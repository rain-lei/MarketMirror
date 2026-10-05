"""Reconstruct ordinary 2019 daily limit prices from unadjusted provider bars."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from ..data_pipeline.fetch_pre_wuhan_price_limit_inputs import (
    END_DATE, FIELDS, START_DATE, prepare_calendar, validate_daily_rows,
)
from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
RAW_MANIFEST = ROOT / "research_outputs/pre_wuhan_price_limit_raw_2019_v1/download_manifest.json"
SOURCE_ARCHIVE = ROOT / "research_outputs/pre_wuhan_common_shock_balanced_2019_v1.json"
OLD_MANIFEST = ROOT / "research_outputs/wuhan_pit_market_download_2020/download_manifest.json"
RULES = ROOT / "research/configs/pre_wuhan_price_limit_rules_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_price_limit_touches_2019_v1.json"
VERSION = "pre-wuhan-rule-reconstructed-price-limit-touches-v1"


def validate_rules(rules: dict) -> None:
    expected = {
        "version": "historical-price-limit-2019-v1",
        "start_date": "2019-11-01", "end_date": "2019-12-31",
        "tick_rmb": "0.01", "rounding": "ROUND_HALF_UP",
        "regular_band_bps": 1000, "non_star_st_band_bps": 500,
        "star_band_bps": 2000, "regular_ipo_exempt_sessions": 1,
        "star_ipo_exempt_sessions": 5,
    }
    if any(type(rules.get(key)) is not type(value) or rules[key] != value
           for key, value in expected.items()):
        raise ValueError("historical 2019 limit rule contract differs")


def ordinary_2019_regime(symbol: str, is_st: str, day: str, ipo: str,
                         calendar: list[str], rules: dict) -> dict:
    if not rules["start_date"] <= day <= rules["end_date"] or is_st not in {"0", "1"}:
        raise ValueError("unsupported date or ST observation for the frozen 2019 rule set")
    if symbol.startswith("sh.688"):
        band = rules["star_band_bps"]
        exempt = rules["star_ipo_exempt_sessions"]
        regime = "STAR_2019"
    elif symbol.startswith(("sh.60", "sz.00", "sz.30")):
        band = rules["non_star_st_band_bps"] if is_st == "1" else rules["regular_band_bps"]
        exempt = rules["regular_ipo_exempt_sessions"]
        regime = "non_STAR_ST_2019" if is_st == "1" else "regular_2019"
    else:
        raise ValueError("unsupported board for the frozen 2019 rule set")
    # For listings before the source calendar, this is a conservative lower
    # bound on listed sessions. A bound above the IPO exemption suffices.
    listed = sum(ipo <= session <= day for session in calendar)
    if listed <= exempt:
        return {"status": "listing_exemption_requires_review", "regime": regime,
                "band_bps": None, "listed_sessions_lower_bound": listed}
    return {"status": "ordinary_rule", "regime": regime, "band_bps": band,
            "listed_sessions_lower_bound": listed}


def classify_bar(record: dict, band_bps: int | None) -> dict:
    """Keep unknown or inconsistent classifications null rather than false."""
    empty = {"upper_touch": None, "lower_touch": None,
             "close_at_upper": None, "close_at_lower": None}
    if band_bps is None:
        return {"qc_status": "unresolved_rule", "limit_lower_rmb": None, "limit_upper_rmb": None, **empty}
    tick = Decimal("0.01")
    preclose = Decimal(record["preclose"])
    if not preclose.is_finite() or preclose <= 0 or type(band_bps) is not int or not 0 < band_bps < 10000:
        raise ValueError("invalid reference price or limit-band setting")
    ratio = Decimal(band_bps) / Decimal(10000)
    lower = (preclose * (1 - ratio)).quantize(tick, rounding=ROUND_HALF_UP)
    upper = (preclose * (1 + ratio)).quantize(tick, rounding=ROUND_HALF_UP)
    prices = {name: Decimal(record[name]) for name in ("open", "high", "low", "close")}
    if (not all(value.is_finite() and value > 0 and value == value.quantize(tick) for value in prices.values())
            or prices["low"] > min(prices["open"], prices["close"])
            or prices["high"] < max(prices["open"], prices["close"])
            or prices["low"] > prices["high"]):
        raise ValueError("invalid unadjusted bar prices or tick precision")
    limits = {"limit_lower_rmb": str(lower), "limit_upper_rmb": str(upper)}
    if record["tradestatus"] == "0":
        if int(record["volume"]) != 0 or any(price != preclose for price in prices.values()):
            raise ValueError("suspended bar is not flat with zero volume")
        return {"qc_status": "suspended", **limits, **empty}
    if record["tradestatus"] != "1":
        raise ValueError("unknown trading status")
    if prices["low"] < lower or prices["high"] > upper:
        return {"qc_status": "bar_outside_reconstructed_band", **limits, **empty}
    return {"qc_status": "ordinary_rule_reconstruction", **limits,
            "upper_touch": prices["high"] == upper, "lower_touch": prices["low"] == lower,
            "close_at_upper": prices["close"] == upper, "close_at_lower": prices["close"] == lower}


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def compute() -> dict:
    raw = json.loads(RAW_MANIFEST.read_text(encoding="utf-8"))
    archive = json.loads(SOURCE_ARCHIVE.read_text(encoding="utf-8"))
    old = json.loads(OLD_MANIFEST.read_text(encoding="utf-8"))
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    validate_rules(rules)
    if (raw.get("pipeline_version") != "pre-wuhan-price-limit-inputs-v1"
            or raw.get("calendar_sessions") != 44 or raw.get("sdk_version") != "0.9.4"
            or raw.get("source_archive_sha256") != file_sha256(SOURCE_ARCHIVE)
            or raw.get("source_download_manifest_sha256") != file_sha256(OLD_MANIFEST)
            or archive.get("result", {}).get("pipeline_version") != "pre-wuhan-common-shock-balanced-sensitivity-v1"
            or raw.get("selected_stock_codes") != archive["result"]["sample"]["selected_stock_codes"]
            or archive.get("input_sha256", {}).get(str(OLD_MANIFEST.resolve())) != file_sha256(OLD_MANIFEST)):
        raise ValueError("raw source, frozen stock sample or limit rule contract differs")
    for name, info in raw["artifacts"].items():
        if file_sha256(RAW_MANIFEST.parent / name) != info["sha256"]:
            raise ValueError("unadjusted raw artifact hash differs")
    calendar_path = OLD_MANIFEST.parent / "calendar_raw.csv"
    if file_sha256(calendar_path) != old["artifacts"]["calendar_raw.csv"]["sha256"]:
        raise ValueError("source listing/calendar context hash differs")
    calendar = sorted(row["calendar_date"] for row in _read_csv(calendar_path) if row["is_trading_day"] == "1")
    supplemental_calendar = _read_csv(RAW_MANIFEST.parent / "calendar_raw.csv")
    sessions = prepare_calendar(["calendar_date", "is_trading_day"],
                                [[row["calendar_date"], row["is_trading_day"]] for row in supplemental_calendar],
                                START_DATE, END_DATE)
    if sessions != [day for day in calendar if START_DATE <= day <= END_DATE]:
        raise ValueError("supplemental calendar differs from the archived market calendar")
    dates = [day for day in calendar if rules["start_date"] <= day <= rules["end_date"]]
    codes = raw["selected_stock_codes"]
    if len(codes) != 123 or len(set(codes)) != 123 or len(dates) != 43:
        raise ValueError("price-limit sample must cover 123 stocks by 43 dates")
    quotes = {row["symbol"]: row for row in old["quote_checks"] if row["symbol"] != old["benchmark_symbol"]}
    baseline = next(row for row in archive["result"]["variants"] if row["name"] == "no_shock")
    observed = {(row["stock_code"], row["trade_date"]): row["observed_return"] for row in baseline["daily_asset_rows"]}
    if (len(observed) != 5289 or len(baseline["daily_asset_rows"]) != 5289
            or set(observed) != {(code, day) for code in codes for day in dates}):
        raise ValueError("archived observed returns do not cover the exact company-day panel")
    rows, seen = [], set()
    max_return_difference = 0.0
    for query in raw["queries"]:
        if query["function"] != "query_history_k_data_plus":
            continue
        symbol = query["symbol"]
        if (query.get("adjustflag") != "3" or query["rows"] != 44 or symbol not in quotes
                or query.get("fields") != ",".join(FIELDS)
                or query.get("start_date") != START_DATE or query.get("end_date") != END_DATE
                or query.get("frequency") != "d" or query["raw_file"] not in raw["artifacts"]):
            raise ValueError("raw query contract or security identity differs")
        file_rows = _read_csv(RAW_MANIFEST.parent / query["raw_file"])
        file_rows = validate_daily_rows(list(FIELDS), [[row[field] for field in FIELDS] for row in file_rows],
                                       symbol, sessions)
        for record in file_rows:
            if record["date"] not in dates:
                continue
            if record["code"] != symbol or record["adjustflag"] != "3" or record["isST"] not in {"0", "1"}:
                raise ValueError("raw daily identity or adjustment/status differs")
            key = (symbol.split(".")[1], record["date"])
            if key in seen:
                raise ValueError("duplicate stock-date price-limit row")
            seen.add(key)
            regime = ordinary_2019_regime(symbol, record["isST"], record["date"],
                                          quotes[symbol]["ipo_date"], calendar, rules)
            classification = classify_bar(record, regime["band_bps"])
            daily_return = Decimal(record["close"]) / Decimal(record["preclose"]) - 1
            if abs(float(daily_return) - float(record["pctChg"]) / 100) > 1e-6:
                raise ValueError("unadjusted close/reference return does not match provider pctChg")
            max_return_difference = max(max_return_difference, abs(float(daily_return) - observed[key]))
            rows.append({"stock_code": key[0], "trade_date": key[1], "symbol": symbol,
                         "isST": record["isST"], "ipo_date": quotes[symbol]["ipo_date"],
                         "open": record["open"], "high": record["high"], "low": record["low"],
                         "close": record["close"], "preclose": record["preclose"],
                         "tradestatus": record["tradestatus"], "volume": record["volume"],
                         "unadjusted_close_reference_return": float(daily_return),
                         **regime, **classification})
    if seen != {(code, day) for code in codes for day in dates}:
        raise ValueError("raw price-limit rows do not cover the full paired company-day panel")
    usable = [row for row in rows if row["qc_status"] == "ordinary_rule_reconstruction"]
    qc = [row for row in rows if row["qc_status"] not in {"ordinary_rule_reconstruction", "suspended"}]
    flags = ("upper_touch", "lower_touch", "close_at_upper", "close_at_lower")
    closing = [row for row in usable if row["close_at_upper"] or row["close_at_lower"]]
    return {
        "pipeline_version": VERSION,
        "sample": {"companies": len(codes), "sessions": len(dates), "company_days": len(rows),
                   "start_date": dates[0], "end_date": dates[-1]},
        "summary": {"ordinary_rule_company_days": len(usable), "suspended_company_days": len(rows) - len(usable) - len(qc),
                    "qc_company_days": len(qc), "provider_st_company_days": sum(row["isST"] == "1" for row in rows),
                    "rule_band_company_days": dict(Counter(str(row["band_bps"]) for row in rows)),
                    **{field: sum(row[field] for row in usable) for field in flags},
                    "touch_companies": len({row["stock_code"] for row in usable if row["upper_touch"] or row["lower_touch"]}),
                    "close_at_limit_companies": len({row["stock_code"] for row in closing}),
                    "closing_limit_days_below_10pct_absolute_return": sum(abs(row["unadjusted_close_reference_return"]) < 0.10 for row in closing),
                    "max_unadjusted_vs_archived_adjusted_return_difference": max_return_difference},
        "source_sha256": {str(path.resolve()): file_sha256(path) for path in (RAW_MANIFEST, SOURCE_ARCHIVE, OLD_MANIFEST, calendar_path, RULES)},
        "interpretation": "Reconstructed ordinary historical rule states from current-vintage provider daily bars, not certified exchange daily limit files or order-book queues. Targets are post-simulation observations, not Agent inputs.",
        "qc_rows": qc,
        "daily_rows": sorted(rows, key=lambda row: (row["stock_code"], row["trade_date"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    payload = {"result": result, "code_sha256": file_sha256(Path(__file__))}
    destination = args.output.resolve()
    if destination.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("price-limit audit output must be written under research_outputs")
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != payload:
            raise ValueError("archived rule-reconstructed price-limit audit differs")
    else:
        if destination.exists():
            raise ValueError("choose a new price-limit audit output path")
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    if result["summary"]["qc_company_days"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
