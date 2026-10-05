"""Download bounded BaoStock observations with raw responses and declared basis.

Requires optional baostock SDK. No user account or token is used. Downloaded
prices are provider observations, not independently verified exchange records.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import importlib.metadata
import json
import re
import socket
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .market_data import numeric, strict_date
from .provenance import file_sha256

DOCUMENTATION = "https://www.baostock.com/mainContent?file=pythonAPI.md"
VERSION = "baostock-fetch-v2"
STOCK_FIELDS = "date,code,close,preclose,volume,amount,adjustflag,tradestatus,pctChg"
INDEX_FIELDS = "date,code,close,preclose,volume,amount,pctChg"
SECURITY_FIELDS = {"code", "code_name", "ipoDate", "outDate", "type", "status"}


def consume(result: Any) -> tuple[list[str], list[list[str]]]:
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock query failed: {result.error_code}: {result.error_msg}")
    rows = []
    fields = list(result.fields)
    while result.next():
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock pagination failed: {result.error_code}: {result.error_msg}")
        row = result.get_row_data()
        if len(row) != len(fields):
            raise ValueError("provider row width differs from declared fields")
        rows.append(list(row))
    if result.error_code != "0":
        raise RuntimeError(f"BaoStock query ended with error: {result.error_code}: {result.error_msg}")
    if not rows:
        raise ValueError("provider returned no rows")
    return fields, rows


def prepare_calendar(fields: list[str], rows: list[list[str]], start: date, end: date) -> list[date]:
    if not {"calendar_date", "is_trading_day"} <= set(fields):
        raise ValueError("calendar response is missing expected fields")
    observations = {}
    for row in rows:
        value = dict(zip(fields, row))
        day = strict_date(value["calendar_date"])
        if day in observations or value["is_trading_day"] not in {"0", "1"}:
            raise ValueError("calendar response has duplicate dates or invalid trading flags")
        observations[day] = value["is_trading_day"]
    expected = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    if set(observations) != expected:
        raise ValueError("provider calendar does not cover every requested calendar day")
    sessions = sorted(day for day, flag in observations.items() if flag == "1")
    if len(sessions) < 2:
        raise ValueError("at least two open sessions are required")
    return sessions


def security_windows(fields: list[str], rows: list[list[str]], symbols: list[str],
                     start: date, end: date) -> dict[str, dict[str, str | None]]:
    """Bound each history request to the provider's declared listing interval."""
    if not SECURITY_FIELDS <= set(fields):
        raise ValueError("security catalog is missing listing interval fields")
    wanted = set(symbols)
    catalog = {}
    for row in rows:
        record = dict(zip(fields, row))
        code = record["code"]
        if code in wanted:
            if code in catalog:
                raise ValueError(f"security catalog has a duplicate symbol for {code}")
            catalog[code] = record
    result = {}
    for symbol in symbols:
        record = catalog.get(symbol)
        if record is None or record["type"] != "1":
            raise ValueError(f"security catalog has no stock record for {symbol}")
        try:
            ipo_date = strict_date(record["ipoDate"])
        except ValueError as exc:
            raise ValueError(f"security catalog has no valid IPO date for {symbol}") from exc
        out_date = None
        raw_out_date = record.get("outDate")
        if raw_out_date and raw_out_date != "0000-00-00":
            try:
                out_date = strict_date(raw_out_date)
            except ValueError as exc:
                raise ValueError(f"security catalog has an invalid delisting date for {symbol}") from exc
            if out_date < ipo_date:
                raise ValueError(f"security catalog listing interval is reversed for {symbol}")
        first = max(start, ipo_date)
        last = min(end, out_date) if out_date is not None else end
        if first > last:
            raise ValueError(f"security {symbol} has no listed sessions in the requested period")
        result[symbol] = {"ipo_date": ipo_date.isoformat(),
                          "out_date": out_date.isoformat() if out_date else None,
                          "query_start_date": first.isoformat(), "query_end_date": last.isoformat(),
                          "catalog_status": record["status"]}
    return result


