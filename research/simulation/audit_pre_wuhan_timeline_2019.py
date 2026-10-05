"""Check whether the execution-reference and return-observation sessions agree.

This audit reads provider volume only after the archived market simulation has
finished. It does not feed observed execution or outcome data into the venue.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from . import screen_pre_wuhan_market_2019 as screen
from .audit_pre_wuhan_market_volume_2019 import (
    DOWNLOAD_MANIFEST, MARKET_MANIFEST, _observed_panel, align_execution,
)


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "research_outputs/pre_wuhan_order_arrival_sensitivity_2019_v1.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_timeline_audit_2019_v1.json"


def classify_sessions(rows: list[dict], observed: dict[tuple[str, str], int]) -> dict:
    expected = {(row["stock_code"], day) for row in rows
                for day in (row["execution_reference_date"], row["trade_date"])}
    if len({(row["stock_code"], row["trade_date"]) for row in rows}) != len(rows) \
            or set(observed) != expected:
        raise ValueError("execution and outcome status panels differ")
    counts: Counter[str] = Counter()
    discordant = []
    for row in rows:
        code, reference, outcome = (row[key] for key in
                                    ("stock_code", "execution_reference_date", "trade_date"))
        if not row["signal_cutoff_date"] < reference < outcome:
            raise ValueError("signal, execution and outcome dates are not ordered")
        reference_shares = observed[code, reference]
        outcome_shares = observed[code, outcome]
        if any(type(value) is not int or value < 0 for value in
               (reference_shares, outcome_shares, row["matched_volume"])):
            raise ValueError("volume must be nonnegative integer shares")
        if reference_shares == 0 and row["matched_volume"] != 0:
            raise ValueError("synthetic auction filled on a suspended execution reference")
        ref_status = "suspended" if reference_shares == 0 else "trading"
        outcome_status = "suspended" if outcome_shares == 0 else "trading"
        counts[f"reference_{ref_status}_outcome_{outcome_status}"] += 1
        if ref_status != outcome_status:
            discordant.append({"stock_code": code, "execution_reference_date": reference,
                               "return_observation_date": outcome,
                               "reference_status": ref_status, "outcome_status": outcome_status,
                               "simulated_matched_shares": row["matched_volume"],
                               "simulated_auction_return": (row["price_after_minor"] /
                                                            row["price_before_minor"] - 1),
                               "observed_outcome_return": row["observed_return"]})
    return {"company_days": len(rows), "status_cross_tab": dict(sorted(counts.items())),
            "discordant_company_days": len(discordant),
            "simulated_trades_on_outcome_suspended_days": sum(
                row["simulated_matched_shares"] > 0 and row["outcome_status"] == "suspended"
                for row in discordant),
            "discordant_examples": sorted(discordant, key=lambda row:
                                           (row["stock_code"], row["return_observation_date"]))}


def compute() -> dict:
    policy = json.loads(screen.POLICY.read_text(encoding="utf-8"))
    screen._validate_policy(policy)
    result, provenance = screen._validated_archive(
        ARCHIVE, "pre-wuhan-order-arrival-sensitivity-v1", policy)
    candidates = [variant for variant in result["variants"] if variant["name"] == "stochastic_nearest"]
    if len(candidates) != 1 or not screen._verify_daily_rows(
            candidates[0], provenance["stock_codes"], policy):
        raise ValueError("frozen daily simulation trace is unavailable")
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    if market["market_dataset_id"] != result["market_dataset_id"]:
        raise ValueError("market calendar does not match the simulation vintage")
    rows = align_execution(candidates[0]["daily_asset_rows"], market["session_dates"])
    dates = sorted({day for row in rows for day in
                    (row["execution_reference_date"], row["trade_date"])})
    download = json.loads(DOWNLOAD_MANIFEST.read_text(encoding="utf-8"))
    observed = _observed_panel(provenance["stock_codes"], dates, download)
    summary = classify_sessions(rows, observed)
    if (summary["company_days"] != policy["pairs_per_variant"]
            or len({row["trade_date"] for row in rows}) != policy["sessions"]):
        raise ValueError("development company-day coverage differs")
    return {"pipeline_version": "pre-wuhan-timeline-audit-v1",
            "market_dataset_id": result["market_dataset_id"],
            "development_period": result["development_period"],
            "source_sha256": {"simulation_archive": file_sha256(ARCHIVE),
                              "download_manifest": file_sha256(DOWNLOAD_MANIFEST),
                              "market_manifest": file_sha256(MARKET_MANIFEST),
                              "volume_audit_code": file_sha256(
                                  ROOT / "research/simulation/audit_pre_wuhan_market_volume_2019.py"),
                              "screen_code": file_sha256(Path(screen.__file__)),
                              "audit_code": file_sha256(Path(__file__))},
            "summary": summary,
            "interpretation": "The archived auction is gated by the preceding execution-reference session, while its price change is stored under the subsequent return-observation date. When those sessions differ in trading status, an outcome-date return comparison is not a same-session market-replication check. Resolve the clock convention in a new model version before claiming historical same-day price/volume fit."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("timeline archive differs from recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("timeline output already exists")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
