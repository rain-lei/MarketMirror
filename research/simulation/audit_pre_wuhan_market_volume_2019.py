"""Compare 2019 simulated matched shares with provider-observed daily volume.

This is a scale diagnostic for a small synthetic venue, not an order-flow or
participant-level calibration. The provider raw files are already archived.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path

from ..data_pipeline.market_activity import DOCUMENTATION, parse_activity_row
from ..data_pipeline.provenance import file_sha256
from . import screen_pre_wuhan_market_2019 as screen


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "research_outputs/pre_wuhan_order_arrival_sensitivity_2019_v1.json"
DOWNLOAD_DIR = ROOT / "research_outputs/wuhan_pit_market_download_2020"
DOWNLOAD_MANIFEST = DOWNLOAD_DIR / "download_manifest.json"
MARKET_MANIFEST = ROOT / "research_outputs/wuhan_pit_market_prepared_2020_v2/market_manifest.json"
EVIDENCE_MANIFEST = ROOT / "research_outputs/observed_2020/evidence/evidence_manifest.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_market_volume_audit_2019_v2.json"
EXPECTED_PIPELINE = "pre-wuhan-order-arrival-sensitivity-v1"
RAW_FIELDS = {"date", "code", "close", "preclose", "volume", "amount",
              "adjustflag", "tradestatus", "pctChg"}


def _nearest_rank(values: list[float], fraction: float) -> float:
    if not values or not 0 < fraction <= 1:
        raise ValueError("invalid quantile input")
    return sorted(values)[math.ceil(fraction * len(values)) - 1]


def compare_volume(rows: list[dict], observed: dict[tuple[str, str], int]) -> dict:
    """Summarize exact company-day pairs, retaining suspended/zero-volume days."""
    keys = [(row["stock_code"], row["execution_reference_date"]) for row in rows]
    if len(set(keys)) != len(keys) or set(keys) != set(observed):
        raise ValueError("simulated and observed company-day panels differ")
    simulated = []
    actual = []
    for row, key in zip(rows, keys):
        volume = row["matched_volume"]
        reference = observed[key]
        if (type(volume) is not int or type(reference) is not int
                or volume < 0 or reference < 0):
            raise ValueError("matched and observed shares must be nonnegative integers")
        simulated.append(volume)
        actual.append(reference)
    if not any(actual):
        raise ValueError("observed panel has no trading volume")
    ratios = [sim / obs for sim, obs in zip(simulated, actual) if obs > 0]
    total_simulated, total_observed = sum(simulated), sum(actual)
    return {"company_days": len(rows), "observed_positive_days": sum(v > 0 for v in actual),
            "observed_zero_days": sum(v == 0 for v in actual),
            "simulated_zero_on_observed_positive_days": sum(s == 0 and o > 0
                                                             for s, o in zip(simulated, actual)),
            "simulated_positive_on_observed_zero_days": sum(s > 0 and o == 0
                                                             for s, o in zip(simulated, actual)),
            "simulated_matched_shares": total_simulated,
            "observed_traded_shares": total_observed,
            "pooled_simulated_to_observed_ratio": total_simulated / total_observed,
            "median_daily_simulated_shares": _nearest_rank(simulated, 0.5),
            "median_daily_observed_shares": _nearest_rank(actual, 0.5),
            "median_daily_ratio_on_observed_positive": _nearest_rank(ratios, 0.5),
            "p95_daily_ratio_on_observed_positive": _nearest_rank(ratios, 0.95)}


def align_execution(rows: list[dict], calendar: list[str]) -> list[dict]:
    """Recover the actual auction reference session from the archived calendar."""
    if calendar != sorted(set(calendar)):
        raise ValueError("market calendar is not ordered and unique")
    calendar_position = {day: index for index, day in enumerate(calendar)}
    aligned = []
    for row in rows:
        index = calendar_position.get(row["trade_date"], -1)
        if (index < 2 or row["signal_cutoff_date"] != calendar[index - 2]
                or not calendar[index - 2] < calendar[index - 1] < row["trade_date"]):
            raise ValueError("simulation information dates differ from market calendar")
        aligned.append({**row, "execution_reference_date": calendar[index - 1]})
    return aligned


def _observed_panel(codes: list[str], dates: list[str], download: dict) -> dict[tuple[str, str], int]:
    if (download.get("provider") != "BaoStock" or download.get("data_kind") != "observed"
            or download.get("documentation") != DOCUMENTATION
            or download.get("start_date") > dates[0]
            or download.get("end_date") < dates[-1]
            or len(set(download["source_symbols"])) != len(download["source_symbols"])):
        raise ValueError("download identity or coverage differs")
    queries = [query for query in download["queries"]
               if query.get("function") == "query_history_k_data_plus"
               and query.get("symbol") != download["benchmark_symbol"]]
    if len(queries) != len(download["source_symbols"]):
        raise ValueError("stock history queries are incomplete")
    by_code = {query["symbol"].split(".")[1]: query for query in queries}
    if len(by_code) != len(queries) or not set(codes) <= set(by_code):
        raise ValueError("sample identities are absent or duplicated in raw queries")
    expected_dates = set(dates)
    observed = {}
    for code in codes:
        query = by_code[code]
        symbol = query["symbol"]
        raw_name = query["raw_file"]
        if (re.fullmatch(r"(sh|sz)_[0-9]{6}_raw\.csv", raw_name) is None
                or raw_name != symbol.replace(".", "_") + "_raw.csv"
                or set(query["fields"].split(",")) != RAW_FIELDS
                or query["adjustflag"] != "1" or query["frequency"] != "d"
                or not download["start_date"] <= query["start_date"] <= dates[0]
                or not dates[-1] <= query["end_date"] <= download["end_date"]):
            raise ValueError("raw query fields or symbol differ")
        raw_path = DOWNLOAD_DIR / raw_name
        if file_sha256(raw_path) != download["artifacts"][raw_name]["sha256"]:
            raise ValueError("provider raw file differs from download manifest")
        stock_dates = set()
        with raw_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if set(reader.fieldnames or []) != RAW_FIELDS:
                raise ValueError("provider raw CSV fields differ")
            for raw in reader:
                if raw["date"] not in expected_dates:
                    continue
                item = parse_activity_row(raw, symbol)
                day = item["trade_date"]
                if day in stock_dates:
                    raise ValueError("provider raw day is duplicated")
                stock_dates.add(day)
                observed[(code, day)] = item["volume_shares"]
        if stock_dates != expected_dates:
            raise ValueError("provider raw file lacks a development session")
    return observed


def compute() -> dict:
    policy = json.loads(screen.POLICY.read_text(encoding="utf-8"))
    screen._validate_policy(policy)
    result, provenance = screen._validated_archive(ARCHIVE, EXPECTED_PIPELINE, policy)
    evidence = json.loads(EVIDENCE_MANIFEST.read_text(encoding="utf-8"))
    docs = [item for item in evidence["sources"] if item["url"] == DOCUMENTATION]
    if len(docs) != 1:
        raise ValueError("provider volume unit documentation is absent or ambiguous")
    doc_path = (EVIDENCE_MANIFEST.parent / docs[0]["path"]).resolve()
    if (doc_path.parent != EVIDENCE_MANIFEST.parent
            or file_sha256(doc_path) != docs[0]["sha256"]):
        raise ValueError("provider documentation provenance differs")
    doc = doc_path.read_text(encoding="utf-8")
    if "| volume | 成交量（累计，单位：股）" not in doc:
        raise ValueError("provider documentation does not establish share units")
    codes = provenance["stock_codes"]
    if len(codes) != 123 or len(result["variants"]) != 3:
        raise ValueError("2019 sample or variant count differs")
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    calendar = market["session_dates"]
    if (market["market_dataset_id"] != result["market_dataset_id"]
            or calendar != sorted(set(calendar))):
        raise ValueError("market calendar or dataset differs from simulation archive")
    first_rows = result["variants"][0]["daily_asset_rows"]
    first_aligned = align_execution(first_rows, calendar)
    trade_dates = sorted({row["trade_date"] for row in first_rows})
    reference_dates = sorted({row["execution_reference_date"] for row in first_aligned})
    if (len(trade_dates) != policy["sessions"]
            or len(reference_dates) != policy["sessions"]
            or {(code, day) for code in codes for day in trade_dates}
            != {(row["stock_code"], row["trade_date"]) for row in first_rows}
            or {(code, day) for code in codes for day in reference_dates}
            != {(row["stock_code"], row["execution_reference_date"]) for row in first_aligned}
            or any(not row["signal_cutoff_date"] < row["execution_reference_date"]
                   < row["trade_date"] for row in first_aligned)):
        raise ValueError("development panel is not a complete company-day grid")
    download = json.loads(DOWNLOAD_MANIFEST.read_text(encoding="utf-8"))
    observed = _observed_panel(codes, reference_dates, download)
    variants = []
    for variant in result["variants"]:
        rows = variant["daily_asset_rows"]
        if not screen._verify_daily_rows(variant, codes, policy):
            raise ValueError("simulation trace is missing")
        variants.append({"name": variant["name"],
                         "volume": compare_volume(align_execution(rows, calendar), observed)})
    return {"pipeline_version": "pre-wuhan-market-volume-audit-v2",
            "development_period": result["development_period"],
            "execution_reference_period": {"start": reference_dates[0],
                                           "end": reference_dates[-1]},
            "market_dataset_id": result["market_dataset_id"],
            "units": {"simulated_matched_volume": "shares",
                      "provider_volume": "shares"},
            "source_sha256": {"simulation_archive": file_sha256(ARCHIVE),
                              "provider_download_manifest": file_sha256(DOWNLOAD_MANIFEST),
                              "market_manifest": file_sha256(MARKET_MANIFEST),
                              "provider_evidence_manifest": file_sha256(EVIDENCE_MANIFEST),
                              "provider_documentation": docs[0]["sha256"],
                              "screen_policy": file_sha256(screen.POLICY),
                              "screen_code": file_sha256(Path(screen.__file__)),
                              "audit_code": file_sha256(Path(__file__)),
                              "activity_parser": file_sha256(
                                  ROOT / "research/data_pipeline/market_activity.py")},
            "variants": variants,
            "interpretation": "A toy multi-agent venue's matched shares are paired with whole-exchange stock volume on the execution reference date (the preceding session), not the later return-observation date. Whole-day volume is used only for post-hoc audit. This diagnostic does not identify market depth, directional order flow, investor class, price impact, or historical replication."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("volume audit archive differs from recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("volume audit output already exists")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    print(json.dumps({"variants": {row["name"]: row["volume"] for row in result["variants"]}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
