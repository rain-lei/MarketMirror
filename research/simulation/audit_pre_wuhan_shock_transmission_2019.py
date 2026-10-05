"""Audit synthetic shock transmission through archived requests, fills and prices."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_common_shock_balanced_2019_v1.json"
CONFIG = ROOT / "research/configs/pre_wuhan_common_shock_balanced_2019.json"
BASE = ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json"
REPLAY = ROOT / "research/configs/wuhan_pre_event_pit_replay_2020.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_shock_transmission_2019_v1.json"
VERSION = "pre-wuhan-archived-shock-transmission-v1"
ROLES = {"aggressive", "conservative", "institutional"}
FLOW_FIELDS = ("strategy_requested_net", "strategy_net", "strategy_filled_net",
               "background_requested_net", "background_net", "background_filled_net", "matched_volume")


def validate_flow(row: dict) -> None:
    nonnegative = ("requested_buy", "requested_sell", "accepted_buy", "accepted_sell", "filled_buy", "filled_sell",
                   "matched_volume", "cash_clipped_buy_orders", "inventory_clipped_sell_orders")
    if any(type(row.get(key)) is not int or row[key] < 0 for key in nonnegative):
        raise ValueError("flow counts must be nonnegative integer shares or order counts")
    if any(type(row.get(key)) is not int for key in (*FLOW_FIELDS, "net_accepted")):
        raise ValueError("net flow fields must be signed integer shares")
    if (row["requested_buy"] < row["accepted_buy"] or row["requested_sell"] < row["accepted_sell"]
            or row["accepted_buy"] < row["filled_buy"] or row["accepted_sell"] < row["filled_sell"]
            or row["filled_buy"] != row["filled_sell"] or row["filled_buy"] != row["matched_volume"]
            or row["net_accepted"] != row["accepted_buy"] - row["accepted_sell"]
            or row["net_accepted"] != row["strategy_net"] + row["background_net"]
            or row["strategy_requested_net"] + row["background_requested_net"] != row["requested_buy"] - row["requested_sell"]
            or row["strategy_filled_net"] + row["background_filled_net"] != 0):
        raise ValueError("request/acceptance/fill flow does not balance")
    roles = row["strategy_role_flow"]
    if not isinstance(roles, dict) or not set(roles) <= ROLES:
        raise ValueError("unexpected strategy role flow")
    for field, aggregate in (("requested_net", "strategy_requested_net"),
                             ("accepted_net", "strategy_net"), ("filled_net", "strategy_filled_net")):
        if (any(type(values.get(field)) is not int for values in roles.values())
                or sum(values[field] for values in roles.values()) != row[aggregate]):
            raise ValueError("role flow does not sum to the strategy aggregate")


def index_rows(rows: list[dict]) -> dict:
    result = {}
    for row in rows:
        key = (row["stock_code"], row["trade_date"])
        if key in result:
            raise ValueError("duplicate company-day transmission row")
        if (any(type(row.get(field)) is not int or row[field] <= 0
                for field in ("price_before_minor", "price_after_minor"))
                or not row["signal_cutoff_date"] < row["trade_date"]
                or not math.isfinite(row["observed_return"])):
            raise ValueError("invalid prices, information dates or observed target")
        validate_flow(row)
        result[key] = row
    return result


def _role_vector(row: dict, field: str) -> tuple:
    return tuple(row["strategy_role_flow"].get(role, {}).get(field, 0) for role in sorted(ROLES))


def paired_group(reference: dict, candidate: dict, keys: list[tuple]) -> dict:
    if set(reference) != set(candidate) or any(key not in reference for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("paired transmission rows require identical, unique company-day keys")
    if not keys:
        raise ValueError("paired transmission group must be nonempty")
    pairs = [(reference[key], candidate[key]) for key in keys]
    return_changes = [after["price_after_minor"] / after["price_before_minor"]
                      - before["price_after_minor"] / before["price_before_minor"] for before, after in pairs]
    return {
        "company_days": len(keys),
        "changed_days": {field: sum(before[field] != after[field] for before, after in pairs) for field in FLOW_FIELDS},
        "strategy_role_request_vector_changed_days": sum(_role_vector(before, "requested_net") != _role_vector(after, "requested_net")
                                                          for before, after in pairs),
        "strategy_role_fill_vector_changed_days": sum(_role_vector(before, "filled_net") != _role_vector(after, "filled_net")
                                                       for before, after in pairs),
        "same_prior_price_days": sum(before["price_before_minor"] == after["price_before_minor"] for before, after in pairs),
        "price_level_changed_days": sum(before["price_after_minor"] != after["price_after_minor"] for before, after in pairs),
        "step_return_changed_days": sum(abs(delta) > 1e-12 for delta in return_changes),
        "mean_step_return_difference_bps": sum(return_changes) / len(keys) * 10000,
        "mean_absolute_step_return_difference_bps": sum(abs(delta) for delta in return_changes) / len(keys) * 10000,
        "max_absolute_step_return_difference_bps": max(abs(delta) for delta in return_changes) * 10000,
        "reference_flow_sums": {field: sum(before[field] for before, _ in pairs) for field in FLOW_FIELDS},
        "candidate_flow_sums": {field: sum(after[field] for _, after in pairs) for field in FLOW_FIELDS},
        "candidate_cash_clipped_buy_orders": sum(after["cash_clipped_buy_orders"] for _, after in pairs),
        "candidate_inventory_clipped_sell_orders": sum(after["inventory_clipped_sell_orders"] for _, after in pairs),
    }


def compute() -> dict:
    archive = json.loads(SOURCE.read_text(encoding="utf-8"))
    result = archive["result"]
    if result["pipeline_version"] != "pre-wuhan-common-shock-balanced-sensitivity-v1":
        raise ValueError("transmission audit requires the frozen balanced shock archive")
    for section in ("input_sha256", "code_sha256"):
        for name, expected in archive[section].items():
            if file_sha256(Path(name)) != expected:
                raise ValueError("source archive input/code hash differs")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    base = json.loads(BASE.read_text(encoding="utf-8"))
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    variants = {row["name"]: row for row in result["variants"]}
    if (len(result["variants"]) != 4 or set(variants) != {row["name"] for row in config["variants"]}
            or config["shock_design"]["pulse_sessions"] != [10, 11, 26, 27]
            or config["shock_design"]["pulse_polarities"] != [1, 1, -1, -1]):
        raise ValueError("transmission archive variants or pulse schedule differs")
    indexed = {name: index_rows(variant["daily_asset_rows"]) for name, variant in variants.items()}
    reference = indexed["no_shock"]
    codes = result["sample"]["selected_stock_codes"]
    dates = sorted({key[1] for key in reference})
    expected_keys = {(code, day) for code in codes for day in dates}
    if len(codes) != 123 or len(set(codes)) != 123 or len(dates) != 43 or set(reference) != expected_keys:
        raise ValueError("transmission archive lacks the complete frozen 123-stock/43-date panel")
    common_by_date = {day: 0.0 for day in dates}
    for session, polarity in zip(config["shock_design"]["pulse_sessions"], config["shock_design"]["pulse_polarities"], strict=True):
        common_by_date[dates[session]] = polarity * 0.20
    for name, rows in indexed.items():
        if set(rows) != expected_keys:
            raise ValueError("variant transmission company-day coverage differs")
        for key, row in rows.items():
            if row["observed_return"] != reference[key]["observed_return"]:
                raise ValueError("observed targets differ between synthetic paths")
            if name in {"no_shock", "common_only"}:
                expected_shock = common_by_date[key[1]] if name == "common_only" else 0.0
                if row["common_shock"] != expected_shock or row["issuer_specific_shock"] != 0.0 or row["scenario_shock"] != expected_shock:
                    raise ValueError("common/no-shock path differs from its declared pulse")
    candidate = indexed["common_only"]
    keys = sorted(expected_keys)
    first_pulse = min(day for day, amplitude in common_by_date.items() if amplitude)
    groups = {
        "before_first_pulse": [key for key in keys if key[1] < first_pulse],
        "first_pulse": [key for key in keys if key[1] == first_pulse],
        "all_pulses": [key for key in keys if common_by_date[key[1]]],
        "positive_pulses": [key for key in keys if common_by_date[key[1]] > 0],
        "negative_pulses": [key for key in keys if common_by_date[key[1]] < 0],
        "whole_path": keys,
    }
    paired = {name: paired_group(reference, candidate, group) for name, group in groups.items()}
    before = paired["before_first_pulse"]
    if (any(before["changed_days"].values()) or before["price_level_changed_days"]
            or before["step_return_changed_days"] or before["strategy_role_request_vector_changed_days"]
            or before["strategy_role_fill_vector_changed_days"]):
        raise ValueError("common shock path changes before the first declared pulse")
    quote_response = variants["common_only"]["quote_response_bps"]
    spread = base["venue"]["quote_spread_bps"]
    roles = {agent["role"]: agent for agent in replay["agents"]}
    if set(roles) != ROLES or len(replay["agents"]) != 3 or quote_response != 200 or spread != 10:
        raise ValueError("quote envelope role/response/spread contract differs")
    envelope = {role: {
        "market_sensitivity": agent["market_sensitivity"],
        "max_absolute_reservation_offset_bps_no_text": abs(agent["market_sensitivity"]) * quote_response + spread / 2,
        "max_direct_common_pulse_offset_bps_same_state": abs(agent["market_sensitivity"]) * quote_response * 0.20,
    } for role, agent in sorted(roles.items())}
    min_price = min(row["price_before_minor"] for rows in indexed.values() for row in rows.values())
    return {
        "pipeline_version": VERSION,
        "sample": {"companies": 123, "sessions": 43, "company_days_per_variant": 5289},
        "pulse_dates": [{"trade_date": day, "common_shock": amplitude} for day, amplitude in common_by_date.items() if amplitude],
        "groups_common_only_vs_no_shock": paired,
        "analytical_quote_envelope": envelope,
        "maximum_one_tick_rounding_allowance_bps": base["venue"]["tick_minor"] / min_price * 10000,
        "background_urgency_bps": variants["common_only"]["background_urgency_bps"],
        "source_archive_sha256": file_sha256(SOURCE),
        "interpretation": "Synthetic path comparison includes endogenous feedback after the first pulse. Quote envelopes are analytical bounds, not recorded order quotes. Flow differences do not identify investor behavior or predict observed prices.",
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
        raise ValueError("transmission audit output must be under research_outputs")
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != payload:
            raise ValueError("archived shock transmission audit differs")
    else:
        if destination.exists():
            raise ValueError("choose a new transmission audit output path")
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"pulse_dates": result["pulse_dates"], "quote_envelope": result["analytical_quote_envelope"],
                      "pulse_group": result["groups_common_only_vs_no_shock"]["all_pulses"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
