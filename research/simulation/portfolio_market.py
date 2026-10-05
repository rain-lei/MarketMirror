"""Causal portfolio decisions, concentration limits and finite background calls."""
from __future__ import annotations

import math
from dataclasses import asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

from .agents import finite_range
from .call_auction import AuctionAccount, LimitOrder
from .feedback_auction import participants, arrival_order, price_observation, validate_scenario, validate_feedback
from .participant_market import background_demand, validate_background
from .portfolio_auction import PortfolioAccount, PortfolioAuction
from .semantic_auction_experiment import _validate_venue
from .semantic_memory_sensitivity import canonical_hash
from .semantic_replay import _check_steps
from .scenario_shocks import validate_scenario_shocks

VERSION = "causal-portfolio-market-v2"


def validate_case(case):
    required = {"case_id", "cash_mode", "institutional_asset_cap"}
    if (not isinstance(case, dict) or set(case) not in (required, required | {"initial_cash_weights"})
            or not isinstance(case["case_id"], str) or not case["case_id"]
            or case["cash_mode"] not in {"shared", "separated"}):
        raise ValueError("explicit portfolio cash mode and institutional concentration required")
    finite_range(case["institutional_asset_cap"], 0.01, 1.0, "institutional asset cap")
    weights = case.get("initial_cash_weights")
    if weights is not None and (case["cash_mode"] != "separated" or not isinstance(weights, list)
                               or len(weights) < 2 or any(type(v) is not int or v <= 0 for v in weights)):
        raise ValueError("initial cash weights require positive integers and separated wallets")


