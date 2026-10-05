"""Screen fixed no-text auction parameters on 2019 data, before Wuhan outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .audit_background import verify_background_demands
from .audit_synthetic_observed_returns import compare_pairs, flat_price_tie
from .historical_replay import load_config, prepare_steps
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import initial_portfolio, simulate_portfolio
from .semantic_signal_join import join_steps
from .scenario_shocks import build_scenario_shocks
from .wuhan_portfolio_baseline import CODE_PATHS


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_market_calibration_2019.json"
TIE_CONFIG = ROOT / "research/configs/pre_wuhan_price_tie_sensitivity_2019.json"
ARRIVAL_CONFIG = ROOT / "research/configs/pre_wuhan_order_arrival_sensitivity_2019.json"
INVENTORY_CONFIG = ROOT / "research/configs/pre_wuhan_strategy_inventory_sensitivity_2019.json"
EXCHANGE_CONFIG = ROOT / "research/configs/pre_wuhan_exchange_auction_tie_break_2019.json"
SHOCK_CONFIG = ROOT / "research/configs/pre_wuhan_common_shock_sensitivity_2019.json"
SHOCK_BALANCED_CONFIG = ROOT / "research/configs/pre_wuhan_common_shock_balanced_2019.json"
SHOCK_DESIGN = {
    "pulse_sessions": [10, 11, 26, 27],
    "pulse_polarities": [1, 1, -1, -1],
    "issuer_seed": "marketmirror-synthetic-exposure-v1",
}
SHOCK_VARIANTS = [
    {"name": "no_shock", "common_amplitude": 0.0, "issuer_amplitude": 0.0},
    {"name": "common_only", "common_amplitude": 0.30, "issuer_amplitude": 0.0},
    {"name": "issuer_specific_only", "common_amplitude": 0.0, "issuer_amplitude": 0.30},
    {"name": "common_plus_issuer_specific", "common_amplitude": 0.20, "issuer_amplitude": 0.20},
]
SHOCK_BALANCED_VARIANTS = [
    {"name": "no_shock", "common_amplitude": 0.0, "issuer_amplitude": 0.0},
    {"name": "common_only", "common_amplitude": 0.20, "issuer_amplitude": 0.0},
    {"name": "issuer_specific_only", "common_amplitude": 0.0, "issuer_amplitude": 0.20},
    {"name": "common_plus_issuer_specific", "common_amplitude": 0.20, "issuer_amplitude": 0.20},
]
BASELINE_CONFIG = ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json"
REPLAY_CONFIG = ROOT / "research/configs/wuhan_pre_event_pit_replay_2020.json"
UNIVERSE = ROOT / "research/configs/wuhan_qna_active_universe_2020.json"
MARKET_MANIFEST = ROOT / "research_outputs/wuhan_pit_market_prepared_2020_v2/market_manifest.json"
DOWNLOAD_MANIFEST = ROOT / "research_outputs/wuhan_pit_market_download_2020/download_manifest.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_market_calibration_2019_v1.json"


def choose_codes(eligible: list[str], count: int) -> tuple[list[str], list[str]]:
    if count <= 0 or count > len(eligible) or len(set(eligible)) != len(eligible):
        raise ValueError("invalid eligible company count")
    ranked = sorted(eligible, key=lambda code: (hashlib.sha256(code.encode()).hexdigest(), code))
    return sorted(ranked[:count]), sorted(ranked[count:])


def accepted_order_flow(call: dict, specs: dict) -> dict:
    totals = {"accepted_buy": 0, "accepted_sell": 0,
              "requested_buy": 0, "requested_sell": 0,
              "filled_buy": 0, "filled_sell": 0,
              "strategy_net": 0, "background_net": 0,
              "strategy_requested_net": 0, "background_requested_net": 0,
              "strategy_filled_net": 0, "background_filled_net": 0,
              "cash_clipped_buy_orders": 0, "inventory_clipped_sell_orders": 0}
    role_flow = {role: {"requested_net": 0, "accepted_net": 0, "filled_net": 0}
                 for role in ("aggressive", "conservative", "institutional", "unknown")}
    for order in call["orders"]:
        quantity = order["accepted_quantity"]
        requested = order["quantity"]
        filled = order["filled_quantity"]
        kind = specs[order["owner"]]["kind"]
        if (type(quantity) is not int or type(requested) is not int or type(filled) is not int
                or not 0 <= filled <= quantity <= requested or kind not in {"strategy", "background"}):
            raise ValueError("invalid accepted order flow")
        sign = 1 if order["side"] == "buy" else -1 if order["side"] == "sell" else None
        if sign is None:
            raise ValueError("unsupported order side")
        totals["accepted_buy" if sign > 0 else "accepted_sell"] += quantity
        totals["requested_buy" if sign > 0 else "requested_sell"] += requested
        totals["filled_buy" if sign > 0 else "filled_sell"] += filled
        totals[f"{kind}_net"] += sign * quantity
        totals[f"{kind}_requested_net"] += sign * requested
        totals[f"{kind}_filled_net"] += sign * filled
        if kind == "strategy":
            role = specs[order["owner"]].get("parameters", {}).get("role", "unknown")
            if role not in role_flow:
                role_flow[role] = {"requested_net": 0, "accepted_net": 0, "filled_net": 0}
            role_flow[role]["requested_net"] += sign * requested
            role_flow[role]["accepted_net"] += sign * quantity
            role_flow[role]["filled_net"] += sign * filled
        totals["cash_clipped_buy_orders"] += int("cash_and_fee_reservation" in order["reasons"])
        totals["inventory_clipped_sell_orders"] += int("sellable_inventory" in order["reasons"])
    totals["net_accepted"] = totals["accepted_buy"] - totals["accepted_sell"]
    if totals["net_accepted"] != totals["strategy_net"] + totals["background_net"]:
        raise ValueError("accepted order flow decomposition does not balance")
    if (totals["filled_buy"] != totals["filled_sell"]
            or totals["filled_buy"] != call["matched_volume"]
            or totals["strategy_filled_net"] + totals["background_filled_net"] != 0):
        raise ValueError("filled order flow decomposition does not balance")
    totals["strategy_role_flow"] = {role: values for role, values in role_flow.items()
                                     if any(values.values())}
    return totals


def audit_scenario_delivery(simulation: dict, assets: list[str], shocks: dict | None) -> None:
    if shocks is None:
        return
    if simulation["summary"].get("scenario_id") != shocks["scenario_id"]:
        raise ValueError("saved simulation summary differs from the declared shock scenario")
    for session, day in enumerate(simulation["trace"]):
        for asset in assets:
            common = shocks["common"][session]
            issuer = shocks["asset_specific"][asset][session]
            observation = day["observations"][asset]
            if (observation.get("scenario_id") != shocks["scenario_id"]
                    or observation.get("common_shock") != common
                    or observation.get("issuer_specific_shock") != issuer
                    or observation.get("scenario_shock") != common + issuer
                    or observation.get("scenario_evidence") != f"scenario:{shocks['scenario_id']}:{asset}:{session}"):
                raise ValueError("scenario shock was not carried into the asset observation exactly")
            for name, spec in simulation["participant_specs"].items():
                if spec["kind"] != "strategy":
                    continue
                profile, parameters = spec["profile"], spec["parameters"]
                market_signal = max(-1.0, min(1.0, observation["market_signal"] * profile["momentum_loading"]
                                              + profile["market_bias"] + common + issuer))
                expected = parameters["market_sensitivity"] * market_signal
                actual = day["decisions"][name]["beliefs"][asset]
                if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12):
                    raise ValueError("strategy belief does not match the declared exogenous shock")


def compute(config_path: Path = CONFIG) -> tuple[dict, dict, dict]:
    config_path = config_path.resolve()
    shock_configs = {SHOCK_CONFIG.resolve(), SHOCK_BALANCED_CONFIG.resolve()}
    if config_path not in {CONFIG.resolve(), TIE_CONFIG.resolve(), ARRIVAL_CONFIG.resolve(),
                           INVENTORY_CONFIG.resolve(), EXCHANGE_CONFIG.resolve(), *shock_configs}:
        raise ValueError("unsupported frozen development configuration")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    base = json.loads(BASELINE_CONFIG.read_text(encoding="utf-8"))
    replay = load_config(REPLAY_CONFIG)
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    download = json.loads(DOWNLOAD_MANIFEST.read_text(encoding="utf-8"))
    csv_path = MARKET_MANIFEST.parent / "market_daily.csv"
    expected_config_keys = {"development_start", "development_end", "company_count", "basket_size",
                            "eligibility_rule", "company_drop_rule", "variants"}
    if config_path in shock_configs:
        expected_config_keys.add("shock_design")
    if (set(config) != expected_config_keys
            or config["eligibility_rule"] != "complete_development_calendar_and_at_least_22_prior_return_sessions"
            or config["company_drop_rule"] != "largest_sha256_of_six_digit_stock_code_among_eligible"
            or config["basket_size"] != 3 or config["company_count"] != 123
            or config["development_start"] != "2019-11-01"
            or config["development_end"] != "2019-12-31"
            or config["development_end"] >= replay["start_date"]
            or set(universe["stock_codes"]) != set(replay["stock_codes"])
            or market["artifacts"]["market_daily.csv"]["sha256"] != file_sha256(csv_path)
            or market["settings"]["stock_return_basis"] != "adjusted_price_return"
            or download["provider"] != "BaoStock"):
        raise ValueError("pre-Wuhan development source or frozen configuration differs")
    if (market.get("data_kind") != "observed" or download.get("data_kind") != "observed"
            or market.get("config_sha256") != file_sha256(Path(market["config_path"]))
            or market.get("settings") != json.loads(Path(market["config_path"]).read_text(encoding="utf-8"))
            or set(market.get("series", {})) != set(replay["stock_codes"])
            or download["start_date"] > config["development_start"]
            or download["end_date"] < config["development_end"]):
        raise ValueError("development market provenance or date coverage differs")
    for info in market["inputs"].values():
        if file_sha256(Path(info["path"])) != info["sha256"]:
            raise ValueError("prepared market input hash differs")
    for name, info in download["artifacts"].items():
        if file_sha256(DOWNLOAD_MANIFEST.parent / name) != info["sha256"]:
            raise ValueError("download source artifact hash differs")
    if config_path == CONFIG.resolve():
        expected_variants = [
            {"name": name, "quote_response_bps": response, "background_urgency_bps": urgency}
            for name, response, urgency in (("baseline", 200, 25), ("background_250", 200, 250),
                                            ("strategy_1000", 1000, 25), ("both_wider", 1000, 250))]
    elif config_path == TIE_CONFIG.resolve():
        expected_variants = [
            {"name": name, "quote_response_bps": 200, "background_urgency_bps": 25,
             "price_tie_break": rule}
            for name, rule in (("nearest_prior", "nearest_prior"),
                               ("accepted_order_pressure", "accepted_order_pressure"))]
    elif config_path == ARRIVAL_CONFIG.resolve():
        expected_variants = [
            {"name": "target_nearest", "quote_response_bps": 200, "background_urgency_bps": 25,
             "price_tie_break": "nearest_prior", "background_mode": "active"},
            {"name": "stochastic_nearest", "quote_response_bps": 200, "background_urgency_bps": 25,
             "price_tie_break": "nearest_prior", "background_mode": "stochastic_arrival", "arrival_rate_bps": 7500},
            {"name": "stochastic_pressure", "quote_response_bps": 200, "background_urgency_bps": 25,
             "price_tie_break": "accepted_order_pressure", "background_mode": "stochastic_arrival",
             "arrival_rate_bps": 7500}]
    elif config_path == INVENTORY_CONFIG.resolve():
        expected_variants = [
            {"name": name, "quote_response_bps": 200, "background_urgency_bps": 25,
             "price_tie_break": rule, "background_mode": "stochastic_arrival", "arrival_rate_bps": 7500,
             "initial_strategy_inventory": inventory}
            for name, rule, inventory in (
                ("original_stochastic_nearest", "nearest_prior", [0, 300, 800, 1600]),
                ("balanced_stochastic_nearest", "nearest_prior", [100, 400, 600, 900]),
                ("balanced_stochastic_pressure", "accepted_order_pressure", [100, 400, 600, 900]))]
    elif config_path == EXCHANGE_CONFIG.resolve():
        expected_variants = [
            {"name": "uniform_nearest_prior", "quote_response_bps": 200,
             "background_urgency_bps": 25, "price_tie_break": "nearest_prior"},
            {"name": "exchange_specific_tie_break", "quote_response_bps": 200,
             "background_urgency_bps": 25, "price_tie_break": "exchange_specific_2019"}]
    elif config_path in shock_configs:
        if config["shock_design"] != SHOCK_DESIGN:
            raise ValueError("common-shock schedule or synthetic issuer exposure rule changed")
        shock_variants = (SHOCK_VARIANTS if config_path == SHOCK_CONFIG.resolve()
                          else SHOCK_BALANCED_VARIANTS)
        expected_variants = [
            {"name": name, "quote_response_bps": 200, "background_urgency_bps": 25,
             "price_tie_break": "nearest_prior", "common_amplitude": common,
             "issuer_amplitude": issuer}
            for name, common, issuer in ((row["name"], row["common_amplitude"], row["issuer_amplitude"])
                                         for row in shock_variants)]
    else:
        raise AssertionError("configuration branch was not assigned an experiment grid")
    if config["variants"] != expected_variants:
        raise ValueError("calibration grid changed")
    groups = _load_market(csv_path, market)
    dates = sorted({row.trade_date.isoformat() for series in groups.values() for row in series
                    if config["development_start"] <= row.trade_date.isoformat() <= config["development_end"]})
    if len(dates) != 43:
        raise ValueError("2019 development calendar differs")
    full_calendar = set(dates)
    eligible = sorted(code for code in replay["stock_codes"]
                      if full_calendar <= {row.trade_date.isoformat() for row in groups[code]}
                      and sum(row.trade_date.isoformat() < dates[0] for row in groups[code]) >= 22)
    codes, dropped = choose_codes(eligible, config["company_count"])
    if len(eligible) != 124 or len(codes) % config["basket_size"]:
        raise ValueError("2019 development company coverage differs")
    quote_checks = {row["symbol"].split(".")[1]: row for row in download["quote_checks"]
                    if row["symbol"] != download["benchmark_symbol"]}
    exchange_by_code = {}
    for code, row in quote_checks.items():
        exchange = row["symbol"].split(".", 1)[0]
        if exchange not in {"sh", "sz"}:
            raise ValueError("downloaded security symbol has an unsupported exchange prefix")
        exchange_by_code[code] = exchange
    agents = [AgentParameters(**row) for row in replay["agents"]]
    joined = {}
    for code in codes:
        steps = prepare_steps(groups[code], dates[0], dates[-1],
                              replay["momentum_sessions"], replay["volatility_sessions"])
        if [step["trade_date"] for step in steps] != dates:
            raise ValueError("selected company lacks a complete lagged development path")
        joined[code] = join_steps(steps, code, [])
        suspended = set(quote_checks[code]["suspended_sessions"])
        for step in joined[code]:
            step["execution_available"] = step["execution_reference_date"] not in suspended
    baskets = [codes[index:index + 3] for index in range(0, len(codes), 3)]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    variants = []
    for item in config["variants"]:
        venue = {**base["venue"], "quote_response_bps": item["quote_response_bps"]}
        core = {**base["core"], "inventory": item.get("initial_strategy_inventory", base["core"]["inventory"])}
        background = {**base["background"], "urgency_bps": item["background_urgency_bps"]}
        if item.get("background_mode") == "stochastic_arrival":
            background.update(mode="stochastic_arrival", case_id="stochastic_arrival75",
                              target_range_lots=0, arrival_rate_bps=item["arrival_rate_bps"])
        price_tie_break = item.get("price_tie_break", "nearest_prior")
        pairs, daily_rows = [], []
        pairs_by_exchange = {"sh": [], "sz": []}
        coverage = Counter()
        for basket in baskets:
            market_slice = {code: joined[code] for code in basket}
            scenario_shocks = (build_scenario_shocks(
                sorted(basket), len(dates), item["name"], SHOCK_DESIGN["pulse_sessions"],
                SHOCK_DESIGN["pulse_polarities"], item["common_amplitude"],
                item["issuer_amplitude"], SHOCK_DESIGN["issuer_seed"])
                if config_path in shock_configs else None)
            if price_tie_break == "exchange_specific_2019":
                applied_tie_break = {code: ("sse_midpoint" if exchange_by_code[code] == "sh"
                                            else "nearest_prior") for code in basket}
            else:
                applied_tie_break = price_tie_break
            simulation = simulate_portfolio(market_slice, agents, core, background,
                                            venue, base["feedback_parameters"], case, False,
                                            applied_tie_break, scenario_shocks)
            audit_scenario_delivery(simulation, basket, scenario_shocks)
            _, accounts, _, _ = initial_portfolio(agents, core, background, basket, case, venue)
            from dataclasses import asdict
            state = {"accounts": {name: asdict(account) for name, account in accounts.items()},
                     "prices": {code: venue["price_start_minor"] for code in basket},
                     "fee_pool_minor": 0,
                     "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
                     "initial_shares": {code: sum(account.shares[code] for account in accounts.values())
                                        for code in basket}}
            for session, day in enumerate(simulation["trace"]):
                for code in basket:
                    background_specs = {name: spec for name, spec in simulation["participant_specs"].items()
                                        if spec["kind"] == "background" and spec["asset"] == code}
                    previous_asset = {"price_minor": state["prices"][code],
                                      "accounts": {name: {"shares": state["accounts"][name]["shares"][code]}
                                                   for name in background_specs}}
                    verify_background_demands(
                        {"background_demands": day["background_demands"][code],
                         "auction": day["portfolio_auction"]["asset_calls"][code]},
                        previous_asset, background, code, session, venue, background_specs)
                    if background["mode"] == "stochastic_arrival":
                        for demand in day["background_demands"][code].values():
                            coverage["background_draws"] += 1
                            coverage["background_arrivals"] += int(demand["arrived"])
                            coverage["background_buy_draws"] += int(demand["side"] == "buy")
                            coverage["background_sell_draws"] += int(demand["side"] == "sell")
                            coverage["background_requested_buy"] += (
                                demand["requested_quantity"] if demand["side"] == "buy" else 0)
                            coverage["background_requested_sell"] += (
                                demand["requested_quantity"] if demand["side"] == "sell" else 0)
                state = audit_portfolio_day(day, state, venue, session, dense=session == 0,
                                            price_tie_break=applied_tie_break)
                if day["trade_date"] != dates[session]:
                    raise ValueError("simulation calendar drift")
                for code in basket:
                    exchange = exchange_by_code[code]
                    call = day["portfolio_auction"]["asset_calls"][code]
                    pair = (call["price_after_minor"] / call["price_before_minor"] - 1,
                            joined[code][session]["observed_return"])
                    pairs.append(pair)
                    pairs_by_exchange[exchange].append(pair)
                    if config_path in {TIE_CONFIG.resolve(), ARRIVAL_CONFIG.resolve(), INVENTORY_CONFIG.resolve(),
                                       EXCHANGE_CONFIG.resolve(), *shock_configs}:
                        flow = accepted_order_flow(call, simulation["participant_specs"])
                        row = {"stock_code": code, "trade_date": day["trade_date"],
                               "exchange": exchange,
                               "applied_price_tie_break": applied_tie_break[code] if isinstance(applied_tie_break, dict) else applied_tie_break,
                               "signal_cutoff_date": day["signal_cutoff_date"],
                               "price_before_minor": call["price_before_minor"],
                               "price_after_minor": call["price_after_minor"],
                               "matched_volume": call["matched_volume"],
                               "observed_return": joined[code][session]["observed_return"],
                               **flow}
                        if scenario_shocks is not None:
                            observation = day["observations"][code]
                            row.update({"scenario_id": observation["scenario_id"],
                                        "common_shock": observation["common_shock"],
                                        "issuer_specific_shock": observation["issuer_specific_shock"],
                                        "scenario_shock": observation["scenario_shock"]})
                            coverage["nonzero_common_shock_asset_days"] += int(observation["common_shock"] != 0)
                            coverage["nonzero_issuer_shock_asset_days"] += int(observation["issuer_specific_shock"] != 0)
                        daily_rows.append(row)
                        coverage["accepted_net_buy_days"] += int(flow["net_accepted"] > 0)
                        coverage["accepted_net_sell_days"] += int(flow["net_accepted"] < 0)
                        coverage["accepted_net_zero_days"] += int(flow["net_accepted"] == 0)
                        coverage["down_on_net_sell_days"] += int(
                            flow["net_accepted"] < 0
                            and call["price_after_minor"] < call["price_before_minor"])
                    coverage["zero_return_with_trades"] += int(
                        call["price_before_minor"] == call["price_after_minor"]
                        and call["matched_volume"] > 0)
                    if (call["price_before_minor"] == call["price_after_minor"]
                            and call["matched_volume"] > 0):
                        coverage["flat_traded_price_ties"] += int(
                            flat_price_tie(call, venue["tick_minor"]))
                    coverage["zero_matched_volume"] += int(call["matched_volume"] == 0)
                    coverage["execution_unavailable"] += int(not call["execution_available"])
            if state["prices"] != simulation["summary"]["final_prices_minor"]:
                raise ValueError("audited final prices differ")
        variant = {"name": item["name"], "quote_response_bps": item["quote_response_bps"],
                   "background_urgency_bps": item["background_urgency_bps"],
                   "background_mode": item.get("background_mode", "active"),
                   "price_tie_break": price_tie_break,
                   "coverage": dict(coverage), "comparison": compare_pairs(pairs),
                   "comparison_by_exchange": {exchange: compare_pairs(exchange_pairs)
                                              for exchange, exchange_pairs in pairs_by_exchange.items()}}
        if config_path == EXCHANGE_CONFIG.resolve():
            variant["applied_tie_break_by_exchange"] = {
                "sh": "sse_midpoint" if price_tie_break == "exchange_specific_2019" else price_tie_break,
                "sz": "nearest_prior" if price_tie_break == "exchange_specific_2019" else price_tie_break}
        if config_path in shock_configs:
            variant["shock_amplitudes"] = {"common": item["common_amplitude"],
                                           "issuer_specific": item["issuer_amplitude"]}
            variant["shock_schedule"] = SHOCK_DESIGN
        if item.get("arrival_rate_bps") is not None:
            variant["arrival_rate_bps"] = item["arrival_rate_bps"]
        if item.get("initial_strategy_inventory") is not None:
            variant["initial_strategy_inventory"] = item["initial_strategy_inventory"]
        if config_path in {TIE_CONFIG.resolve(), ARRIVAL_CONFIG.resolve(), INVENTORY_CONFIG.resolve(),
                           EXCHANGE_CONFIG.resolve(), *shock_configs}:
            if len(daily_rows) != len(pairs):
                raise ValueError("daily sensitivity trace coverage differs")
            variant["daily_asset_rows"] = daily_rows
        if config_path == INVENTORY_CONFIG.resolve():
            first_day_net = sum(row["strategy_requested_net"] for row in daily_rows
                                if row["trade_date"] == dates[0])
            expected_first_day_net = -209100 if item["name"] == "original_stochastic_nearest" else 0
            if first_day_net != expected_first_day_net:
                raise ValueError("initial strategy inventory no longer produces the frozen first-day order balance")
            variant["first_day_strategy_requested_net"] = first_day_net
        variants.append(variant)
    result = {"pipeline_version": ("pre-wuhan-market-calibration-screen-v1"
                                   if config_path == CONFIG.resolve() else
                                   "pre-wuhan-price-tie-sensitivity-v1"
                                   if config_path == TIE_CONFIG.resolve() else
                                   "pre-wuhan-order-arrival-sensitivity-v1"
                                   if config_path == ARRIVAL_CONFIG.resolve() else
                                   "pre-wuhan-strategy-inventory-sensitivity-v1"
                                   if config_path == INVENTORY_CONFIG.resolve() else
                                   "pre-wuhan-exchange-auction-tie-break-v1"
                                   if config_path == EXCHANGE_CONFIG.resolve() else
                                   "pre-wuhan-common-shock-sensitivity-v1"
                                   if config_path == SHOCK_CONFIG.resolve() else
                                   "pre-wuhan-common-shock-balanced-sensitivity-v1"),
              "market_dataset_id": market["market_dataset_id"],
              "development_period": {"start": dates[0], "end": dates[-1]},
              "sample": {"parent_companies": len(replay["stock_codes"]), "eligible_companies": len(eligible),
                         "selected_companies": len(codes), "selected_stock_codes": codes,
                         "coverage_excluded_codes": sorted(set(replay["stock_codes"]) - set(eligible)),
                         "hash_dropped_codes": dropped, "baskets": len(baskets),
                         "sessions": len(dates)},
              "variants": variants,
              "interpretation": "Development-period mechanism screen only. Cohort was chosen from January 2020 question activity, so 2019 membership is retrospective. No Wuhan text, observed same-day return, or future return drives synthetic prices. Prior Wuhan diagnostics were already inspected, so later Wuhan comparison is not blind holdout validation."}
    source_files = (config_path, BASELINE_CONFIG, REPLAY_CONFIG, UNIVERSE,
                    MARKET_MANIFEST, csv_path, DOWNLOAD_MANIFEST, Path(market["config_path"]),
                    *(Path(info["path"]) for info in market["inputs"].values()),
                    *(DOWNLOAD_MANIFEST.parent / name for name in download["artifacts"]))
    inputs = {str(path.resolve()): file_sha256(path) for path in source_files}
    code = {str(Path(__file__).resolve()): file_sha256(Path(__file__))}
    for path in (*CODE_PATHS.values(), ROOT / "research/simulation/audit_synthetic_observed_returns.py",
                 ROOT / "research/simulation/audit_background.py"):
        code[str(path.resolve())] = file_sha256(path)
    if config_path in shock_configs:
        scenario_code = ROOT / "research/simulation/scenario_shocks.py"
        code[str(scenario_code.resolve())] = file_sha256(scenario_code)
    return result, inputs, code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result, inputs, code = compute(args.config)
    payload = {"result": result, "input_sha256": inputs, "code_sha256": code}
    destination = args.output.resolve()
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != payload:
            raise ValueError("archived development screen differs from recomputation")
    else:
        if destination.exists() or destination.parent != (ROOT / "research_outputs").resolve():
            raise ValueError("calibration output must be a new research_outputs file")
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    print(json.dumps({"sample": {key: value for key, value in result["sample"].items()
                                  if key != "selected_stock_codes"},
                      "variants": [{"name": row["name"], **row["comparison"]["synthetic"]}
                                   for row in result["variants"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
