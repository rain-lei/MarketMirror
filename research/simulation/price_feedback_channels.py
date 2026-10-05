"""Separate own-price momentum in portfolio targets from physical quotes."""
from __future__ import annotations

import copy
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from types import SimpleNamespace

from .agents import AgentParameters
from .issuer_valuation import strategy_quote_terms
from .own_price_feedback import validate_scale
from .portfolio_market import portfolio_decision
from .semantic_memory_sensitivity import canonical_hash


def effective_channel_cohort(cohort, specs, target_scale, quote_scale):
    validate_scale(target_scale)
    validate_scale(quote_scale)
    output, quotes = [], {}
    for agent, original in cohort:
        target = original if target_scale == 1 else {
            **original, "momentum_loading": original["momentum_loading"] * target_scale}
        quotes[agent.name] = original if quote_scale == 1 else {
            **original, "momentum_loading": original["momentum_loading"] * quote_scale}
        if target_scale != 1 or quote_scale != 1:
            specs[agent.name]["profile"] = target
            specs[agent.name]["price_feedback_channels"] = {
                "target_scale": target_scale, "quote_scale": quote_scale,
                "original_momentum_loading": original["momentum_loading"]}
        output.append((agent, target))
    return output, quotes


def add_channel_terms(decision, agent, profile, quote_profile, observations, receipts,
                      target_scale, quote_scale):
    target_terms = {stock: strategy_quote_terms(agent, profile, observation, receipts[stock],
                                              decision["beliefs"][stock], False)
                    for stock, observation in observations.items()}
    decision.update(
        issuer_base_beliefs={stock: terms["base_belief"] for stock, terms in target_terms.items()},
        issuer_belief_contributions={stock: terms["belief_contribution"] for stock, terms in target_terms.items()},
        issuer_quote_shift_bps={stock: terms["quote_shift_bps"] for stock, terms in target_terms.items()})
    quote_base = {stock: strategy_quote_terms(agent, quote_profile, observation, receipts[stock],
                                            decision["beliefs"][stock], False)["base_belief"]
                  for stock, observation in observations.items()}
    if target_scale != 1 or quote_scale != 1:
        decision["quote_base_beliefs"] = quote_base
    return quote_base


def same_state_orders(day, previous, baseline_specs, case, venue, previous_streaks,
                      target_scale, quote_scale):
    """Return submitted orders only; preserve factual resources and confirmation."""
    validate_scale(target_scale)
    validate_scale(quote_scale)
    assets = sorted(previous["prices"])
    specs = copy.deepcopy(baseline_specs)
    cohort = []
    for name, spec in specs.items():
        if spec["kind"] != "strategy":
            continue
        if "price_feedback_channels" in spec or "own_price_feedback_control" in spec:
            raise ValueError("same-state channels require unscaled baseline profiles")
        cohort.append((AgentParameters(**spec["parameters"]), spec["profile"]))
    cohort, quote_profiles = effective_channel_cohort(cohort, specs, target_scale, quote_scale)
    streaks = copy.deepcopy(previous_streaks)
    sequence = {name: index for index, name in enumerate(day["arrival_order"])}
    session = day["portfolio_auction"]["session"]
    decisions, orders = {}, {stock: [] for stock in assets}
    for agent, profile in cohort:
        name = agent.name
        receipts = day["issuer_information_receipts"][name]
        observations = {stock: {**day["observations"][stock],
                                "scenario_shock": day["observations"][stock]["scenario_shock"]
                                + receipts[stock]["applied_belief_signal"]} for stock in assets}
        account = SimpleNamespace(**previous["accounts"][name])
        decision = portfolio_decision(agent, profile, account, previous["prices"], observations,
                                      day["covariance"], case, streaks[name], session, False)
        quote_base = add_channel_terms(decision, agent, profile, quote_profiles[name],
                                       day["observations"], receipts, target_scale, quote_scale)
        decisions[name] = decision
        for stock in assets:
            delta, price = decision["order_weight_changes"][stock], previous["prices"][stock]
            quantity = math.floor(abs(delta) * decision["nav_minor"] / (price * venue["lot_size"])) * venue["lot_size"]
            if delta < 0:
                quantity = min(quantity, account.shares[stock])
            if not quantity:
                continue
            side = "buy" if delta > 0 else "sell"
            reservation = Decimal(price) * (1 + (Decimal(str(quote_base[stock])) * venue["quote_response_bps"]
                          + Decimal(str(decision["issuer_quote_shift_bps"][stock]))) / 10000
                          + Decimal((-1 if side == "buy" else 1) * venue["quote_spread_bps"]) / 20000)
            rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
            quote = int((reservation / venue["tick_minor"]).to_integral_value(rounding=rounding)) * venue["tick_minor"]
            lower, upper = day["portfolio_auction"]["asset_calls"][stock]["price_bounds_minor"]
            orders[stock].append({"order_id": f"{session}:{stock}:{name}", "owner": name,
                                  "side": side, "quantity": quantity,
                                  "limit_price_minor": max(lower, min(upper, quote)), "sequence": sequence[name]})
    return {"decisions": decisions,
            "orders": {stock: sorted(rows, key=lambda order: order["sequence"]) for stock, rows in orders.items()}}


def diagonal_as_legacy(simulated, scale):
    """Remove declared metadata only to compare full diagonal legacy objects."""
    result = copy.deepcopy(simulated)
    if scale == 1:
        return result
    channels = {"target_scale": scale, "quote_scale": scale}
    for spec in result["participant_specs"].values():
        if spec["kind"] == "strategy":
            detail = spec.pop("price_feedback_channels")
            if {key: detail[key] for key in channels} != channels:
                raise ValueError("diagonal projection received unequal channels")
            spec["own_price_feedback_control"] = {
                "scale": scale, "original_momentum_loading": detail["original_momentum_loading"]}
    for day in result["trace"]:
        if day.pop("price_feedback_channels") != channels:
            raise ValueError("diagonal day controls differ")
        day["own_price_feedback_scale"] = scale
        for decision in day["decisions"].values():
            if decision.pop("quote_base_beliefs") != decision["issuer_base_beliefs"]:
                raise ValueError("diagonal physical quote differs from target base")
    if result["summary"].pop("price_feedback_channels") != channels:
        raise ValueError("diagonal summary controls differ")
    result["summary"]["own_price_feedback_scale"] = scale
    result["summary"]["trace_sha256"] = canonical_hash(result["trace"])
    return result