def prepare_series(fields: list[str], rows: list[list[str]], symbol: str,
                   sessions: list[date], stock: bool) -> tuple[list[list[Any]], dict[str, Any]]:
    required = {"date", "code", "close", "preclose", "pctChg"} | ({"adjustflag", "tradestatus"} if stock else set())
    if not required <= set(fields):
        raise ValueError("price response is missing expected fields")
    observed = {}
    suspended, max_difference, same_day_max = [], 0.0, 0.0
    tolerance = 1e-5
    for row in rows:
        record = dict(zip(fields, row))
        day = strict_date(record["date"])
        if record["code"] != symbol or day in observed:
            raise ValueError("provider price identity/date mismatch")
        if stock and record["adjustflag"] != "1":
            raise ValueError("stock response must use requested backward adjustment flag 1")
        if stock and record["tradestatus"] not in {"0", "1"}:
            raise ValueError("unexpected stock trading status")
        close = numeric(record["close"], "prices", None)
        preclose = numeric(record["preclose"], "prices", None)
        pct = numeric(record["pctChg"], "returns", "percent")
        same_day_max = max(same_day_max, abs(close / preclose - 1 - pct))
        if stock and record["tradestatus"] == "0":
            suspended.append(day.isoformat())
            if abs(close / preclose - 1) > 1e-10 or float(record.get("volume", "nan")) != 0:
                raise ValueError("suspended provider observation disagrees with flat-price/zero-volume documentation")
        observed[day] = {"close": close, "pct": pct, "pct_percent_raw": record["pctChg"]}
    if set(observed) != set(sessions):
        raise ValueError("provider prices do not cover the requested open-session calendar exactly")
    anomalies = []
    for previous, current in zip(sessions, sessions[1:]):
        difference = abs(observed[current]["close"] / observed[previous]["close"] - 1 - observed[current]["pct"])
        max_difference = max(max_difference, difference)
        if difference > tolerance:
            anomalies.append({"previous_date": previous.isoformat(), "trade_date": current.isoformat(),
                              "adjacent_close_return": observed[current]["close"] / observed[previous]["close"] - 1,
                              "provider_daily_return": observed[current]["pct"], "absolute_difference": difference})
    if same_day_max > tolerance:
        raise ValueError("provider daily pctChg differs from same-day close/preclose reference ratio")
    if not stock and anomalies:
        raise ValueError("benchmark prices are inconsistent with provider daily returns")
    identifier = symbol.split(".")[1] if stock else symbol
    # A daily return is a different provider field, not a repaired price level.
    # Stock price continuity is separately audited and never asserted to pass.
    values = [[d.isoformat(), identifier, observed[d]["pct_percent_raw"] if stock else observed[d]["close"]] for d in sessions]
    return values, {"symbol": symbol, "rows": len(rows), "suspended_sessions": suspended,
                    "same_day_return_gate": "passed", "same_day_max_difference": same_day_max,
                    "adjacent_price_gate": "failed" if anomalies else "passed", "adjacent_price_anomalies": anomalies,
                    "max_adjacent_price_difference": max_difference, "return_comparison_tolerance": tolerance,
                    "output_method": "provider_pctChg_percent" if stock else "unadjusted_index_close"}


