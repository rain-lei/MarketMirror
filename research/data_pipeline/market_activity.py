"""Extract provider-observed daily shares and RMB amount with source/version checks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .fetch_baostock import DOCUMENTATION, VERSION as DOWNLOAD_VERSION
from .market_data import VERSION as MARKET_VERSION, numeric, read_rows, strict_date
from .provenance import file_sha256

VERSION = "market-activity-v1"
OUTPUT_FIELDS = ["trade_date", "stock_code", "provider_symbol", "volume_shares", "amount_cny", "trading_status"]
REQUIRED_RAW = {"date", "code", "close", "preclose", "volume", "amount", "adjustflag", "tradestatus", "pctChg"}


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "data_kind", "download_dir", "market_manifest", "evidence_manifest", "stock_symbols"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("activity config has missing or unknown fields")
    if config["data_kind"] != "observed":
        raise ValueError("activity import requires observed data")
    for name in ("run_id", "download_dir", "market_manifest", "evidence_manifest"):
        if not isinstance(config[name], str) or not config[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    symbols = config["stock_symbols"]
    if (not isinstance(symbols, list) or not symbols or any(not isinstance(s, str) or re.fullmatch(r"(sh|sz)\.[0-9]{6}", s) is None for s in symbols)
            or len(set(symbols)) != len(symbols) or len({s.split(".")[1] for s in symbols}) != len(symbols)):
        raise ValueError("stock_symbols must be unique exchange-qualified stock identities")
    return config


def parse_activity_row(row: dict[str, str], symbol: str) -> dict[str, Any]:
    if row["code"] != symbol or row["adjustflag"] != "1" or row["tradestatus"] not in {"0", "1"}:
        raise ValueError("activity symbol, adjustment flag or trading status differs from provider query")
    day = strict_date(row["date"])
    if re.fullmatch(r"[0-9]+", row["volume"]) is None:
        raise ValueError("volume must be a nonnegative integer count of shares")
    volume = int(row["volume"])
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", row["amount"]) is None:
        raise ValueError("amount must be a nonnegative plain decimal RMB value")
    try:
        amount = Decimal(row["amount"])
    except InvalidOperation as error:
        raise ValueError("amount is not a valid decimal") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("amount must be finite and nonnegative")
    if row["tradestatus"] == "1":
        if volume <= 0 or amount <= 0:
            raise ValueError("trading session must have positive volume and amount")
    else:
        if volume != 0 or amount != 0:
            raise ValueError("suspended session must have zero volume and amount")
        close, preclose = numeric(row["close"], "prices", None), numeric(row["preclose"], "prices", None)
        if abs(close / preclose - 1) > 1e-10:
            raise ValueError("suspended session has nonflat reference prices")
    return {"trade_date": day.isoformat(), "stock_code": symbol.split(".")[1], "provider_symbol": symbol,
            "volume_shares": volume, "amount_cny": str(amount),
            "trading_status": "trading" if row["tradestatus"] == "1" else "suspended"}


def import_activity(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config = load_config(config_path)
    download_dir = (config_path.parent / config["download_dir"]).resolve()
    market_manifest_path = (config_path.parent / config["market_manifest"]).resolve()
    evidence_manifest_path = (config_path.parent / config["evidence_manifest"]).resolve()
    download_manifest_path = download_dir / "download_manifest.json"
    download = json.loads(download_manifest_path.read_text(encoding="utf-8"))
    market = json.loads(market_manifest_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_manifest_path.read_text(encoding="utf-8"))
    if (download.get("pipeline_version") != DOWNLOAD_VERSION or download.get("provider") != "BaoStock"
            or download.get("data_kind") != "observed" or download.get("source_symbols") != config["stock_symbols"]):
        raise ValueError("download manifest provider, version or symbols differ from activity config")
    if market.get("pipeline_version") != MARKET_VERSION or market.get("data_kind") != "observed":
        raise ValueError("prepared market version or data kind differs from activity config")
    if set(market["series"]) != {s.split(".")[1] for s in config["stock_symbols"]}:
        raise ValueError("prepared market stock identities differ from activity config")
    docs = [s for s in evidence["sources"] if s["url"] == DOCUMENTATION]
    if len(docs) != 1:
        raise ValueError("one archived primary provider document is required")
    doc_path = (evidence_manifest_path.parent / docs[0]["path"]).resolve()
    if doc_path.parent != evidence_manifest_path.parent:
        raise ValueError("archived provider document escaped evidence directory")
    manifest_paths = [config_path, download_manifest_path, market_manifest_path, evidence_manifest_path, doc_path]
    inputs = {p: file_sha256(p) for p in manifest_paths}
    if inputs[doc_path] != docs[0]["sha256"]:
        raise ValueError("archived provider documentation hash differs from evidence manifest")
    doc = doc_path.read_text(encoding="utf-8")
    if "| volume | 成交量（累计，单位：股）" not in doc or "| amount | 成交额（单位：人民币元）" not in doc:
        raise ValueError("archived provider documentation does not support stated activity units")
    for name, item in download["artifacts"].items():
        path = (download_dir / name).resolve()
        if path.parent != download_dir or file_sha256(path) != item["sha256"]:
            raise ValueError(f"download artifact changed or escaped directory: {name}")
        inputs[path] = item["sha256"]
    if market["config_sha256"] != download["artifacts"]["market_import.json"]["sha256"]:
        raise ValueError("prepared market is not tied to this downloaded provider configuration")
    if (market["settings"]["stock_return_basis"] != "adjusted_price_return"
            or market["settings"]["benchmark_price_basis"] != "price_index"):
        raise ValueError("prepared market return/benchmark basis differs from provider fields")
    for key, name in (("stock", "stock_returns.csv"), ("benchmark", "benchmark_prices.csv"), ("calendar", "calendar.csv")):
        if market["inputs"][key]["sha256"] != download["artifacts"][name]["sha256"]:
            raise ValueError("prepared market input differs from downloaded vintage")
    market_csv = market_manifest_path.parent / "market_daily.csv"
    if file_sha256(market_csv) != market["artifacts"]["market_daily.csv"]["sha256"]:
        raise ValueError("prepared market CSV hash differs from manifest")
    inputs[market_csv.resolve()] = market["artifacts"]["market_daily.csv"]["sha256"]
    session_dates = market["session_dates"]
    if session_dates != sorted(set(session_dates)) or len(session_dates) < 2:
        raise ValueError("prepared session calendar is not ordered and unique")
    for day in session_dates:
        strict_date(day)
    calendar_rows = [row["trade_date"] for _, row in read_rows(download_dir / "calendar.csv", {"trade_date"})]
    if calendar_rows != session_dates:
        raise ValueError("provider calendar differs from prepared market sessions")
    queries = {q["symbol"]: q for q in download["queries"] if q["function"] == "query_history_k_data_plus"}
    if len(queries) != len(download["source_symbols"]) + 1:
        raise ValueError("download history query identities are incomplete or duplicated")
    rows, per_stock = [], {}
    for symbol in config["stock_symbols"]:
        query = queries.get(symbol)
        if (not query or query["adjustflag"] != "1" or query["frequency"] != "d"
                or set(query["fields"].split(",")) != REQUIRED_RAW or query["start_date"] != download["start_date"]
                or query["end_date"] != download["end_date"]):
            raise ValueError(f"stock query does not declare expected daily fields: {symbol}")
        raw_name = query["raw_file"]
        if raw_name != symbol.replace(".", "_") + "_raw.csv":
            raise ValueError("stock raw filename differs from declared provider symbol")
        with (download_dir / raw_name).open(encoding="utf-8-sig", newline="") as handle:
            if next(csv.reader(handle)) != query["fields"].split(","):
                raise ValueError("stock raw column order differs from provider query")
        parsed = []
        for _, raw in read_rows(download_dir / raw_name, REQUIRED_RAW):
            parsed.append(parse_activity_row(raw, symbol))
        dates = [r["trade_date"] for r in parsed]
        if dates != session_dates or query["rows"] != len(parsed):
            raise ValueError("activity stock dates/rows differ from complete trading calendar")
        rows.extend(parsed)
        per_stock[symbol] = {"sessions": len(parsed), "traded_sessions": sum(r["trading_status"] == "trading" for r in parsed),
                             "suspended_sessions": sum(r["trading_status"] == "suspended" for r in parsed),
                             "total_volume_shares": sum(r["volume_shares"] for r in parsed),
                             "total_amount_cny": str(sum(Decimal(r["amount_cny"]) for r in parsed))}
    if any(file_sha256(p) != h for p, h in inputs.items()):
        raise RuntimeError("activity input changed during import")
    if any(output_dir == p or output_dir in p.parents for p in inputs):
        raise ValueError("activity output must not contain an input file")
    if any(output_dir == folder or folder in output_dir.parents for folder in
           (download_dir, market_manifest_path.parent, evidence_manifest_path.parent, config_path.parent)):
        raise ValueError("activity output must be outside input directories")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty activity output directory")
    code_hash = {name: file_sha256(Path(__file__).with_name(name)) for name in
                 ("market_activity.py", "market_data.py", "provenance.py")}
    identity = {"input_sha256": {str(p): h for p, h in inputs.items()}, "code_sha256": code_hash}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        with (staging / "market_activity.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: (r["stock_code"], r["trade_date"])))
        report = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                  "activity_id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest(),
                  "run_id": config["run_id"], "data_kind": "observed", "provider": "BaoStock",
                  "provider_documentation": DOCUMENTATION, "documentation_sha256": inputs[doc_path],
                  "market_dataset_id": market["market_dataset_id"], "session_dates": session_dates,
                  "counts": {"rows": len(rows), "stocks": len(per_stock), "sessions": len(session_dates)},
                  "per_stock": per_stock, "units": {"volume_shares": "share", "amount_cny": "CNY"},
                  "interpretation": "Observed executed daily volume and amount; neither represents order-book depth or executable liquidity for an agent.",
                  "limitations": ["Provider values are a current retrieval vintage, not archived point-in-time exchange records.",
                                  "Backward-adjusted close levels are not used to validate amount/volume; adjusted-price discontinuities are separately quarantined.",
                                  "Volume/amount do not identify investor classes, trade direction, available depth or a price-impact coefficient."],
                  **identity,
                  "artifacts": {"market_activity.csv": {"sha256": file_sha256(staging / "market_activity.csv")}}}
        (staging / "activity_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = import_activity(args.config, args.output_dir)
    print(json.dumps({"activity_id": report["activity_id"], "counts": report["counts"],
                      "per_stock": report["per_stock"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
