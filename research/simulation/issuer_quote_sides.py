"""Reprice selected existing uninformed orders; keep their original submission."""

from __future__ import annotations

import copy
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from fractions import Fraction

from .agents import finite_range
from .call_auction import LimitOrder
from .portfolio_auction import PortfolioAccount, PortfolioAuction

CASES = ("frozen_half_book", "inform_unreceived_buyers", "inform_unreceived_sellers", "inform_unreceived_both")
ORDER_FIELDS = ("order_id", "owner", "side", "quantity", "limit_price_minor", "sequence")


def _selected(case: str, side: str) -> bool:
    if case not in CASES or side not in {"buy", "sell"}:
        raise ValueError("quote-side counterfactual requires a declared case and order side")
    return case == "inform_unreceived_both" or (case == "inform_unreceived_buyers" and side == "buy") or (case == "inform_unreceived_sellers" and side == "sell")


def _reservation_terms(day: dict, stock: str, order: dict, specs: dict, message_bps: float,
                       venue: dict, background: dict, response: dict) -> tuple[Decimal, Decimal]:
    observation, spec = day["observations"][stock], specs[order["owner"]]
    side = order["side"]
    if spec["kind"] == "strategy":
        p, profile = spec["parameters"], spec["profile"]
        shared = observation["market_signal"] * profile["momentum_loading"] + profile["market_bias"] + observation["scenario_shock"]
        base = p["market_sensitivity"] * max(-1.0, min(1.0, shared))
        shift = Decimal(str(base)) * venue["quote_response_bps"] + Decimal(str(p["market_sensitivity"] * message_bps))
        spread = Decimal((-1 if side == "buy" else 1) * venue["quote_spread_bps"]) / 2
        return shift, spread
    if spec["kind"] != "background" or spec["asset"] != stock:
        raise ValueError("quote-side source contains an invalid participant")
    shared = observation["common_shock"] + observation.get("industry_shock", 0.0)
    shift = Decimal(str(shared)) * response["valuation_response_bps"] + Decimal(str(message_bps))
    urgency = Decimal(background["urgency_bps"] if side == "buy" else -background["urgency_bps"])
    return shift, urgency


def reprice_orders(day: dict, specs: dict, messages: dict[str, float], venue: dict,
                   background: dict, response: dict, case: str) -> tuple[dict, list[dict]]:
    if case not in CASES or set(messages) != set(day["portfolio_auction"]["asset_calls"]):
        raise ValueError("quote-side case or current-message coverage differs")
    for value in messages.values():
        finite_range(value, -500.0, 500.0, "current issuer quote message")
    books, changes = {}, []
    for stock, call in day["portfolio_auction"]["asset_calls"].items():
        orders = []
        for original in call["orders"]:
            order = {key: original[key] for key in ORDER_FIELDS}
            receipt = day["issuer_information_receipts"][order["owner"]][stock]
            if not receipt["received"] and _selected(case, order["side"]):
                shift, spread = _reservation_terms(day, stock, order, specs, messages[stock], venue, background, response)
                units = Decimal(call["price_before_minor"]) * (1 + (shift + spread) / 10000) / venue["tick_minor"]
                rounding = ROUND_FLOOR if order["side"] == "buy" else ROUND_CEILING
                quote = int(units.to_integral_value(rounding=rounding)) * venue["tick_minor"]
                quote = max(call["price_bounds_minor"][0], min(call["price_bounds_minor"][1], quote))
                order["limit_price_minor"] = quote
                changes.append({"stock_code": stock, "order_id": order["order_id"], "owner": order["owner"],
                                "kind": specs[order["owner"]]["kind"], "side": order["side"],
                                "source_limit_price_minor": original["limit_price_minor"],
                                "counterfactual_limit_price_minor": quote, "quote_changed": quote != original["limit_price_minor"],
                                "delivered_valuation_shift_bps": messages[stock]})
            orders.append(LimitOrder(**order))
        books[stock] = orders
    return books, changes