def fetch_baostock(symbols: list[str], benchmark: str, start_date: str, end_date: str,
                   output_dir: Path, sdk: Any = None) -> dict[str, Any]:
    start, end = strict_date(start_date), strict_date(end_date)
    if start > end or end >= datetime.now(timezone(timedelta(hours=8))).date():
        raise ValueError("date range must be ordered and end before the current local day")
    if not symbols or len(set(symbols)) != len(symbols) or any(re.fullmatch(r"(?:sh|sz)\.[0-9]{6}", s) is None for s in symbols):
        raise ValueError("provide unique exchange-qualified sh./sz. stock symbols")
    if re.fullmatch(r"(?:sh|sz)\.[0-9]{6}", benchmark) is None or benchmark in symbols:
        raise ValueError("benchmark must be a distinct exchange-qualified index symbol")
    if len({s.split('.')[1] for s in symbols}) != len(symbols):
        raise ValueError("stock symbols would collide after stock-code normalization")
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("download requires a fresh empty directory to preserve prior observations")
    if sdk is None:
        sdk = importlib.import_module("baostock")
        sdk_version = importlib.metadata.version("baostock")
    else:
        sdk_version = "injected-test-sdk"
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(20)
    logged_in = False
    queries = []
    try:
        login = sdk.login()
        if login.error_code != "0":
            raise RuntimeError(f"BaoStock anonymous login failed: {login.error_code}: {login.error_msg}")
        logged_in = True
        calendar_result = sdk.query_trade_dates(start_date=start_date, end_date=end_date)
        calendar_fields, calendar_rows = consume(calendar_result)
        sessions = prepare_calendar(calendar_fields, calendar_rows, start, end)
        security_fields, security_rows = consume(sdk.query_stock_basic())
        windows = security_windows(security_fields, security_rows, symbols, start, end)
        raw_responses = {"calendar_raw.csv": (calendar_fields, calendar_rows),
                         "security_basic_raw.csv": (security_fields, security_rows)}
        queries.append({"function": "query_trade_dates", "start_date": start_date, "end_date": end_date,
                        "rows": len(calendar_rows), "raw_file": "calendar_raw.csv"})
        queries.append({"function": "query_stock_basic", "symbols": symbols,
                        "rows": len(security_rows), "raw_file": "security_basic_raw.csv"})
        stocks, summaries = [], []
        for symbol in symbols + [benchmark]:
            is_stock = symbol != benchmark
            fields = STOCK_FIELDS if is_stock else INDEX_FIELDS
            flag = "1" if is_stock else "3"
            window = windows[symbol] if is_stock else {"query_start_date": start_date,
                                                        "query_end_date": end_date}
            response = sdk.query_history_k_data_plus(symbol, fields,
                                                       start_date=window["query_start_date"],
                                                       end_date=window["query_end_date"],
                                                       frequency="d", adjustflag=flag)
            response_fields, rows = consume(response)
            series_sessions = ([day for day in sessions
                                if window["query_start_date"] <= day.isoformat() <= window["query_end_date"]]
                               if is_stock else sessions)
            values, info = prepare_series(response_fields, rows, symbol, series_sessions, is_stock)
            if is_stock:
                info.update(window)
            raw_name = symbol.replace(".", "_") + "_raw.csv"
            raw_responses[raw_name] = (response_fields, rows)
            queries.append({"function": "query_history_k_data_plus", "symbol": symbol, "fields": fields,
                            "start_date": window["query_start_date"], "end_date": window["query_end_date"],
                            "frequency": "d", "adjustflag": flag,
                            "rows": len(rows), "raw_file": raw_name})
            summaries.append(info)
            if is_stock:
                stocks.extend(values)
            else:
                benchmark_values = values
        settings = {
            "stock_input": "stock_returns.csv", "benchmark_input": "benchmark_prices.csv", "calendar_input": "calendar.csv",
            "benchmark_id": benchmark, "stock_format": "returns", "benchmark_format": "prices",
            "stock_value_column": "stock_return_pct", "benchmark_value_column": "close",
            "stock_return_unit": "percent", "stock_return_basis": "adjusted_price_return", "benchmark_price_basis": "price_index",
            "stock_data_source": f"BaoStock SDK {sdk_version}; pctChg of flag=1 query, checked against same-day close/preclose; {DOCUMENTATION}; see download_manifest.json",
            "benchmark_data_source": f"BaoStock SDK {sdk_version}; index flag=3; {DOCUMENTATION}; see download_manifest.json",
            "calendar_data_source": f"BaoStock query_trade_dates open-day flags; {DOCUMENTATION}; see download_manifest.json",
            "data_kind": "observed", "return_type": "simple",
        }
        manifest = {
            "pipeline_version": VERSION, "provider": "BaoStock", "sdk_version": sdk_version,
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "data_kind": "observed", "documentation": DOCUMENTATION,
            "code_sha256": file_sha256(Path(__file__)), "queries": queries, "quote_checks": summaries,
            "source_symbols": symbols, "benchmark_symbol": benchmark, "start_date": start_date, "end_date": end_date,
            "assumptions": [
                "Backward-adjusted prices use BaoStock's documented return-based corporate-action algorithm; not a claim of a universal total-return index.",
                "Stocks use provider daily pctChg checked against same-day close/preclose, not returns computed from adjacent adjusted price levels.",
                "Adjacent adjusted-price discontinuities remain recorded; these levels are quarantined in raw files, never repaired or used as coherent price inputs.",
                "Data is a current provider vintage and may include historical corrections; it is not an archived point-in-time feed.",
                "Per-stock history starts at the provider-declared IPO date and ends at its declared delisting date when present; catalog status is recorded but not used to select the sample.",
                "Stock suspensions are retained only if provider status, flat close/preclose and zero volume agree; no local filling is done.",
                "The trading calendar is a separate provider query, not inferred from observed stock or benchmark rows.",
                "Sample symbols are explicitly selected for a pilot; no representativeness or forecasting claim.",
            ],
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
            staging = Path(tmp)
            files = {**raw_responses,
                     "stock_returns.csv": (["trade_date", "stock_code", "stock_return_pct"], stocks),
                     "benchmark_prices.csv": (["trade_date", "benchmark_id", "close"], benchmark_values),
                     "calendar.csv": (["trade_date"], [[d.isoformat()] for d in sessions])}
            for name, (columns, rows) in files.items():
                with (staging / name).open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(columns)
                    writer.writerows(rows)
            (staging / "market_import.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest["artifacts"] = {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}
            (staging / "download_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            for path in staging.iterdir():
                path.replace(output_dir / path.name)
        return manifest
    finally:
        try:
            if logged_in:
                sdk.logout()
        finally:
            socket.setdefaulttimeout(previous_timeout)


def main():
    parser = argparse.ArgumentParser(description="Fetch read-only public BaoStock market observations")
    parser.add_argument("--stocks", nargs="+", required=True)
    parser.add_argument("--benchmark", default="sh.000300")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dependency-dir", type=Path)
    args = parser.parse_args()
    if args.dependency_dir:
        import sys
        sys.path.insert(0, str(args.dependency_dir.resolve()))
    manifest = fetch_baostock(args.stocks, args.benchmark, args.start_date, args.end_date, args.output_dir)
    print(json.dumps({"provider": manifest["provider"], "queries": len(manifest["queries"]),
                      "stock_symbols": args.stocks, "data_kind": manifest["data_kind"]}))


if __name__ == "__main__":
    main()
