"""Public factor target and physical quote channels, with frozen private news."""
from __future__ import annotations

import copy
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .price_feedback_channels import add_channel_terms

MODES = {"none": None, "targets_only": {"strategy_target_scale": 1.0, "quote_scale": 0.0},
         "quotes_only": {"strategy_target_scale": 0.0, "quote_scale": 1.0},
         "both": {"strategy_target_scale": 1.0, "quote_scale": 1.0}}
EXTRA_DECISION = {"public_target_belief_contributions", "public_quote_shift_bps"}


def validate_controls(controls):
    if controls is None:
        return
    if (not isinstance(controls, dict) or set(controls) != {"strategy_target_scale", "quote_scale"}
            or any(type(v) not in (int, float) or v not in (0, 1) for v in controls.values())
            or not any(controls.values())):
        raise ValueError("public factor controls require an explicit nonzero target/quote cell")


def target_observations(actor_observations, observations, controls):
    if controls is None or not controls["strategy_target_scale"]:
        return actor_observations
    return {a: {**o, "scenario_shock": o.get("scenario_shock", 0.0)
                + controls["strategy_target_scale"] * observations[a]["public_factor"]["applied_belief_signal"]}
            for a, o in actor_observations.items()}


def add_factor_terms(decision, agent, target_profile, quote_profile, observations, receipts,
                     target_scale, quote_scale, controls):
    bases = add_channel_terms(decision, agent, target_profile, quote_profile, observations, receipts, target_scale, quote_scale)
    if controls is not None:
        contributions, shifts = {}, {}
        for a, o in observations.items():
            original = decision["issuer_base_beliefs"][a]
            signal = (o["market_signal"] * target_profile["momentum_loading"] + target_profile["market_bias"]
                      + o.get("scenario_shock", 0.0) + controls["strategy_target_scale"] * o["public_factor"]["applied_belief_signal"])
            base = agent.market_sensitivity * max(-1.0, min(1.0, signal))
            decision["issuer_base_beliefs"][a] = base
            decision["issuer_belief_contributions"][a] = decision["beliefs"][a] - base
            contributions[a] = base - original
            shifts[a] = agent.market_sensitivity * controls["quote_scale"] * o["public_factor"]["valuation_shift_bps"]
        decision["public_target_belief_contributions"] = contributions
        decision["public_quote_shift_bps"] = shifts
    return bases


def background_quote(demand, receipt, observation, price, bounds, venue, background, shared_shock, response, controls):
    if controls is None or not controls["quote_scale"]:
        return demand
    applied = controls["quote_scale"] * observation["public_factor"]["valuation_shift_bps"]
    result = {**demand, "public_factor_quote_response": {"base_limit_price_minor": demand["limit_price_minor"],
              "applied_shift_bps": applied, "history_sha256": observation["public_factor"]["history_sha256"]}}
    if demand["requested_quantity"]:
        shift = Decimal(str(shared_shock)) * response["valuation_response_bps"] if response else Decimal(0)
        shift += Decimal(str(receipt["applied_valuation_shift_bps"])) + Decimal(str(applied))
        side = demand["side"]
        urgency = background["urgency_bps"] if side == "buy" else -background["urgency_bps"]
        units = Decimal(price) * (1 + (shift + urgency) / 10000) / venue["tick_minor"]
        rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
        q = int(units.to_integral_value(rounding=rounding)) * venue["tick_minor"]
        result["limit_price_minor"] = max(bounds[0], min(bounds[1], q))
    return result


def same_state_orders(day, previous, specs, venue, case, old_states, factor_rows, parameters, controls, background, response):
    from .agents import AgentParameters
    from .portfolio_market import portfolio_decision
    from .public_market_factor import message
    from types import SimpleNamespace
    validate_controls(controls)
    observations = {a: {**o, "public_factor": message(factor_rows[a], parameters)} for a, o in day["observations"].items()}
    decisions, orders = {}, {a: [] for a in previous["prices"]}
    for name, spec in specs.items():
        if spec["kind"] != "strategy":
            continue
        agent = AgentParameters(**spec["parameters"])
        account = SimpleNamespace(**previous["accounts"][name])
        profile = spec["profile"]
        details = spec.get("price_feedback_channels")
        t, q = (details["target_scale"], details["quote_scale"]) if details else (1.0, 1.0)
        quote_profile = {**profile, "momentum_loading": details["original_momentum_loading"] * q} if details else profile
        receipts = day["issuer_information_receipts"][name]
        actor = {a: {**o, "scenario_shock": o.get("scenario_shock", 0.0) + receipts[a]["applied_belief_signal"]} for a, o in observations.items()}
        actor = target_observations(actor, observations, controls)
        decision = portfolio_decision(agent, profile, account, previous["prices"], actor, day["covariance"], case,
                                      copy.deepcopy(old_states[name]), day["portfolio_auction"]["session"], False)
        bases = add_factor_terms(decision, agent, profile, quote_profile, observations, receipts, t, q, controls)
        decisions[name] = decision
        for a, price in previous["prices"].items():
            delta = decision["order_weight_changes"][a]
            n = math.floor(abs(delta) * decision["nav_minor"] / (price * venue["lot_size"])) * venue["lot_size"]
            if delta < 0:
                n = min(n, account.shares[a])
            if not n:
                continue
            side = "buy" if delta > 0 else "sell"
            shift = Decimal(str(decision["issuer_quote_shift_bps"][a])) + Decimal(str(decision.get("public_quote_shift_bps", {}).get(a, 0.0)))
            units = Decimal(price) * (1 + (Decimal(str(bases[a])) * venue["quote_response_bps"] + shift) / 10000
                                     + Decimal((-1 if side == "buy" else 1) * venue["quote_spread_bps"]) / 20000) / venue["tick_minor"]
            rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
            limit = int(units.to_integral_value(rounding=rounding)) * venue["tick_minor"]
            lower, upper = day["portfolio_auction"]["asset_calls"][a]["price_bounds_minor"]
            orders[a].append({"order_id": f"{day['portfolio_auction']['session']}:{a}:{name}", "owner": name, "side": side,
                              "quantity": n, "limit_price_minor": max(lower, min(upper, limit)), "sequence": day["arrival_order"].index(name)})
    for a, demands in day["background_demands"].items():
        for name, demand in demands.items():
            changed = background_quote(demand, day["issuer_information_receipts"][name][a], observations[a], previous["prices"][a],
                        day["portfolio_auction"]["asset_calls"][a]["price_bounds_minor"], venue, background,
                        observations[a].get("common_shock", 0.0) + observations[a].get("industry_shock", 0.0), response, controls)
            if changed["requested_quantity"]:
                orders[a].append({"order_id": f"{day['portfolio_auction']['session']}:{a}:{name}", "owner": name,
                    "side": changed["side"], "quantity": changed["requested_quantity"], "limit_price_minor": changed["limit_price_minor"],
                    "sequence": day["arrival_order"].index(name)})
    return {"decisions": decisions, "orders": {a: sorted(rows, key=lambda r: r["sequence"]) for a, rows in orders.items()}}
