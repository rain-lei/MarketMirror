"""Explicit hypothetical controls for inventory restoration and decision waits."""

from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

BASELINE = {"background_anchor": "initial_inventory", "strategy_wait": "original"}


def validate_quantity_controls(value: dict) -> None:
    if (not isinstance(value, dict) or set(value) != set(BASELINE)
            or value["background_anchor"] not in {"initial_inventory", "current_inventory"}
            or value["strategy_wait"] not in {"original", "immediate_daily"}):
        raise ValueError("quantity controls require explicit inventory anchor and strategy wait modes")


def effective_cohort(cohort: list, specs: dict, controls: dict | None) -> list:
    if controls is None or controls["strategy_wait"] == "original":
        return cohort
    result = []
    for agent, profile in cohort:
        effective = replace(agent, confirmation_steps=1, rebalance_interval=1)
        specs[agent.name]["original_wait_parameters"] = {"confirmation_steps": agent.confirmation_steps,
                                                       "rebalance_interval": agent.rebalance_interval}
        specs[agent.name]["parameters"] = asdict(effective)
        result.append((effective, profile))
    return result


def anchor_background_demand(demand: dict, price: int, bounds: tuple[int, int], venue: dict,
                            background: dict, controls: dict | None) -> dict:
    if controls is None or controls["background_anchor"] == "initial_inventory":
        return demand
    if demand["mode"] != "inventory_target" or background["mode"] != "active":
        raise ValueError("current-inventory anchor requires active target demand")
    current = demand["current_shares"]
    target = max(0, current + demand["target_offset_lots"] * venue["lot_size"])
    delta = target - current
    quantity = min(abs(delta), background["max_order_lots"] * venue["lot_size"])
    side = "buy" if delta > 0 else "sell" if delta < 0 else "hold"
    quote = None
    if quantity:
        urgency = background["urgency_bps"] if side == "buy" else -background["urgency_bps"]
        units = Decimal(price) * (1 + Decimal(urgency) / 10000) / venue["tick_minor"]
        rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
        quote = int(units.to_integral_value(rounding=rounding)) * venue["tick_minor"]
        quote = max(bounds[0], min(bounds[1], quote))
    return {**demand, "target_shares": target, "requested_quantity": quantity, "side": side, "limit_price_minor": quote,
            "inventory_anchor_response": {"reference_anchor_shares": background["initial_shares"], "applied_anchor_shares": current,
                                          "original_target_shares": demand["target_shares"],
                                          "original_requested_quantity": demand["requested_quantity"], "original_side": demand["side"],
                                          "original_limit_price_minor": demand["limit_price_minor"]}}
