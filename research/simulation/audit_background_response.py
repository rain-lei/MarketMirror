"""Independently rebuild common-news background orders with rational arithmetic."""

from __future__ import annotations

import hashlib
import math
from fractions import Fraction


def expected_demand(stock: str, session: int, name: str, shares: int, price: int,
                    bounds: list[int], venue: dict, background: dict, shock: float, response: dict) -> dict:
    if background["mode"] != "active":
        raise ValueError("background response audit requires active target demand")
    tick, lot = venue["tick_minor"], venue["lot_size"]
    digest = hashlib.sha256(f"{background['seed']}:{stock}:{session}:{name}".encode()).hexdigest()
    width = background["target_range_lots"]
    offset = int(digest[:16], 16) * (2 * width + 1) // 2**64 - width
    target = max(0, background["initial_shares"] + offset * lot)
    delta = target - shares
    side = "buy" if delta > 0 else "sell" if delta < 0 else "hold"
    quantity = min(abs(delta), background["max_order_lots"] * lot)

    def quote_for(shift):
        urgency = background["urgency_bps"] if side == "buy" else -background["urgency_bps"]
        units = Fraction(price) * (10000 + shift + urgency) / (10000 * tick)
        rounded = units.numerator // units.denominator if side == "buy" else -(-units.numerator // units.denominator)
        return max(bounds[0], min(bounds[1], rounded * tick))

    quote = quote_for(Fraction(0)) if quantity else None
    expected = {"mode": "inventory_target", "current_shares": shares, "target_shares": target,
                "target_offset_lots": offset, "requested_quantity": quantity, "side": side,
                "limit_price_minor": quote, "shock_sha256": digest}
    if shock == 0 or response == {"valuation_response_bps": 0, "pulse_participation_bps": 10000}:
        return expected
    liquidity_digest = hashlib.sha256(f"background-liquidity-v1:{background['seed']}:{stock}:{session}:{name}".encode()).hexdigest()
    probability = response["pulse_participation_bps"]
    participates = int(liquidity_digest[:16], 16) * 10000 // 2**64 < probability
    shift = Fraction(str(shock)) * response["valuation_response_bps"]
    expected["common_news_response"] = {
        "common_shock": shock, "valuation_shift_bps": float(shift),
        "participation_probability_bps": probability, "participates": participates,
        "liquidity_sha256": liquidity_digest, "base_requested_quantity": quantity,
        "base_side": side, "base_limit_price_minor": quote,
    }
    if not participates:
        expected.update(requested_quantity=0, side="hold", limit_price_minor=None)
    elif quantity:
        expected["limit_price_minor"] = quote_for(shift)
    return expected


def verify_background_response(day: dict, previous: dict, stock: str, session: int,
                               venue: dict, background: dict, shock: float, response: dict, specs: dict) -> None:
    names = {name for name, spec in specs.items() if spec["kind"] == "background" and spec["asset"] == stock}
    if set(day["background_demands"][stock]) != names:
        raise ValueError("background news demand coverage differs")
    call = day["portfolio_auction"]["asset_calls"][stock]
    orders = {order["owner"]: order for order in call["orders"]}
    for name in names:
        expected = expected_demand(stock, session, name, previous["accounts"][name]["shares"][stock],
                                   previous["prices"][stock], call["price_bounds_minor"], venue, background, shock, response)
        if day["background_demands"][stock][name] != expected:
            raise ValueError("background news demand differs from independent reconstruction")
        if expected["requested_quantity"]:
            order = orders.get(name)
            if order is None or any(order[field] != expected[key] for field, key in (
                    ("side", "side"), ("quantity", "requested_quantity"), ("limit_price_minor", "limit_price_minor"))):
                raise ValueError("background news order differs from declared demand")
        elif name in orders:
            raise ValueError("nonparticipating background trader submitted an order")


def verify_strategy_quotes(day: dict, previous: dict, venue: dict, specs: dict) -> None:
    """Rebuild order quantity and quote from the declared portfolio decision."""
    lot, tick = venue["lot_size"], venue["tick_minor"]
    for stock, call in day["portfolio_auction"]["asset_calls"].items():
        orders = {order["owner"]: order for order in call["orders"]}
        price = previous["prices"][stock]
        for name, spec in specs.items():
            if spec["kind"] != "strategy":
                continue
            decision = day["decisions"][name]
            delta = decision["order_weight_changes"][stock]
            quantity = math.floor(abs(delta) * decision["nav_minor"] / (price * lot)) * lot
            if delta < 0:
                quantity = min(quantity, previous["accounts"][name]["shares"][stock])
            if not quantity:
                if name in orders:
                    raise ValueError("strategy with zero declared demand submitted an order")
                continue
            side = "buy" if delta > 0 else "sell"
            sign = -1 if side == "buy" else 1
            raw = Fraction(price) * (1 + Fraction(str(decision["beliefs"][stock])) * venue["quote_response_bps"] / 10000
                                     + Fraction(sign * venue["quote_spread_bps"], 20000)) / tick
            units = raw.numerator // raw.denominator if side == "buy" else -(-raw.numerator // raw.denominator)
            lower, upper = call["price_bounds_minor"]
            quote = max(lower, min(upper, units * tick))
            order = orders.get(name)
            if order is None or (order["side"], order["quantity"], order["limit_price_minor"]) != (side, quantity, quote):
                raise ValueError("strategy order differs from declared decision and quote")