def _initial_wallets(total_cash, assets, weights):
    weights = weights or [1] * len(assets)
    if len(weights) != len(assets):
        raise ValueError("initial cash weights must match the portfolio assets")
    denominator = sum(weights)
    cash = [total_cash * weight // denominator for weight in weights]
    remainder = total_cash - sum(cash)
    cash[max(range(len(weights)), key=lambda i: weights[i])] += remainder
    return dict(zip(assets, cash, strict=True))


def covariance(histories, cutoff, settings):
    assets = sorted(histories)
    window = settings["volatility_sessions"]
    values = {}
    for asset in assets:
        rows = [r for r in histories[asset] if r["trade_date"] <= cutoff]
        values[asset] = ([0.0] * window + [r["price_after_minor"] / r["price_before_minor"] - 1 for r in rows])[-window:]
    means = {a: sum(values[a]) / window for a in assets}
    matrix = {}
    for a in assets:
        matrix[a] = {}
        for b in assets:
            value = sum((x - means[a]) * (y - means[b]) for x, y in zip(values[a], values[b], strict=True)) / (window - 1)
            matrix[a][b] = max(settings["volatility_floor"] ** 2, value) if a == b else 0.75 * value
    return matrix


def initial_portfolio(agents, core, background, assets, case, venue):
    cohort = participants(agents, core)
    accounts, specs = {}, {}
    for agent, profile in cohort:
        raw = Decimal(str(agent.initial_cash)) * 100
        if raw != raw.to_integral_value():
            raise ValueError("portfolio initial cash must have at most two decimal places")
        cash = int(raw)
        shares = {a: core["inventory"][int(agent.name.rsplit("_", 1)[1])] for a in assets}
        total_cash = cash * len(assets)
        wallets = {"shared": total_cash} if case["cash_mode"] == "shared" else _initial_wallets(
            total_cash, assets, case.get("initial_cash_weights"))
        accounts[agent.name] = PortfolioAccount(wallets, shares, dict(shares))
        specs[agent.name] = {"kind": "strategy", "parameters": asdict(agent), "profile": profile}
    for asset in assets:
        for index in range(background["participants"]):
            name = f"background_{asset}_{index:03d}"
            wallets = {a: int(Decimal(str(background["initial_cash"])) * 100) if a == asset else 0 for a in assets}
            shares = {a: background["initial_shares"] if a == asset else 0 for a in assets}
            accounts[name] = PortfolioAccount(wallets, shares, dict(shares))
            specs[name] = {"kind": "background", "asset": asset}
    wealth = {n: sum(a.wallets.values()) + sum(a.shares[s] * venue["price_start_minor"] for s in assets) for n, a in accounts.items()}
    return cohort, accounts, specs, wealth


def portfolio_decision(agent, profile, account, prices, observations, cov, case, state, session, use_text):
    assets = sorted(prices)
    nav = sum(account.wallets.values()) + sum(account.shares[a] * prices[a] for a in assets)
    current = {a: account.shares[a] * prices[a] / nav for a in assets}
    beliefs = {}
    for a in assets:
        o = observations[a]
        scenario_shock = o.get("scenario_shock", 0.0)
        finite_range(scenario_shock, -1.0, 1.0, "scenario shock")
        market = max(-1.0, min(1.0, o["market_signal"] * profile["momentum_loading"]
                                + profile["market_bias"] + scenario_shock))
        beliefs[a] = agent.market_sensitivity * market + (agent.text_sensitivity * o["text_signal"]
                     - agent.uncertainty_aversion * o["text_uncertainty"] if use_text else 0.0)
    score = sum(beliefs.values()) / len(assets)
    sign = (score > 1e-12) - (score < -1e-12)
    state["streak"] = state["streak"] + 1 if sign and sign == state["sign"] else (1 if sign else 0)
    state["sign"] = sign
    exponent = {a: math.exp(beliefs[a] - max(beliefs.values())) for a in assets}
    distribution = {a: exponent[a] / sum(exponent.values()) for a in assets}
    risk = math.sqrt(max(0.0, sum(distribution[a] * cov[a][b] * distribution[b] for a in assets for b in assets)))
    cap = min(agent.max_weight, agent.risk_budget / max(1e-6, risk))
    asset_cap = case["institutional_asset_cap"] if agent.role == "institutional" else 1.0
    total = min(cap, max(0.0, agent.base_weight + 0.4 * score))
    target = {a: min(asset_cap, total * distribution[a]) for a in assets}
    def exposure_risk(weights):
        return math.sqrt(max(0.0, sum(weights[a] * cov[a][b] * weights[b] for a in assets for b in assets)))
    current_risk = exposure_risk(current)
    liquidation = (sum(current.values()) > cap + 1e-12 or any(w > asset_cap + 1e-12 for w in current.values())
                   or current_risk > agent.risk_budget + 1e-12)
    reasons = ["portfolio_risk", "institutional_concentration" if agent.role == "institutional" else "portfolio_allocation"]
    if liquidation:
        target = {a: min(target[a], current[a]) for a in assets}
        reasons.append("risk_liquidation_priority")
    elif (state["streak"] < agent.confirmation_steps and sign) or session % agent.rebalance_interval:
        target = dict(current)
        reasons.append("confirmation_or_rebalance_wait")
    # Concentration clipping or liquidation may remove a negatively correlated
    # hedge. Re-evaluate the actual proposed vector, not its original mix.
    target_risk = exposure_risk(target)
    if target_risk > agent.risk_budget:
        scale = agent.risk_budget / target_risk
        target = {a: w * scale for a, w in target.items()}
        reasons.append("actual_portfolio_risk_cap")
    delta = {a: target[a] - current[a] for a in assets}
    turnover = sum(abs(d) for d in delta.values())
    if not liquidation and turnover < agent.min_trade_weight:
        delta = {a: 0.0 for a in assets}
        reasons.append("minimum_portfolio_trade")
    elif not liquidation and turnover > agent.max_turnover:
        delta = {a: d * agent.max_turnover / turnover for a, d in delta.items()}
        reasons.append("portfolio_turnover_cap")
    return {"nav_minor": nav, "beliefs": beliefs, "current_weights": current, "desired_weights": target,
            "order_weight_changes": delta, "risk_weight_cap": cap, "portfolio_volatility": risk,
            "asset_weight_cap": asset_cap, "current_portfolio_risk": current_risk,
            "desired_portfolio_risk": exposure_risk(target), "risk_liquidation": liquidation, "reasons": reasons}


def simulate_portfolio(joined, agents, core, background, venue_settings, feedback_settings, case, use_text,
                       price_tie_break="nearest_prior", scenario_shocks=None, background_response=None,
                       industry_shocks=None, issuer_valuation=None, quantity_controls=None):
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
        from .issuer_valuation import validate_issuer_valuation, message_receipt, strategy_quote_terms, respond_issuer_background
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
    if controls is not None:
        cohort = effective_cohort(cohort, specs, controls)
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
            decision = portfolio_decision(agent, profile, venue.accounts[agent.name], venue.prices, actor_observations,
                                          cov, case, states[agent.name], session, use_text)
            if issuer_path is not None:
                terms = {a: strategy_quote_terms(agent, profile, observations[a], receipts[agent.name][a], decision["beliefs"][a], use_text) for a in assets}
                decision.update(issuer_base_beliefs={a: terms[a]["base_belief"] for a in assets},
                                issuer_belief_contributions={a: terms[a]["belief_contribution"] for a in assets},
                                issuer_quote_shift_bps={a: terms[a]["quote_shift_bps"] for a in assets})
            decisions[agent.name] = decision
            for a in assets:
                delta = decision["order_weight_changes"][a]
                quantity = math.floor(abs(delta) * decision["nav_minor"] / (venue.prices[a] * venue_settings["lot_size"])) * venue_settings["lot_size"]
                if delta < 0:
                    quantity = min(quantity, venue.accounts[agent.name].shares[a])
                if not quantity:
                    continue
                side = "buy" if delta > 0 else "sell"
                quote_belief = decision["issuer_base_beliefs"][a] if issuer_path is not None else decision["beliefs"][a]
                private_shift = Decimal(str(decision["issuer_quote_shift_bps"][a])) if issuer_path is not None else Decimal(0)
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
    summary["role_wealth_multiple"] = {role: sum(a["final_wealth_minor"] for a in summaries.values() if a["role"] == role)
                                       / sum(a["initial_wealth_minor"] for a in summaries.values() if a["role"] == role)
                                       for role in ("aggressive", "conservative", "institutional")}
    return {"summary": summary, "trace": trace, "participant_specs": specs}