def verify_repriced_orders(day: dict, specs: dict, messages: dict[str, float], settings: dict,
                          background: dict, response: dict, case: str, books: dict, changes: list[dict]) -> None:
    """Reconstruct quote changes independently with rational arithmetic."""
    expected_changes = []
    if case not in CASES or set(messages) != set(day["portfolio_auction"]["asset_calls"]):
        raise ValueError("counterfactual case or message coverage differs")
    if set(books) != set(day["portfolio_auction"]["asset_calls"]):
        raise ValueError("counterfactual book asset coverage differs")
    for stock, call in day["portfolio_auction"]["asset_calls"].items():
        saved = call["orders"]
        if len(books[stock]) != len(saved):
            raise ValueError("counterfactual omitted or added an order")
        for actual, original in zip(books[stock], saved, strict=True):
            if any(getattr(actual, key) != original[key] for key in ORDER_FIELDS if key != "limit_price_minor"):
                raise ValueError("counterfactual changed submission side, quantity, identity or priority")
            name, side = original["owner"], original["side"]
            received = day["issuer_information_receipts"][name][stock]["received"]
            selected = not received and (case == "inform_unreceived_both" or case == "inform_unreceived_buyers" and side == "buy"
                                         or case == "inform_unreceived_sellers" and side == "sell")
            quote = original["limit_price_minor"]
            if selected:
                observation, spec = day["observations"][stock], specs[name]
                message = messages[stock]
                if spec["kind"] == "strategy":
                    p, profile = spec["parameters"], spec["profile"]
                    normalized = min(1.0, max(-1.0, observation["market_signal"] * profile["momentum_loading"]
                                             + profile["market_bias"] + observation["scenario_shock"]))
                    baseline = p["market_sensitivity"] * normalized
                    shift = Fraction(str(baseline)) * settings["quote_response_bps"] + Fraction(str(p["market_sensitivity"] * message))
                    extra = Fraction((-1 if side == "buy" else 1) * settings["quote_spread_bps"], 2)
                else:
                    shift = Fraction(str(observation["common_shock"] + observation.get("industry_shock", 0.0))) * response["valuation_response_bps"] + Fraction(str(message))
                    extra = Fraction(background["urgency_bps"] if side == "buy" else -background["urgency_bps"])
                raw = Fraction(call["price_before_minor"], settings["tick_minor"]) * (1 + (shift + extra) / 10000)
                units = raw.numerator // raw.denominator if side == "buy" else -(-raw.numerator // raw.denominator)
                quote = max(call["price_bounds_minor"][0], min(call["price_bounds_minor"][1], units * settings["tick_minor"]))
                expected_changes.append({"stock_code": stock, "order_id": original["order_id"], "owner": name, "kind": spec["kind"],
                                         "side": side, "source_limit_price_minor": original["limit_price_minor"],
                                         "counterfactual_limit_price_minor": quote, "quote_changed": quote != original["limit_price_minor"],
                                         "delivered_valuation_shift_bps": message})
            if actual.limit_price_minor != quote:
                raise ValueError("counterfactual quote differs from independent unrounded reservation")
    if changes != expected_changes:
        raise ValueError("counterfactual treatment log differs from independent reconstruction")


def reclear_from_state(previous: dict, settings: dict, day: dict, books: dict, session: int) -> dict:
    """Use the original prior state; callers retain only the observed next state."""
    accounts = {name: PortfolioAccount(**copy.deepcopy(account)) for name, account in previous["accounts"].items()}
    venue = PortfolioAuction(accounts, sorted(previous["prices"]), settings)
    venue.prices = dict(previous["prices"])
    venue.session = session - 1
    venue.fee_pool_minor = previous["fee_pool_minor"]
    venue.initial_cash_minor = previous["initial_cash_minor"]
    venue.initial_shares = dict(previous["initial_shares"])
    available = {stock: call["execution_available"] for stock, call in day["portfolio_auction"]["asset_calls"].items()}
    return venue.clear(session, books, available)
