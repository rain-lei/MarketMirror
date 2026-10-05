"""Explain virtual background sell intentions from inventory and target feedback."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path

from ..data_pipeline.provenance import file_sha256


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "research_outputs/pre_wuhan_price_tie_sensitivity_2019_v1.json"
BASELINE = ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json"
OUTPUT = ROOT / "research_outputs/inventory_target_feedback_2019_v1.json"


def target_shares(code: str, session: int, participant: int, settings: dict, lot: int) -> int:
    name = f"background_{code}_{participant:03d}"
    digest = hashlib.sha256(f"{settings['seed']}:{code}:{session}:{name}".encode()).hexdigest()
    width = settings["target_range_lots"]
    bucket = int(digest[:16], 16) * (2 * width + 1) // 2**64 - width
    return max(0, settings["initial_shares"] + bucket * lot)


def analyze_variant(rows: list[dict], codes: list[str], dates: list[str], settings: dict,
                    lot: int, targets: dict) -> dict:
    expected = {(code, date) for code in codes for date in dates}
    by_key = {(row["stock_code"], row["trade_date"]): row for row in rows}
    if len(by_key) != len(rows) or set(by_key) != expected:
        raise ValueError("inventory audit needs one complete company-date row")
    initial_each = settings["participants"] * settings["initial_shares"]
    holdings = {code: initial_each for code in codes}
    gaps, requests, daily = [], [], []
    for date in dates:
        before_total = target_total = requested_total = filled_total = 0
        for code in codes:
            row = by_key[code, date]
            target = targets[code, date]
            before = holdings[code]
            gap = target - before
            request = row["background_requested_net"]
            fill = row["background_filled_net"]
            if (row["strategy_filled_net"] + fill != 0
                    or row["filled_buy"] != row["filled_sell"]
                    or row["filled_buy"] != row["matched_volume"]):
                raise ValueError("daily strategy-background flow does not balance")
            holdings[code] += fill
            if holdings[code] < 0:
                raise ValueError("background shares became negative")
            before_total += before
            target_total += target
            requested_total += request
            filled_total += fill
            gaps.append(gap)
            requests.append(request)
        daily.append({"trade_date": date, "background_shares_before": before_total,
                      "target_shares": target_total, "target_minus_inventory": target_total - before_total,
                      "background_requested_net": requested_total,
                      "background_filled_net": filled_total,
                      "background_shares_after": sum(holdings.values())})
    if any(row["background_shares_before"] + row["background_filled_net"]
           != row["background_shares_after"] for row in daily):
        raise ValueError("daily inventory identity differs")
    return {"initial_background_shares": initial_each * len(codes),
            "final_background_shares": sum(holdings.values()),
            "mean_daily_target_shares": statistics.mean(row["target_shares"] for row in daily),
            "days_above_target_and_requesting_sales": sum(
                row["background_shares_before"] > row["target_shares"]
                and row["background_requested_net"] < 0 for row in daily),
            "company_day_target_gap_request_correlation": statistics.correlation(gaps, requests),
            "daily": daily}


def compute() -> dict:
    archived = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    for filename, expected in archived["input_sha256"].items():
        if file_sha256(Path(filename)) != expected:
            raise ValueError("calibration source hash differs")
    for filename, expected in archived["code_sha256"].items():
        if file_sha256(Path(filename)) != expected:
            raise ValueError("calibration code hash differs")
    data = archived["result"]
    if data["pipeline_version"] != "pre-wuhan-price-tie-sensitivity-v1":
        raise ValueError("not a frozen tie-break experiment")
    config = json.loads(BASELINE.read_text(encoding="utf-8"))
    codes = data["sample"]["selected_stock_codes"]
    if len(codes) != data["sample"]["selected_companies"] or len(set(codes)) != len(codes):
        raise ValueError("duplicate or incomplete selected companies")
    dates = sorted({row["trade_date"] for variant in data["variants"]
                    for row in variant["daily_asset_rows"]})
    if len(dates) != data["sample"]["sessions"] or dates[0] != data["development_period"]["start"] or dates[-1] != data["development_period"]["end"]:
        raise ValueError("development date coverage differs")
    settings, lot = config["background"], config["venue"]["lot_size"]
    targets = {(code, date): sum(target_shares(code, session, person, settings, lot)
                                 for person in range(settings["participants"]))
               for code in codes for session, date in enumerate(dates)}
    variants = {}
    for variant in data["variants"]:
        variants[variant["name"]] = analyze_variant(variant["daily_asset_rows"],
                                                    codes, dates, settings, lot, targets)
    if set(variants) != {"nearest_prior", "accepted_order_pressure"}:
        raise ValueError("paired tie-break variants differ")
    return {"pipeline_version": "inventory-target-feedback-audit-v1",
            "source_pipeline_version": data["pipeline_version"],
            "companies": len(codes), "sessions": len(dates),
            "background_accounts_per_company": settings["participants"],
            "variants": variants,
            "interpretation": "Virtual inventory feedback only; targets are deterministic random offsets around initial shares. The January-2020-selected cohort is retrospective for 2019, and these are not observed investor inventories or orders.",
            "input_sha256": {str(path): file_sha256(path) for path in (ARCHIVE, BASELINE)},
            "audit_code_sha256": file_sha256(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    destination = args.output.resolve()
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != result:
            raise ValueError("inventory feedback archive differs from recomputation")
    else:
        if destination.exists() or destination.parent != (ROOT / "research_outputs").resolve():
            raise ValueError("inventory feedback output must be a new research_outputs file")
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: {key: value for key, value in variant.items() if key != "daily"}
                      for name, variant in result["variants"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
