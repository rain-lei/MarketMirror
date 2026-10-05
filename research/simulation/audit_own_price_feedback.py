"""Independent explicit control, same-state rational-order and resource audits."""
from __future__ import annotations

import copy
import hashlib
import math

from .audit_issuer_valuation import verify_issuer_day
from .audit_quantity_controls import verify_strategy_allocation


def expected_specs(baseline, scale):
    """This auditor does not import the producer's profile transform."""
    if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 <= scale <= 1:
        raise ValueError("invalid independently audited feedback scale")
    result = copy.deepcopy(baseline)
    for name, spec in result.items():
        if spec["kind"] != "strategy":
            continue
        if "own_price_feedback_control" in spec:
            raise ValueError("baseline already has a momentum intervention")
        if scale != 1:
            original = spec["profile"]["momentum_loading"]
            spec["profile"]["momentum_loading"] = original * scale
            spec["own_price_feedback_control"] = {"scale": scale, "original_momentum_loading": original}
    return result


def verify_specs(actual, baseline, scale):
    if actual != expected_specs(baseline, scale):
        raise ValueError("feedback changed a parameter/resource/profile other than momentum loading")


def verify_arrival(day, specs, core, session):
    if core["order_mode"] != "hashed":
        raise ValueError("frozen multi-seed feedback study requires hashed arrival")
    expected = sorted(specs, key=lambda name: (hashlib.sha256(f"{core['seed']}:{session}:{name}".encode()).hexdigest(), name))
    if day["arrival_order"] != expected:
        raise ValueError("independent arrival priority differs from the declared seed")


def strategy_orders(day, specs):
    fields = ("order_id", "owner", "side", "quantity", "limit_price_minor", "sequence")
    return {stock: [{key: order[key] for key in fields} for order in call["orders"]
                    if specs[order["owner"]]["kind"] == "strategy"]
            for stock, call in day["portfolio_auction"]["asset_calls"].items()}


def verify_same_state(payload, factual_day, previous, baseline_specs, venue, background, response,
                      shocks, industry, issuer, controls, case, previous_streaks, session, scale):
    if set(payload) != {"decisions", "orders"} or set(payload["orders"]) != set(previous["prices"]):
        raise ValueError("same-state output coverage differs")
    specs = expected_specs(baseline_specs, scale)
    strategies = {name for name, spec in specs.items() if spec["kind"] == "strategy"}
    if set(payload["decisions"]) != strategies:
        raise ValueError("same-state decision identities differ")
    for stock, orders in payload["orders"].items():
        seen = set()
        sequence = {name: index for index, name in enumerate(factual_day["arrival_order"])}
        for order in orders:
            if (set(order) != {"order_id", "owner", "side", "quantity", "limit_price_minor", "sequence"}
                    or order["owner"] not in strategies or order["owner"] in seen
                    or type(order["quantity"]) is not int or order["quantity"] <= 0):
                raise ValueError("same-state orders cannot contain hypothetical fills or invalid owners")
            if (order["order_id"] != f"{session}:{stock}:{order['owner']}"
                    or order["sequence"] != sequence[order["owner"]]):
                raise ValueError("same-state identity or arrival priority differs")
            seen.add(order["owner"])
    calls = {stock: {**call, "orders": payload["orders"][stock]}
             for stock, call in factual_day["portfolio_auction"]["asset_calls"].items()}
    view = {**factual_day, "decisions": payload["decisions"],
            "issuer_information_receipts": {name: factual_day["issuer_information_receipts"][name] for name in strategies},
            "background_demands": {stock: {} for stock in calls},
            "portfolio_auction": {**factual_day["portfolio_auction"], "asset_calls": calls}}
    strategy_specs = {name: specs[name] for name in strategies}
    counts = verify_issuer_day(view, previous, strategy_specs, venue, background, response, shocks, industry, issuer, session)
    counts["strategy_allocation_checks"] = verify_strategy_allocation(view, previous, strategy_specs, case, copy.deepcopy(previous_streaks))
    if scale == 1 and (payload["decisions"] != factual_day["decisions"]
                      or payload["orders"] != strategy_orders(factual_day, baseline_specs)):
        raise ValueError("same-state original scale differs from factual orders/decisions")
    return counts


def verify_final_resources(state, summary):
    if state["prices"] != summary["final_prices_minor"] or state["fee_pool_minor"] != summary["fee_pool_minor"]:
        raise ValueError("independent final prices or fees differ")
    if set(state["accounts"]) != set(summary["accounts"]):
        raise ValueError("independent final owner coverage differs")
    for name, account in state["accounts"].items():
        saved = summary["accounts"][name]
        for actual, recorded in (("wallets", "wallets"), ("shares", "shares"), ("sellable", "sellable")):
            if account[actual] != saved[recorded]:
                raise ValueError("independent final wallets/shares/sellable differ")
        nav = sum(account["wallets"].values()) + sum(account["shares"][stock] * state["prices"][stock] for stock in state["prices"])
        if nav != saved["final_wealth_minor"]:
            raise ValueError("independent final wealth differs")
