"""Frozen execution loop with explicit temporal histories and nullable evaluator targets."""
from __future__ import annotations

import math
from dataclasses import asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

from .agents import finite_range
from .call_auction import AuctionAccount, LimitOrder
from .feedback_auction import arrival_order, price_observation, validate_scenario, validate_feedback
from .participant_market import background_demand, validate_background
from .portfolio_auction import PortfolioAuction
from .portfolio_market import validate_case, initial_portfolio, covariance, portfolio_decision
from .semantic_auction_experiment import _validate_venue
from .semantic_memory_sensitivity import canonical_hash
from .temporal_risk_inputs import check_steps as _check_steps
from .scenario_shocks import validate_scenario_shocks
from .own_price_feedback import validate_scale
from .price_feedback_channels import effective_channel_cohort
from .public_market_factor import validate_path, message
from .public_factor_channels import validate_controls, target_observations, add_factor_terms, background_quote

VERSION = "temporal-available-observation-received-risk-portfolio-path-v1"


def simulate_portfolio(joined, agents, core, background, venue_settings, feedback_settings, case, use_text,
                       price_tie_break="nearest_prior", scenario_shocks=None, background_response=None,
                       industry_shocks=None, issuer_valuation=None, quantity_controls=None, target_feedback_scale=1.0, quote_feedback_scale=1.0,
                       public_factor_path=None, public_factor_controls=None, initial_allocation=None):
    if public_factor_path is not None or public_factor_controls is not None:
        raise ValueError('received-risk coverage must not add a second public factor')
    validate_controls(public_factor_controls)
    if (public_factor_path is None) != (public_factor_controls is None):
        raise ValueError("public factor path and controls must be declared together")
    validate_scale(target_feedback_scale)
    validate_scale(quote_feedback_scale)
    if use_text is not False or issuer_valuation is None:
        raise ValueError("separate feedback channels require disabled text and explicit private issuer information")
    validate_case(case)
    _validate_venue(venue_settings)
    validate_scenario(core, venue_settings["lot_size"])
    validate_background(background, venue_settings["lot_size"])
    validate_feedback(feedback_settings)
    assets = sorted(joined)
    controls = None
    if quantity_controls is not None:
        from .quantity_controls import BASELINE, validate_quantity_controls, effective_cohort, anchor_background_demand
        validate_quantity_controls(quantity_controls)
        if quantity_controls["background_anchor"] == "current_inventory" and background["mode"] != "active":
            raise ValueError("current-inventory quantity control requires active background")
        controls = quantity_controls if quantity_controls != BASELINE else None
    if core["feedback_mode"] != "endogenous" or type(use_text) is not bool or not assets:
        raise ValueError("portfolio experiment requires endogenous feedback and explicit text ablation")
    for steps in joined.values():
        _check_steps(steps)
    calendar = [(s["trade_date"], s["signal_cutoff_date"], s["execution_reference_date"]) for s in joined[assets[0]]]
    if any([(s["trade_date"], s["signal_cutoff_date"], s["execution_reference_date"]) for s in joined[a]] != calendar for a in assets):
        raise ValueError("portfolio assets require exactly aligned calendars and information cutoffs")
    if any(not cutoff < reference < day for day, cutoff, reference in calendar):
        raise ValueError("portfolio information dates must be ordered")
    public_path = validate_path(assets, calendar, public_factor_path) if public_factor_path is not None else None
    shock_path = (validate_scenario_shocks(assets, len(calendar), scenario_shocks)
                  if scenario_shocks is not None else None)
    industry_path = None
    if industry_shocks is not None:
        from .industry_shocks import validate_industry_shocks, respond_shared_background
        if shock_path is None:
            raise ValueError("industry shocks require an explicit common and issuer component path")
        industry_path = validate_industry_shocks(assets, len(calendar), industry_shocks)
        for asset in assets:
            values = industry_path["by_group"][industry_path["path_groups"][asset]]
            for session, value in enumerate(values):
                finite_range(shock_path["common"][session] + shock_path["asset_specific"][asset][session] + value,
                             -1.0, 1.0, "combined common issuer and industry shock")
    if background_response is not None:
        from .background_response import validate_background_response, respond_background_demand, is_zero_response
        validate_background_response(background_response)
        if shock_path is None or background["mode"] != "active":
            raise ValueError("background response requires explicit shocks and active background")
    issuer_path = None
    if issuer_valuation is not None:
        from .temporal_public_information_risk import validate_issuer_valuation, message_receipt, strategy_quote_terms, respond_issuer_background
        issuer_path = validate_issuer_valuation(assets, calendar, issuer_valuation)
        if background["mode"] != "active":
            raise ValueError("issuer valuation requires active inventory-target background")
        for asset in assets:
            for session, row in enumerate(issuer_path["by_stock"][asset]):
                shared = (shock_path["common"][session] + shock_path["asset_specific"][asset][session]) if shock_path else 0.0
                if industry_path:
                    shared += industry_path["by_group"][industry_path["path_groups"][asset]][session]
                finite_range(shared + row["valuation_shift_bps"] / issuer_path["parameters"]["belief_scale_bps"],
                             -1.0, 1.0, "combined shared and private issuer shock")
    cohort, accounts, specs, wealth = initial_portfolio(agents, core, background, assets, case, venue_settings)
    from .initial_resource_allocation import allocate
    accounts, allocation_metadata = allocate(accounts, specs, assets, venue_settings["price_start_minor"],
        venue_settings["lot_size"], initial_allocation)
    initial_allocated_accounts = {n: asdict(a) for n, a in accounts.items()} if allocation_metadata else None
    if controls is not None:
        cohort = effective_cohort(cohort, specs, controls)
    cohort, quote_profiles = effective_channel_cohort(cohort, specs, target_feedback_scale, quote_feedback_scale)
    if public_path is not None:
        for spec in specs.values():
            spec["public_factor_controls"] = dict(public_factor_controls)
    venue = PortfolioAuction(accounts, assets, venue_settings, price_tie_break)
    histories = {a: [] for a in assets}
    states = {agent.name: dict(sign=0, streak=0) for agent, _ in cohort}
    peaks, drawdowns = dict(wealth), {n: 0.0 for n in wealth}
    breaches = {n: dict(risk_breach_sessions=0, concentration_breach_sessions=0) for n in wealth}
    trace = []
    for session, (date, cutoff, reference) in enumerate(calendar):
        observations = {}
        for asset in assets:
            observation = {**price_observation(histories[asset], cutoff, feedback_settings),
                           "text_signal": joined[asset][session]["text_signal"] if use_text else 0.0,
                           "text_uncertainty": joined[asset][session]["text_uncertainty"] if use_text else 0.0,
                           "text_evidence": joined[asset][session]["text_evidence"] if use_text else "text:disabled"}
            if shock_path is not None:
                common_shock = shock_path["common"][session]
                issuer_shock = shock_path["asset_specific"][asset][session]
                observation.update({"scenario_id": shock_path["scenario_id"],
                                    "common_shock": common_shock,
                                    "issuer_specific_shock": issuer_shock,
                                    "scenario_shock": common_shock + issuer_shock,
                                    "scenario_evidence": f"scenario:{shock_path['scenario_id']}:{asset}:{session}"})
            if industry_path is not None:
                group = industry_path["path_groups"][asset]
                industry_shock = industry_path["by_group"][group][session]
                observation.update({"industry_scenario_id": industry_path["scenario_id"],
                                    "industry_assignment_mode": industry_path["assignment_mode"],
                                    "industry_code": industry_path["membership"][asset],
                                    "industry_path_group": group, "industry_shock": industry_shock,
                                    "scenario_shock": observation["scenario_shock"] + industry_shock,
                                    "industry_evidence": f"industry-scenario:{industry_path['scenario_id']}:{group}:{session}"})
            if public_path is not None:
                observation["public_factor"] = message(public_path["by_stock"][asset][session], public_path["parameters"])
            observations[asset] = observation
        cov = covariance(histories, cutoff, feedback_settings)
        order = arrival_order(list(accounts), core["order_mode"], core["seed"], session)
        sequence = {n: i for i, n in enumerate(order)}
        books, decisions, demands = {a: [] for a in assets}, {}, {a: {} for a in assets}
        receipts = {} if issuer_path is not None else None
        bounds = {a: venue._venue(a, venue.accounts, {}).price_bounds() for a in assets}
        for agent, profile in cohort:
            actor_observations = observations
            if issuer_path is not None:
                receipts[agent.name] = {a: message_receipt(issuer_path["by_stock"][a][session], issuer_path["parameters"], a, agent.name) for a in assets}
                actor_observations = {a: {**observations[a], "scenario_shock": observations[a].get("scenario_shock", 0.0)
                                         + receipts[agent.name][a]["applied_belief_signal"]} for a in assets}
            actor_observations = target_observations(actor_observations, observations, public_factor_controls)
            decision = portfolio_decision(agent, profile, venue.accounts[agent.name], venue.prices, actor_observations,
                                          cov, case, states[agent.name], session, use_text)
            if issuer_path is not None:
                quote_bases = add_factor_terms(decision, agent, profile, quote_profiles[agent.name],
                                                observations, receipts[agent.name], target_feedback_scale, quote_feedback_scale, public_factor_controls)
            decisions[agent.name] = decision
            for a in assets:
                delta = decision["order_weight_changes"][a]
                quantity = math.floor(abs(delta) * decision["nav_minor"] / (venue.prices[a] * venue_settings["lot_size"])) * venue_settings["lot_size"]
                if delta < 0:
                    quantity = min(quantity, venue.accounts[agent.name].shares[a])
                if not quantity:
                    continue
                side = "buy" if delta > 0 else "sell"
                quote_belief = quote_bases[a]
                private_shift = Decimal(str(decision["issuer_quote_shift_bps"][a])) if issuer_path is not None else Decimal(0)
                if public_path is not None:
                    private_shift += Decimal(str(decision["public_quote_shift_bps"][a]))
                reservation = Decimal(venue.prices[a]) * (1 + (Decimal(str(quote_belief)) * venue_settings["quote_response_bps"] + private_shift) / 10000
                              + Decimal((-1 if side == "buy" else 1) * venue_settings["quote_spread_bps"]) / 20000)
                rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
                quote = int((reservation / venue_settings["tick_minor"]).to_integral_value(rounding=rounding)) * venue_settings["tick_minor"]
                quote = max(bounds[a][0], min(bounds[a][1], quote))
                books[a].append(LimitOrder(f"{session}:{a}:{agent.name}", agent.name, side, quantity, quote, sequence[agent.name]))
        for name, spec in specs.items():
            if spec["kind"] != "background":
                continue
            a, account = spec["asset"], venue.accounts[name]
            demand = background_demand(a, session, name, AuctionAccount(sum(account.wallets.values()), account.shares[a], account.sellable[a]),
                                       venue.prices[a], bounds[a], venue_settings, background)
            if controls is not None:
                demand = anchor_background_demand(demand, venue.prices[a], bounds[a], venue_settings, background, controls)
            if background_response is not None:
                if industry_path is None:
                    demand = respond_background_demand(demand, a, session, name, venue.prices[a], bounds[a],
                                                       venue_settings, background, observations[a]["common_shock"], background_response)
                else:
                    demand = respond_shared_background(demand, a, session, name, venue.prices[a], bounds[a],
                                                       venue_settings, background, observations[a]["common_shock"],
                                                       observations[a]["industry_shock"], observations[a]["industry_code"],
                                                       observations[a]["industry_path_group"], background_response)
            if issuer_path is not None:
                receipt = message_receipt(issuer_path["by_stock"][a][session], issuer_path["parameters"], a, name)
                receipts[name] = {a: receipt}
                shared_shock = observations[a].get("common_shock", 0.0) + observations[a].get("industry_shock", 0.0)
                demand = respond_issuer_background(demand, receipt, venue.prices[a], bounds[a], venue_settings,
                                                   background, shared_shock, background_response)
            if public_path is not None:
                demand = background_quote(demand, receipt, observations[a], venue.prices[a], bounds[a], venue_settings,
                                          background, shared_shock, background_response, public_factor_controls)
            demands[a][name] = demand
            if demand["requested_quantity"]:
                books[a].append(LimitOrder(f"{session}:{a}:{name}", name, demand["side"], demand["requested_quantity"], demand["limit_price_minor"], sequence[name]))
        cleared = venue.clear(session, books, {a: joined[a][session]["execution_available"] for a in assets})
        for a in assets:
            call = cleared["asset_calls"][a]
            histories[a].append({"trade_date": date, "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"]})
        for n, account in venue.accounts.items():
            value = sum(account.wallets.values()) + sum(account.shares[a] * venue.prices[a] for a in assets)
            peaks[n] = max(peaks[n], value)
            drawdowns[n] = max(drawdowns[n], 1 - value / peaks[n])
            if specs[n]["kind"] == "strategy":
                weights = {a: account.shares[a] * venue.prices[a] / value for a in assets}
                risk = math.sqrt(max(0.0, sum(weights[a] * cov[a][b] * weights[b] for a in assets for b in assets)))
                p = specs[n]["parameters"]
                breaches[n]["risk_breach_sessions"] += int(risk > p["risk_budget"] + 1e-9 or sum(weights.values()) > p["max_weight"] + 1e-9)
                breaches[n]["concentration_breach_sessions"] += int(any(w > decisions[n]["asset_weight_cap"] + 1e-9 for w in weights.values()))
        trace.append({"trade_date": date, "signal_cutoff_date": cutoff, "execution_reference_date": reference,
                      "observations": observations, "covariance": cov, "decisions": decisions,
                      "background_demands": demands, "arrival_order": order, "portfolio_auction": cleared})
        if issuer_path is not None:
            trace[-1]["issuer_information_receipts"] = receipts
        if controls is not None:
            trace[-1]["quantity_control_parameters"] = dict(controls)
        if target_feedback_scale != 1 or quote_feedback_scale != 1:
            trace[-1]["price_feedback_channels"] = {"target_scale": target_feedback_scale, "quote_scale": quote_feedback_scale}
        if public_path is not None:
            trace[-1]["public_factor_controls"] = dict(public_factor_controls)
    summaries = {}
    for n, account in venue.accounts.items():
        value = sum(account.wallets.values()) + sum(account.shares[a] * venue.prices[a] for a in assets)
        summaries[n] = {"kind": specs[n]["kind"], "role": specs[n].get("parameters", {}).get("role"), **asdict(account),
                        "initial_wealth_minor": wealth[n], "final_wealth_minor": value, "wealth_multiple": value / wealth[n],
                        "max_drawdown": drawdowns[n], **breaches[n]}
    strategy_orders = [o for day in trace for call in day["portfolio_auction"]["asset_calls"].values() for o in call["orders"] if specs[o["owner"]]["kind"] == "strategy"]
    accepted = sum(o["accepted_quantity"] for o in strategy_orders)
    filled = sum(o["filled_quantity"] for o in strategy_orders)
    requested = sum(o["quantity"] for o in strategy_orders)
    summary = {"assets": assets, "sessions": len(calendar), "cash_mode": case["cash_mode"],
               "initial_cash_weights": case.get("initial_cash_weights"), "use_text": use_text,
               "final_prices_minor": venue.prices, "fee_pool_minor": venue.fee_pool_minor, "accounts": summaries,
               "strategy_requested": requested, "strategy_accepted": accepted, "strategy_filled": filled,
               "strategy_fill_fraction": filled / accepted if accepted else 0.0,
               "strategy_requested_fill_fraction": filled / requested if requested else 0.0,
               "cash_clipped_orders": sum("cash_and_fee_reservation" in o["reasons"] for o in strategy_orders),
               "trace_sha256": canonical_hash(trace)}
    if shock_path is not None:
        summary["scenario_id"] = shock_path["scenario_id"]
        summary["nonzero_scenario_asset_sessions"] = sum(
            shock_path["common"][session] + shock_path["asset_specific"][asset][session]
            + (industry_path["by_group"][industry_path["path_groups"][asset]][session] if industry_path else 0.0) != 0.0
            for session in range(len(calendar)) for asset in assets)
    if industry_path is not None:
        summary["industry_scenario"] = {key: industry_path[key] for key in ("scenario_id", "assignment_mode", "membership", "path_groups")}
    if issuer_path is not None:
        summary["issuer_valuation_scenario"] = {"scenario_id": issuer_path["scenario_id"], "parameters": issuer_path["parameters"],
                                                "path_sha256": canonical_hash(issuer_path)}
    if controls is not None:
        summary["quantity_control_parameters"] = dict(controls)
    if background_response is not None and not is_zero_response(background_response):
        summary["background_response_parameters"] = dict(background_response)
    if target_feedback_scale != 1 or quote_feedback_scale != 1:
        summary["price_feedback_channels"] = {"target_scale": target_feedback_scale, "quote_scale": quote_feedback_scale}
    if public_path is not None:
        summary["public_factor_controls"] = dict(public_factor_controls)
        summary["public_factor_path"] = {"parameters": public_path["parameters"], "path_sha256": canonical_hash(public_path)}
    summary["role_wealth_multiple"] = {role: sum(a["final_wealth_minor"] for a in summaries.values() if a["role"] == role)
                                       / sum(a["initial_wealth_minor"] for a in summaries.values() if a["role"] == role)
                                       for role in ("aggressive", "conservative", "institutional")}
    if allocation_metadata is not None:
        summary["initial_allocation"] = allocation_metadata
        summary["initial_allocated_accounts"] = initial_allocated_accounts
    return {"summary": summary, "trace": trace, "participant_specs": specs}
