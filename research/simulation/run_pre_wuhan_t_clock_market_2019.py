"""Run a development-only market path with one explicit auction/outcome date.

The legacy engine and all participant/auction parameters are unchanged. This
new pipeline only gates a call by the observation date t rather than the prior
reference date t-1, then pairs both synthetic price and volume with t. A full
legacy replay must reproduce its frozen archived trace before the new run is
accepted. The t-day trading status is an exchange replay condition, never an
Agent observation or an ex-ante forecast input.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from . import calibrate_pre_wuhan_market as calibration
from . import screen_pre_wuhan_market_2019 as screen
from .agents import AgentParameters
from .audit_pre_wuhan_market_volume_2019 import (
    DOWNLOAD_MANIFEST, MARKET_MANIFEST, _observed_panel, compare_volume,
)
from .audit_synthetic_observed_returns import compare_pairs
from .historical_replay import load_config, prepare_steps
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import initial_portfolio, simulate_portfolio
from .semantic_signal_join import join_steps


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "research_outputs/pre_wuhan_order_arrival_sensitivity_2019_v1.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_t_clock_market_2019_v1.json"


def set_execution_availability(steps: list[dict], code: str,
                               observed: dict[tuple[str, str], int],
                               *, clock: str) -> list[dict]:
    if clock not in {"legacy_reference", "auction_date"}:
        raise ValueError("unsupported execution clock")
    prepared = []
    for step in steps:
        cutoff, reference, day = (step[key] for key in
                                  ("signal_cutoff_date", "execution_reference_date", "trade_date"))
        if not cutoff < reference < day:
            raise ValueError("step information and auction dates are not ordered")
        status_date = reference if clock == "legacy_reference" else day
        shares = observed[code, status_date]
        if type(shares) is not int or shares < 0:
            raise ValueError("observed trading status requires nonnegative integer volume")
        prepared.append({**step, "execution_available": shares > 0})
    return prepared


def _daily_row(code: str, day: dict, call: dict, specs: dict,
               observed_return: float) -> dict:
    return {"stock_code": code, "trade_date": day["trade_date"],
            "signal_cutoff_date": day["signal_cutoff_date"],
            "price_before_minor": call["price_before_minor"],
            "price_after_minor": call["price_after_minor"],
            "matched_volume": call["matched_volume"],
            "observed_return": observed_return,
            **calibration.accepted_order_flow(call, specs)}


def _initial_audit_state(agents: list[AgentParameters], core: dict, background: dict,
                         basket: list[str], case: dict, venue: dict) -> dict:
    _, accounts, _, _ = initial_portfolio(agents, core, background, basket, case, venue)
    return {"accounts": {name: asdict(account) for name, account in accounts.items()},
            "prices": {code: venue["price_start_minor"] for code in basket},
            "fee_pool_minor": 0,
            "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
            "initial_shares": {code: sum(account.shares[code] for account in accounts.values())
                               for code in basket}}


def compute() -> dict:
    policy = json.loads(screen.POLICY.read_text(encoding="utf-8"))
    screen._validate_policy(policy)
    old, provenance = screen._validated_archive(
        ARCHIVE, "pre-wuhan-order-arrival-sensitivity-v1", policy)
    candidates = [v for v in old["variants"] if v["name"] == "stochastic_nearest"]
    if len(candidates) != 1 or not screen._verify_daily_rows(
            candidates[0], provenance["stock_codes"], policy):
        raise ValueError("legacy stochastic-nearest trace is absent")
    frozen = candidates[0]
    base = json.loads(calibration.BASELINE_CONFIG.read_text(encoding="utf-8"))
    replay = load_config(calibration.REPLAY_CONFIG)
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    download = json.loads(DOWNLOAD_MANIFEST.read_text(encoding="utf-8"))
    csv_path = MARKET_MANIFEST.parent / "market_daily.csv"
    if (market["market_dataset_id"] != old["market_dataset_id"]
            or market["data_kind"] != "observed"
            or market["artifacts"]["market_daily.csv"]["sha256"] != file_sha256(csv_path)
            or len(provenance["stock_codes"]) != policy["companies"]
            or not set(provenance["stock_codes"]) <= set(replay["stock_codes"])):
        raise ValueError("frozen market sample or source differs")
    codes = provenance["stock_codes"]
    dates = sorted({row["trade_date"] for row in frozen["daily_asset_rows"]})
    if len(dates) != policy["sessions"] or dates[0] != policy["period_start"] \
            or dates[-1] != policy["period_end"]:
        raise ValueError("development sessions differ")
    calendar = market["session_dates"]
    if calendar != sorted(set(calendar)):
        raise ValueError("market calendar is not ordered and unique")
    positions = {day: index for index, day in enumerate(calendar)}
    if any(day not in positions or positions[day] < 2 for day in dates):
        raise ValueError("development date lacks two earlier market sessions")
    all_dates = sorted(set(dates) | {calendar[positions[day] - 1] for day in dates})
    observed = _observed_panel(codes, all_dates, download)
    groups = _load_market(csv_path, market)
    agents = [AgentParameters(**row) for row in replay["agents"]]
    joined = {"legacy_reference": {}, "auction_date": {}}
    for code in codes:
        steps = join_steps(prepare_steps(groups[code], dates[0], dates[-1],
                                         replay["momentum_sessions"],
                                         replay["volatility_sessions"]), code, [])
        if ([step["trade_date"] for step in steps] != dates
                or any(step["execution_reference_date"] != calendar[positions[day] - 1]
                       for step, day in zip(steps, dates, strict=True))):
            raise ValueError("company decision clock differs from market calendar")
        for clock in joined:
            joined[clock][code] = set_execution_availability(steps, code, observed, clock=clock)
    item = next(row for row in json.loads(calibration.ARRIVAL_CONFIG.read_text(encoding="utf-8"))["variants"]
                if row["name"] == "stochastic_nearest")
    if item != {"name": "stochastic_nearest", "quote_response_bps": 200,
                "background_urgency_bps": 25, "price_tie_break": "nearest_prior",
                "background_mode": "stochastic_arrival", "arrival_rate_bps": 7500}:
        raise ValueError("frozen stochastic-arrival settings differ")
    venue = {**base["venue"], "quote_response_bps": item["quote_response_bps"]}
    background = {**base["background"], "urgency_bps": item["background_urgency_bps"],
                  "mode": "stochastic_arrival", "case_id": "stochastic_arrival75",
                  "target_range_lots": 0, "arrival_rate_bps": item["arrival_rate_bps"]}
    core = base["core"]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    baskets = [codes[index:index + 3] for index in range(0, len(codes), 3)]
    if len(baskets) != 41 or any(len(basket) != 3 for basket in baskets):
        raise ValueError("frozen three-asset baskets differ")
    old_rows, new_rows, new_pairs = [], [], []
    coverage: Counter[str] = Counter()
    for basket in baskets:
        for clock in ("legacy_reference", "auction_date"):
            path = simulate_portfolio({code: joined[clock][code] for code in basket},
                                      agents, core, background, venue,
                                      base["feedback_parameters"], case, False, "nearest_prior")
            state = (_initial_audit_state(agents, core, background, basket, case, venue)
                     if clock == "auction_date" else None)
            for index, day in enumerate(path["trace"]):
                if day["trade_date"] != dates[index]:
                    raise ValueError("portfolio session date differs")
                if clock == "auction_date":
                    state = audit_portfolio_day(day, state, venue, index,
                                                dense=index == 0, price_tie_break="nearest_prior")
                for code in basket:
                    call = day["portfolio_auction"]["asset_calls"][code]
                    step = joined[clock][code][index]
                    if (call["execution_available"] != step["execution_available"]
                            or (not call["execution_available"] and call["matched_volume"] != 0)):
                        raise ValueError("auction availability differs from explicit clock")
                    row = _daily_row(code, day, call, path["participant_specs"],
                                     step["observed_return"])
                    if clock == "legacy_reference":
                        old_rows.append(row)
                    else:
                        new_rows.append({**row, "auction_date": day["trade_date"],
                                         "pricing_reference_date": day["execution_reference_date"],
                                         "execution_available": call["execution_available"]})
                        new_pairs.append((call["price_after_minor"] /
                                          call["price_before_minor"] - 1,
                                          step["observed_return"]))
                        coverage["zero_matched_volume"] += int(call["matched_volume"] == 0)
                        coverage["execution_unavailable"] += int(not call["execution_available"])
            if clock == "auction_date" and state["prices"] != path["summary"]["final_prices_minor"]:
                raise ValueError("independently audited final prices differ")
    if old_rows != frozen["daily_asset_rows"]:
        raise ValueError("unchanged engine does not reproduce the complete legacy trace")
    if len(new_rows) != policy["pairs_per_variant"]:
        raise ValueError("new clock company-day coverage differs")
    different_status, different_price, different_volume, different_flow = [], [], [], []
    for prior, current in zip(old_rows, new_rows, strict=True):
        key = (prior["stock_code"], prior["trade_date"])
        if key != (current["stock_code"], current["trade_date"]):
            raise ValueError("legacy and corrected company-day order differs")
        if ((observed[prior["stock_code"], current["pricing_reference_date"]] > 0)
                != current["execution_available"]):
            different_status.append(key)
        if any(prior[field] != current[field] for field in
               ("price_before_minor", "price_after_minor")):
            different_price.append(key)
        if prior["matched_volume"] != current["matched_volume"]:
            different_volume.append(key)
        if any(prior[field] != current[field] for field in
               ("strategy_requested_net", "background_requested_net",
                "strategy_filled_net", "background_filled_net")):
            different_flow.append(key)
    path_difference = {
        "availability_changed_company_days": len(different_status),
        "price_changed_company_days": len(different_price),
        "matched_volume_changed_company_days": len(different_volume),
        "role_flow_changed_company_days": len(different_flow),
        "first_price_difference_date": (min(day for _, day in different_price)
                                        if different_price else None),
    }
    observed_t = {(code, day): observed[code, day] for code in codes for day in dates}
    volume = compare_volume(
        [{"stock_code": row["stock_code"], "execution_reference_date": row["auction_date"],
          "matched_volume": row["matched_volume"]} for row in new_rows], observed_t)
    return {
        "pipeline_version": "pre-wuhan-t-clock-market-development-v1",
        "sample_role": "seen_development_only",
        "market_dataset_id": old["market_dataset_id"],
        "development_period": old["development_period"],
        "clock_contract": {"agent_information_cutoff": "t-2",
                           "pricing_reference_date": "t-1",
                           "auction_and_price_date": "t",
                           "execution_availability_date": "t",
                           "observed_return_date": "t",
                           "observed_volume_date": "t"},
        "legacy_exact_trace_reproduced": True,
        "legacy_comparison": frozen["comparison"],
        "corrected_comparison": compare_pairs(new_pairs),
        "path_difference_from_legacy": path_difference,
        "corrected_coverage": dict(coverage),
        "corrected_volume": volume,
        "corrected_daily_asset_rows": new_rows,
        "source_sha256": {"legacy_simulation_archive": file_sha256(ARCHIVE),
                          "market_manifest": file_sha256(MARKET_MANIFEST),
                          "market_daily": file_sha256(csv_path),
                          "download_manifest": file_sha256(DOWNLOAD_MANIFEST),
                          "baseline_config": file_sha256(calibration.BASELINE_CONFIG),
                          "arrival_config": file_sha256(calibration.ARRIVAL_CONFIG),
                          "replay_config": file_sha256(calibration.REPLAY_CONFIG),
                          "screen_policy": file_sha256(screen.POLICY),
                          "portfolio_engine": file_sha256(Path(simulate_portfolio.__code__.co_filename)),
                          "portfolio_audit": file_sha256(Path(audit_portfolio_day.__code__.co_filename)),
                          "pipeline_code": file_sha256(Path(__file__))},
        "interpretation": "A development-only same-auction-date accounting correction. The t-day trading status is an environmental replay condition, not an Agent forecast input. No same-day observed return or volume enters decisions or price formation; both are used only after simulation. The venue remains far smaller than the exchange and is not calibrated for market replication."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("t-clock market archive differs from source recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("t-clock market output already exists")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    print(json.dumps({"legacy_exact_trace_reproduced": result["legacy_exact_trace_reproduced"],
                      "legacy_return_correlation": result["legacy_comparison"]["return_correlation"],
                      "corrected_comparison": result["corrected_comparison"],
                      "path_difference_from_legacy": result["path_difference_from_legacy"],
                      "corrected_coverage": result["corrected_coverage"],
                      "corrected_volume": result["corrected_volume"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
