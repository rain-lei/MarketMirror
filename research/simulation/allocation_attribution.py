"""Ordered bookkeeping decomposition of factual portfolio allocation rules."""

from __future__ import annotations

import math

COMPONENTS = [
    "base_anchor_minus_current", "belief_total_exposure", "total_risk_weight_cap",
    "belief_relative_allocation", "asset_concentration_clip", "risk_liquidation_branch",
    "confirmation_rebalance_wait", "post_allocation_risk_cap", "minimum_trade_gate", "turnover_cap",
]


def close(a, b, label):
    if not math.isfinite(a) or not math.isfinite(b) or not math.isclose(a, b, rel_tol=0, abs_tol=1e-12):
        raise ValueError("allocation attribution differs: " + label)


def decompose(parameters, decision, covariance):
    """Stages sum to factual weight changes; order is fixed, not causal credit."""
    assets = sorted(decision["current_weights"])
    if not assets or set(assets) != set(decision["beliefs"]) or set(covariance) != set(assets):
        raise ValueError("allocation attribution asset identities differ")
    current, beliefs = decision["current_weights"], decision["beliefs"]
    if any(not math.isfinite(v) for v in [*current.values(), *beliefs.values()]) or any(v < 0 for v in current.values()):
        raise ValueError("allocation attribution inputs are nonfinite or negative")

    def risk(weights):
        value = sum(weights[a] * covariance[a][b] * weights[b] for a in assets for b in assets)
        if not math.isfinite(value):
            raise ValueError("allocation attribution covariance is nonfinite")
        return math.sqrt(max(0.0, value))

    score = sum(beliefs.values()) / len(assets)
    top = max(beliefs.values())
    exponents = {a: math.exp(beliefs[a] - top) for a in assets}
    denominator = sum(exponents.values())
    mix = {a: exponents[a] / denominator for a in assets}
    cap = min(parameters["max_weight"], parameters["risk_budget"] / max(1e-6, risk(mix)))
    close(decision["risk_weight_cap"], cap, "total risk cap")
    close(decision["portfolio_volatility"], risk(mix), "belief-mix risk")
    close(decision["current_portfolio_risk"], risk(current), "current risk")
    base = {a: parameters["base_weight"] / len(assets) for a in assets}
    uncapped_total = max(0.0, parameters["base_weight"] + .4 * score)
    uncapped = {a: uncapped_total / len(assets) for a in assets}
    capped_total = min(cap, uncapped_total)
    capped = {a: capped_total / len(assets) for a in assets}
    allocated = {a: capped_total * mix[a] for a in assets}
    limited = {a: min(decision["asset_weight_cap"], allocated[a]) for a in assets}
    liquidated = {a: min(limited[a], current[a]) if decision["risk_liquidation"] else limited[a] for a in assets}
    waits = "confirmation_or_rebalance_wait" in decision["reasons"]
    if waits and decision["risk_liquidation"]:
        raise ValueError("risk liquidation cannot be overwritten by a wait")
    waited = dict(current) if waits else liquidated
    proposed_risk = risk(waited)
    factor = parameters["risk_budget"] / proposed_risk if proposed_risk > parameters["risk_budget"] else 1.0
    final_target = {a: waited[a] * factor for a in assets}
    close(decision["desired_portfolio_risk"], risk(final_target), "final target risk")
    for a in assets:
        close(decision["desired_weights"][a], final_target[a], "final target " + a)
    raw_delta = {a: final_target[a] - current[a] for a in assets}
    turnover = sum(abs(v) for v in raw_delta.values())
    minimum = not decision["risk_liquidation"] and turnover < parameters["min_trade_weight"]
    gate_delta = {a: 0.0 if minimum else raw_delta[a] for a in assets}
    turn_factor = parameters["max_turnover"] / turnover if not decision["risk_liquidation"] and not minimum and turnover > parameters["max_turnover"] else 1.0
    final_delta = {a: gate_delta[a] * turn_factor for a in assets}
    gate_vector = dict(current) if minimum else dict(final_target)
    turnover_vector = {a: current[a] + final_delta[a] for a in assets} if turn_factor != 1.0 else dict(gate_vector)
    vectors = [current, base, uncapped, capped, allocated, limited, liquidated, waited, final_target,
               gate_vector, turnover_vector]
    components = {name: {a: vectors[i + 1][a] - vectors[i][a] for a in assets} for i, name in enumerate(COMPONENTS)}
    for a in assets:
        close(final_delta[a], decision["order_weight_changes"][a], "factual order delta " + a)
        close(sum(v[a] for v in components.values()), final_delta[a], "telescoping delta " + a)
    return {
        "portfolio_belief_score": score, "unconstrained_total_target": uncapped_total,
        "capped_total_target": capped_total, "current_total_exposure": sum(current.values()),
        "component_weight_changes": components,
        "factual_order_weight_changes": final_delta,
        "factual_desired_weights": final_target,
        "decomposition_order_is_fixed_not_causal": True,
    }


def submitted_quantity(delta, nav, price, shares, lot):
    if (type(nav) is not int or nav <= 0 or type(price) is not int or price <= 0
            or type(shares) is not int or shares < 0 or type(lot) is not int or lot <= 0 or not math.isfinite(delta)):
        raise ValueError("allocation order conversion needs finite delta and positive integer resources")
    quantity = math.floor(abs(delta) * nav / (price * lot)) * lot
    if delta < 0:
        quantity = min(quantity, shares)
    return ("buy" if delta > 0 else "sell" if delta < 0 else "hold"), quantity
