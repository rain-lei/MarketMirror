"""Explicit common-news valuation and liquidity responses for background traders."""

from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .agents import finite_range

VERSION = "common-news-background-response-v1"


def validate_background_response(response: dict) -> None:
    if not isinstance(response, dict) or set(response) != {"valuation_response_bps", "pulse_participation_bps"}:
        raise ValueError("background response needs explicit valuation and participation parameters")
    for field, maximum in (("valuation_response_bps", 1000), ("pulse_participation_bps", 10000)):
        if type(response[field]) is not int or not 0 <= response[field] <= maximum:
            raise ValueError(f"background response {field} must be a bounded integer")


def is_zero_response(response: dict) -> bool:
    return response["valuation_response_bps"] == 0 and response["pulse_participation_bps"] == 10000


def respond_background_demand(demand: dict, stock: str, session: int, name: str, price: int,
                              bounds: tuple[int, int], venue: dict, background: dict,
                              common_shock: float, response: dict) -> dict:
    """Use declared scenario news, never realized returns, to amend a finite order.

    Participation draws are coupled across valuation cells and independent of
    the inventory-target hash. The zero-response contract preserves old traces.
    """
    validate_background_response(response)
    finite_range(common_shock, -1.0, 1.0, "common background news")
    if background["mode"] != "active" or demand["mode"] != "inventory_target":
        raise ValueError("common-news response currently requires active inventory-target background")
    if common_shock == 0 or is_zero_response(response):
        return demand
    digest = hashlib.sha256(f"background-liquidity-v1:{background['seed']}:{stock}:{session}:{name}".encode()).hexdigest()
    probability = response["pulse_participation_bps"]
    participates = int(digest[:16], 16) * 10000 // 2**64 < probability
    shift = Decimal(str(common_shock)) * response["valuation_response_bps"]
    result = dict(demand)
    result["common_news_response"] = {
        "common_shock": common_shock, "valuation_shift_bps": float(shift),
        "participation_probability_bps": probability, "participates": participates,
        "liquidity_sha256": digest, "base_requested_quantity": demand["requested_quantity"],
        "base_side": demand["side"], "base_limit_price_minor": demand["limit_price_minor"],
    }
    if not participates:
        result.update(requested_quantity=0, side="hold", limit_price_minor=None)
    elif demand["requested_quantity"]:
        sign = 1 if demand["side"] == "buy" else -1
        reservation = Decimal(price) * (1 + (shift + sign * background["urgency_bps"]) / 10000)
        rounding = ROUND_FLOOR if sign > 0 else ROUND_CEILING
        quote = int((reservation / venue["tick_minor"]).to_integral_value(rounding=rounding)) * venue["tick_minor"]
        result["limit_price_minor"] = max(bounds[0], min(bounds[1], quote))
    return result
