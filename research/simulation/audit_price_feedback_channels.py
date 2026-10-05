"""Independently audit separate target and quote momentum and actual orders."""
from __future__ import annotations

import copy
import math
from fractions import Fraction

from .audit_issuer_valuation import expected_receipt
from .audit_own_price_feedback import strategy_orders
from .audit_quantity_controls import verify_quantity_day, verify_strategy_allocation


def expected_specs(baseline, target_scale, quote_scale):
    for scale in (target_scale, quote_scale):
        if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 <= scale <= 1:
            raise ValueError("invalid channel scale")
    specs = copy.deepcopy(baseline)
    for spec in specs.values():
        if spec["kind"] != "strategy":
            continue
        if "price_feedback_channels" in spec or "own_price_feedback_control" in spec:
            raise ValueError("baseline already has feedback controls")
        if target_scale != 1 or quote_scale != 1:
            original = spec["profile"]["momentum_loading"]
            if target_scale != 1:
                spec["profile"]["momentum_loading"] = original * target_scale
            spec["price_feedback_channels"] = {"target_scale": target_scale, "quote_scale": quote_scale,
                                                "original_momentum_loading": original}
    return specs


def verify_specs(actual, baseline, target_scale, quote_scale):
    if actual != expected_specs(baseline, target_scale, quote_scale):
        raise ValueError("channel profile/origin changed an undeclared parameter")


def verify_strategy(day, previous, specs, venue, issuer, case, states, session,
                    target_scale, quote_scale):
    strategies = {name: spec for name, spec in specs.items() if spec["kind"] == "strategy"}
    assets = sorted(previous["prices"])
    if set(day["decisions"]) != set(strategies):
        raise ValueError("channel strategy decision coverage differs")
    counts = {"strategy_receipt_checks": 0, "strategy_received": 0, "strategy_rational_quote_checks": 0}
    active = target_scale != 1 or quote_scale != 1
    for stock, call in day["portfolio_auction"]["asset_calls"].items():
        orders = {order["owner"]: order for order in call["orders"]}
        if len(orders) != len(call["orders"]) or any(name not in specs for name in orders):
            raise ValueError("duplicate or unknown order owner")
        price, tick, lot = previous["prices"][stock], venue["tick_minor"], venue["lot_size"]
        lower, upper = call["price_bounds_minor"]
        observation = day["observations"][stock]
        for name, spec in strategies.items():
            detail = spec.get("price_feedback_channels")
            if active:
                if detail is None or set(detail) != {"target_scale", "quote_scale", "original_momentum_loading"}:
                    raise ValueError("channel spec control metadata differs")
                if detail["target_scale"] != target_scale or detail["quote_scale"] != quote_scale:
                    raise ValueError("channel spec applied scales differ")
                original = detail["original_momentum_loading"]
                if spec["profile"]["momentum_loading"] != original * target_scale:
                    raise ValueError("target profile does not apply target scale")
            else:
                if detail is not None:
                    raise ValueError("unmodified baseline has intervention metadata")
                original = spec["profile"]["momentum_loading"]
            receipt = expected_receipt(issuer["by_stock"][stock][session], issuer["parameters"], stock, name)
            if day["issuer_information_receipts"].get(name, {}).get(stock) != receipt:
                raise ValueError("channel private receipt differs")
            counts["strategy_receipt_checks"] += 1
            counts["strategy_received"] += int(receipt["received"])
            profile, parameters = spec["profile"], spec["parameters"]
            target_signal = observation["market_signal"] * profile["momentum_loading"] + profile["market_bias"] + observation["scenario_shock"]
            quote_signal = observation["market_signal"] * (original if quote_scale == 1 else original * quote_scale) + profile["market_bias"] + observation["scenario_shock"]
            target_base = parameters["market_sensitivity"] * min(1.0, max(-1.0, target_signal))
            target_private = parameters["market_sensitivity"] * min(1.0, max(-1.0, target_signal + receipt["applied_belief_signal"]))
            quote_base = parameters["market_sensitivity"] * min(1.0, max(-1.0, quote_signal))
            shift = parameters["market_sensitivity"] * receipt["applied_valuation_shift_bps"]
            decision = day["decisions"][name]
            fields = [("beliefs", target_private), ("issuer_base_beliefs", target_base),
                      ("issuer_belief_contributions", target_private - target_base), ("issuer_quote_shift_bps", shift)]
            if active:
                fields.append(("quote_base_beliefs", quote_base))
            elif "quote_base_beliefs" in decision:
                raise ValueError("baseline has extra quote metadata")
            for field, expected in fields:
                if not math.isclose(decision[field][stock], expected, rel_tol=0.0, abs_tol=1e-12):
                    raise ValueError("independent target/quote channel belief differs")
            delta = decision["order_weight_changes"][stock]
            quantity = math.floor(abs(delta) * decision["nav_minor"] / (price * lot)) * lot
            if delta < 0:
                quantity = min(quantity, previous["accounts"][name]["shares"][stock])
            if not quantity:
                if name in orders:
                    raise ValueError("zero strategy demand submitted an order")
                continue
            side = "buy" if delta > 0 else "sell"
            units = Fraction(price, tick) * (1 + (Fraction(str(quote_base)) * venue["quote_response_bps"]
                    + Fraction(str(shift))) / 10000 + Fraction((-1 if side == "buy" else 1) * venue["quote_spread_bps"], 20000))
            rounded = units.numerator // units.denominator if side == "buy" else -(-units.numerator // units.denominator)
            quote = max(lower, min(upper, rounded * tick))
            order = orders.get(name)
            if order is None or (order["side"], order["quantity"], order["limit_price_minor"]) != (side, quantity, quote):
                raise ValueError("independent separate-channel order differs")
            sequence = day["arrival_order"].index(name)
            if order["order_id"] != f"{session}:{stock}:{name}" or order["sequence"] != sequence:
                raise ValueError("channel order identity/sequence differs")
            counts["strategy_rational_quote_checks"] += 1
    counts["strategy_allocation_checks"] = verify_strategy_allocation(day, previous, specs, case, states)
    return counts


