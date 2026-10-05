"""Archive unadjusted daily bars needed for a 2019 price-limit audit.

The selected securities are read from the frozen balanced 2019 scenario archive.
This supplemental download does not alter the adjusted-return market baseline.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import importlib.metadata
import json
import math
import socket
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .market_data import strict_date
from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ARCHIVE = ROOT / "research_outputs/pre_wuhan_common_shock_balanced_2019_v1.json"
SOURCE_DOWNLOAD_MANIFEST = ROOT / "research_outputs/wuhan_pit_market_download_2020/download_manifest.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_price_limit_raw_2019_v1"
START_DATE = "2019-10-31"
END_DATE = "2019-12-31"
FIELDS = ("date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
          "adjustflag", "tradestatus", "pctChg", "isST")
VERSION = "pre-wuhan-price-limit-inputs-v1"


def consume(result: Any) -> tuple[list[str], list[list[str]]]:
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock request failed: {result.error_code}: {result.error_msg}")
    fields = list(result.fields)
    rows = []
    while result.next():
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock pagination failed: {result.error_code}: {result.error_msg}")
        row = result.get_row_data()
        if len(row) != len(fields):
            raise ValueError("BaoStock row width differs from its field list")
        rows.append(list(row))
    if result.error_code != "0" or not rows:
        raise ValueError("BaoStock returned no complete rows")
    return fields, rows


def prepare_calendar(fields: list[str], rows: list[list[str]], start_date: str,
                     end_date: str) -> list[str]:
    if not {"calendar_date", "is_trading_day"} <= set(fields):
        raise ValueError("provider calendar lacks expected fields")
    start, end = strict_date(start_date), strict_date(end_date)
    expected = {(start + timedelta(days=offset)).isoformat()
                for offset in range((end - start).days + 1)}
    observed = {}
    for row in rows:
        record = dict(zip(fields, row, strict=True))
        day = strict_date(record["calendar_date"]).isoformat()
        if day in observed or record["is_trading_day"] not in {"0", "1"}:
            raise ValueError("provider calendar has duplicate dates or invalid flags")
        observed[day] = record["is_trading_day"]
    if set(observed) != expected:
        raise ValueError("provider calendar does not cover the full requested range")
    sessions = sorted(day for day, flag in observed.items() if flag == "1")
    if len(sessions) != 44 or sessions[0] != START_DATE or sessions[-1] != END_DATE:
        raise ValueError("calendar does not match the frozen 44-session window")
    return sessions


def validate_daily_rows(fields: list[str], rows: list[list[str]], symbol: str,
                        sessions: list[str]) -> list[dict[str, str]]:
    if tuple(fields) != FIELDS:
        raise ValueError("unadjusted response fields differ from the frozen request")
    by_day = {}
    for row in rows:
        record = dict(zip(fields, row, strict=True))
        day = strict_date(record["date"]).isoformat()
        if record["code"] != symbol or day in by_day or day not in sessions:
            raise ValueError("provider daily row has wrong identity or duplicate/unexpected date")
        if record["adjustflag"] != "3" or record["tradestatus"] not in {"0", "1"} or record["isST"] not in {"0", "1"}:
            raise ValueError("provider row is not unadjusted or has invalid trading/ST status")
        prices = {name: float(record[name]) for name in ("open", "high", "low", "close", "preclose")}
        amount, pct = float(record["amount"]), float(record["pctChg"])
        volume = int(record["volume"])
        if (not all(math.isfinite(value) and value > 0 for value in prices.values())
                or not math.isfinite(amount) or amount < 0 or volume < 0 or not math.isfinite(pct)
                or prices["low"] > min(prices["open"], prices["close"])
                or prices["high"] < max(prices["open"], prices["close"])
                or prices["low"] > prices["high"]
                or abs((prices["close"] / prices["preclose"] - 1) * 100 - pct) > 0.0001):
            raise ValueError("unadjusted daily OHLC or pctChg consistency check failed")
        if record["tradestatus"] == "0" and (volume != 0 or abs(pct) > 0.0001):
            raise ValueError("suspended row unexpectedly reports volume or return")
        by_day[day] = record
    if set(by_day) != set(sessions):
        raise ValueError("unadjusted daily bars do not cover each open session")
    return [by_day[day] for day in sessions]


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]] | list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for row in rows:
            writer.writerow([row[name] for name in fields] if isinstance(row, dict) else row)


def fetch_pre_wuhan_price_limit_inputs(output_dir: Path = OUTPUT, sdk: Any = None) -> dict:
    output_dir = output_dir.resolve()
    if output_dir.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("price-limit raw data must be saved under research_outputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output directory must be fresh to preserve prior provider responses")
    source = json.loads(SOURCE_ARCHIVE.read_text(encoding="utf-8"))
    download = json.loads(SOURCE_DOWNLOAD_MANIFEST.read_text(encoding="utf-8"))
    codes = source.get("result", {}).get("sample", {}).get("selected_stock_codes")
    if (not isinstance(codes, list) or len(codes) != 123 or len(set(codes)) != 123
            or source.get("result", {}).get("pipeline_version") != "pre-wuhan-common-shock-balanced-sensitivity-v1"
            or source.get("input_sha256", {}).get(str(SOURCE_DOWNLOAD_MANIFEST.resolve()))
            != file_sha256(SOURCE_DOWNLOAD_MANIFEST)):
        raise ValueError("frozen source archive lacks its exact 123-stock sample")
    symbol_map = {row["symbol"].split(".")[1]: row["symbol"] for row in download.get("quote_checks", [])
                  if row.get("symbol", "") != download.get("benchmark_symbol")}
    if any(code not in symbol_map for code in codes):
        raise ValueError("source download manifest cannot identify every selected exchange symbol")
    if sdk is None:
        sdk = importlib.import_module("baostock")
        sdk_version = importlib.metadata.version("baostock")
    else:
        sdk_version = "injected-test-sdk"

    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(20)
    logged_in = False
    try:
        login = sdk.login()
        if login.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {login.error_code}: {login.error_msg}")
        logged_in = True
        calendar_fields, calendar_rows = consume(sdk.query_trade_dates(start_date=START_DATE, end_date=END_DATE))
        sessions = prepare_calendar(calendar_fields, calendar_rows, START_DATE, END_DATE)
        responses = {"calendar_raw.csv": (calendar_fields, calendar_rows)}
        queries = [{"function": "query_trade_dates", "start_date": START_DATE, "end_date": END_DATE,
                    "rows": len(calendar_rows), "raw_file": "calendar_raw.csv"}]
        for index, code in enumerate(codes, start=1):
            symbol = symbol_map[code]
            result = sdk.query_history_k_data_plus(symbol, ",".join(FIELDS), start_date=START_DATE,
                                                   end_date=END_DATE, frequency="d", adjustflag="3")
            fields, raw_rows = consume(result)
            rows = validate_daily_rows(fields, raw_rows, symbol, sessions)
            filename = symbol.replace(".", "_") + "_unadjusted_raw.csv"
            responses[filename] = (fields, rows)
            queries.append({"function": "query_history_k_data_plus", "symbol": symbol,
                            "fields": ",".join(FIELDS), "start_date": START_DATE, "end_date": END_DATE,
                            "frequency": "d", "adjustflag": "3", "rows": len(rows), "raw_file": filename})
            if index % 20 == 0 or index == len(codes):
                print(f"Validated unadjusted bars for {index}/{len(codes)} companies")
    finally:
        try:
            if logged_in:
                sdk.logout()
        finally:
            socket.setdefaulttimeout(previous_timeout)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir.parent) as temp_name:
        stage = Path(temp_name)
        artifacts = {}
        for filename, (fields, rows) in responses.items():
            path = stage / filename
            _write_csv(path, fields, rows)
            artifacts[filename] = {"sha256": file_sha256(path), "rows": len(rows), "fields": fields}
        manifest = {
            "pipeline_version": VERSION, "provider": "BaoStock", "sdk_version": sdk_version,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "data_kind": "observed",
            "purpose": "unadjusted daily OHLC plus status fields for the frozen 2019 price-limit diagnostic",
            "start_date": START_DATE, "end_date": END_DATE, "calendar_sessions": len(sessions),
            "selected_stock_codes": codes, "source_archive": str(SOURCE_ARCHIVE.resolve()),
            "source_archive_sha256": file_sha256(SOURCE_ARCHIVE),
            "source_download_manifest": str(SOURCE_DOWNLOAD_MANIFEST.resolve()),
            "source_download_manifest_sha256": file_sha256(SOURCE_DOWNLOAD_MANIFEST),
            "code_sha256": file_sha256(Path(__file__)), "queries": queries, "artifacts": artifacts,
            "assumptions": [
                "Historical OHLC, isST and trade status are provider observations and are not independently certified exchange records.",
                "No adjusted price/return, inferred ST status, or later company outcome is used to derive these fields.",
                "The first requested session is the execution-reference date preceding the 2019-11-01 development sample.",
                "Raw response files are preserved exactly by returned fields and row values; this supplemental source does not replace the existing adjusted-return dataset.",
            ],
        }
        manifest_path = stage / "download_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if output_dir.exists():
            output_dir.rmdir()
        stage.replace(output_dir)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    manifest = fetch_pre_wuhan_price_limit_inputs(args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "stocks": len(manifest["selected_stock_codes"]),
                      "sessions": manifest["calendar_sessions"], "artifact_count": len(manifest["artifacts"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
