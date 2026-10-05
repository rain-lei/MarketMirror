"""Explicit profile scaling and same-state orders; no hypothetical executions."""
from __future__ import annotations

import copy
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from types import SimpleNamespace

from .agents import AgentParameters
from .issuer_valuation import strategy_quote_terms
from .portfolio_market import portfolio_decision


def validate_scale(scale):
    if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 <= scale <= 1:
        raise ValueError("own-price feedback scale must be a finite number in [0,1]")


def effective_feedback_cohort(cohort, specs, scale):
    """Keep every parameter and profile field except momentum loading intact."""
    validate_scale(scale)
    if scale == 1:
        return cohort
    output = []
    for agent, original in cohort:
        profile = {**original, "momentum_loading": original["momentum_loading"] * scale}
        specs[agent.name]["profile"] = profile
        specs[agent.name]["own_price_feedback_control"] = {
            "scale": scale, "original_momentum_loading": original["momentum_loading"]}
        output.append((agent, profile))
    return output


def same_state_orders(day, previous, baseline_specs, case, venue, previous_streaks, scale):
    """Recompute only strategy decisions/orders on one factual prior state.

    Wallets, shares, covariance, own-price observation, receipts and previous
    confirmation states are held fixed. Returned orders have no accepted/filled
    quantity or clearing price; the result is not a new multi-day price path.
    Input confirmation state is copied before decisions are made.
    """
    validate_scale(scale)
    assets = sorted(previous["prices"])
    decisions, orders = {}, {stock: [] for stock in assets}
    streaks = copy.deepcopy(previous_streaks)
    sequence = {name: index for index, name in enumerate(day["arrival_order"])}
    session = day["portfolio_auction"]["session"]
    for name, spec in baseline_specs.items():
        if spec["kind"] != "strategy":
            continue
        if "own_price_feedback_control" in spec:
            raise ValueError("same-state comparison requires unscaled baseline profiles")
        agent = AgentParameters(**spec["parameters"])
        profile = spec["profile"] if scale == 1 else {
            **spec["profile"], "momentum_loading": spec["profile"]["momentum_loading"] * scale}
        receipts = day["issuer_information_receipts"][name]
        observations = {stock: {**day["observations"][stock],
                        "scenario_shock": day["observations"][stock]["scenario_shock"]
                        + receipts[stock]["applied_belief_signal"]} for stock in assets}
        account = SimpleNamespace(**previous["accounts"][name])
        decision = portfolio_decision(agent, profile, account, previous["prices"], observations,
                                      day["covariance"], case, streaks[name], session, False)
        terms = {stock: strategy_quote_terms(agent, profile, day["observations"][stock],
                                             receipts[stock], decision["beliefs"][stock], False) for stock in assets}
        decision.update(issuer_base_beliefs={stock: terms[stock]["base_belief"] for stock in assets},
                        issuer_belief_contributions={stock: terms[stock]["belief_contribution"] for stock in assets},
                        issuer_quote_shift_bps={stock: terms[stock]["quote_shift_bps"] for stock in assets})
        decisions[name] = decision
        for stock in assets:
            delta, price = decision["order_weight_changes"][stock], previous["prices"][stock]
            quantity = math.floor(abs(delta) * decision["nav_minor"] / (price * venue["lot_size"])) * venue["lot_size"]
            if delta < 0:
                quantity = min(quantity, account.shares[stock])
            if not quantity:
                continue
            side = "buy" if delta > 0 else "sell"
            reservation = Decimal(price) * (1 + (Decimal(str(terms[stock]["base_belief"])) * venue["quote_response_bps"]
                          + Decimal(str(terms[stock]["quote_shift_bps"]))) / 10000
                          + Decimal((-1 if side == "buy" else 1) * venue["quote_spread_bps"]) / 20000)
            rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
            quote = int((reservation / venue["tick_minor"]).to_integral_value(rounding=rounding)) * venue["tick_minor"]
            lower, upper = day["portfolio_auction"]["asset_calls"][stock]["price_bounds_minor"]
            orders[stock].append({"order_id": f"{session}:{stock}:{name}", "owner": name,
                                  "side": side, "quantity": quantity,
                                  "limit_price_minor": max(lower, min(upper, quote)), "sequence": sequence[name]})
    return {"decisions": decisions,
            "orders": {stock: sorted(rows, key=lambda order: order["sequence"]) for stock, rows in orders.items()}}


def order_totals(orders):
    return {side: sum(order["quantity"] for rows in orders.values() for order in rows if order["side"] == side)
            for side in ("buy", "sell")}