def verify_day(day, previous, specs, venue, background, response, shocks, industry,
               issuer, controls, case, states, session, target_scale, quote_scale):
    expected_controls = {"target_scale": target_scale, "quote_scale": quote_scale} if target_scale != 1 or quote_scale != 1 else None
    if day.get("price_feedback_channels") != expected_controls:
        raise ValueError("daily applied channel controls differ")
    if set(day["issuer_information_receipts"]) != set(specs):
        raise ValueError("channel receipt owner coverage differs")
    backgrounds = {name: spec for name, spec in specs.items() if spec["kind"] == "background"}
    # Existing background audit receives only actual background records. It
    # still verifies every shared observation; all strategies are checked below.
    view = {**day, "decisions": {},
            "issuer_information_receipts": {name: day["issuer_information_receipts"][name] for name in backgrounds},
            "portfolio_auction": {**day["portfolio_auction"], "asset_calls": {
                stock: {**call, "orders": [order for order in call["orders"] if order["owner"] in backgrounds]}
                for stock, call in day["portfolio_auction"]["asset_calls"].items()}}}
    counts = verify_quantity_day(view, previous, backgrounds, venue, background, response, shocks,
                                 industry, issuer, controls, case, {}, session)
    strategy_counts = verify_strategy(day, previous, specs, venue, issuer, case, states, session, target_scale, quote_scale)
    for key, value in strategy_counts.items():
        counts[key] = counts.get(key, 0) + value
    return counts


def verify_same_state(payload, factual_day, previous, baseline_specs, venue, issuer,
                      case, previous_streaks, session, target_scale, quote_scale):
    if set(payload) != {"decisions", "orders"} or set(payload["orders"]) != set(previous["prices"]):
        raise ValueError("same-state channel payload differs")
    specs = expected_specs(baseline_specs, target_scale, quote_scale)
    for orders in payload["orders"].values():
        if any(set(order) != {"order_id", "owner", "side", "quantity", "limit_price_minor", "sequence"}
               or type(order["quantity"]) is not int or order["quantity"] <= 0
               or order["owner"] not in specs or specs[order["owner"]]["kind"] != "strategy" for order in orders):
            raise ValueError("same-state channels cannot contain fills or invalid orders")
        if [order["sequence"] for order in orders] != sorted(order["sequence"] for order in orders):
            raise ValueError("same-state strategy order sequence differs")
    view = {**factual_day, "decisions": payload["decisions"],
            "portfolio_auction": {**factual_day["portfolio_auction"], "asset_calls": {
                stock: {**call, "orders": payload["orders"][stock]}
                for stock, call in factual_day["portfolio_auction"]["asset_calls"].items()}}}
    counts = verify_strategy(view, previous, specs, venue, issuer, case, copy.deepcopy(previous_streaks),
                             session, target_scale, quote_scale)
    if target_scale == quote_scale == 1 and (payload["decisions"] != factual_day["decisions"]
                                           or payload["orders"] != strategy_orders(factual_day, baseline_specs)):
        raise ValueError("original same-state channels differ from factual strategy records")
    return counts
