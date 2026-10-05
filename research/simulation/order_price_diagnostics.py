"""Read-only accepted-book diagnostics; no new orders, fills or price rules."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict

VERSION = "accepted-book-order-to-price-diagnostics-v1"
ORDER_FIELDS = ("owner", "role", "profile", "branch", "received", "side", "requested",
                "accepted", "filled", "quote_minus_prior_minor", "eligible_at_prior",
                "eligible_at_clear", "cash_clipped", "inventory_clipped")
ROLES = ("aggressive", "conservative", "institutional", "background")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def diagnose_book(call, tick, specs, decisions, receipts, stock):
    """Sweep every constant-demand/supply interval, including interior ticks."""
    prior, chosen = call["price_before_minor"], call["price_after_minor"]
    lower, upper = call["price_bounds_minor"]
    if (type(tick) is not int or tick <= 0 or any(type(p) is not int or p <= 0 or p % tick
            for p in (prior, chosen, lower, upper)) or not lower <= prior <= upper
            or not lower <= chosen <= upper or type(call["execution_available"]) is not bool):
        raise ValueError("diagnostic price grid or execution availability differs")
    events, demand = defaultdict(lambda: [0, 0]), 0
    decoded, names, ids = [], set(), set()
    for order in call["orders"]:
        owner, side, limit = order["owner"], order["side"], order["limit_price_minor"]
        requested, accepted, filled = [order[k] for k in ("quantity", "accepted_quantity", "filled_quantity")]
        if (owner not in specs or owner in names or order["order_id"] in ids or side not in ("buy", "sell")
                or any(type(v) is not int or v < 0 for v in (requested, accepted, filled))
                or requested == 0 or not filled <= accepted <= requested
                or type(limit) is not int or limit <= 0 or limit % tick
                or accepted and (not call["execution_available"] or not lower <= limit <= upper)):
            raise ValueError("diagnostic order identity, quantities or accepted band differs")
        names.add(owner)
        ids.add(order["order_id"])
        if accepted:
            if side == "buy":
                demand += accepted
                events[limit + tick][0] -= accepted
            else:
                events[limit][1] += accepted
        spec = specs[owner]
        if spec["kind"] == "strategy":
            role, profile = spec["parameters"]["role"], int(owner.rsplit("_", 1)[1])
            decision = decisions[owner]
            branch = ("risk_liquidation" if decision["risk_liquidation"] else
                      "wait" if "confirmation_or_rebalance_wait" in decision["reasons"] else "ordinary")
        elif spec["kind"] == "background" and spec["asset"] == stock:
            role, profile, branch = "background", None, "inventory_target"
        else:
            raise ValueError("diagnostic participant kind or asset differs")
        received = receipts[owner][stock]["received"]
        if role not in ROLES or type(received) is not bool:
            raise ValueError("diagnostic role or receipt flag differs")
        at_prior = limit >= prior if side == "buy" else limit <= prior
        at_clear = limit >= chosen if side == "buy" else limit <= chosen
        decoded.append(dict(zip(ORDER_FIELDS, (owner, role, profile, branch, received, side,
            requested, accepted, filled, limit - prior, at_prior, at_clear,
            "cash_and_fee_reservation" in order["reasons"], "sellable_inventory" in order["reasons"]))))
    boundaries = sorted({lower, upper + tick} | {p for p in events if lower <= p <= upper})
    intervals, supply = [], 0
    for left, right_start in zip(boundaries, boundaries[1:]):
        change = events.get(left, (0, 0))
        demand, supply = demand + change[0], supply + change[1]
        intervals.append({"lower_minor": left, "upper_minor": right_start - tick,
                          "demand": demand, "supply": supply})
    maximum = max(min(r["demand"], r["supply"]) for r in intervals)
    minimum = min(abs(r["demand"] - r["supply"]) for r in intervals
                  if min(r["demand"], r["supply"]) == maximum)
    optimal = [r for r in intervals if (min(r["demand"], r["supply"]),
                abs(r["demand"] - r["supply"])) == (maximum, minimum)]
    candidates = [max(r["lower_minor"], min(prior, r["upper_minor"])) for r in optimal]
    expected = min(candidates, key=lambda p: (abs(p - prior), p)) if maximum else prior
    pstate = next(r for r in intervals if r["lower_minor"] <= prior <= r["upper_minor"])
    cstate = next(r for r in intervals if r["lower_minor"] <= chosen <= r["upper_minor"])
    prior_volume = min(pstate["demand"], pstate["supply"])
    if (chosen != expected or call["matched_volume"] != maximum
            or call["clearing_imbalance"] != cstate["demand"] - cstate["supply"]
            or sum(o["filled"] for o in decoded if o["side"] == "buy") != maximum
            or sum(o["filled"] for o in decoded if o["side"] == "sell") != maximum):
        raise ValueError("accepted book does not explain the saved clearing result")
    direction = "down" if chosen < prior else "up" if chosen > prior else "flat"
    if not call["execution_available"]:
        criterion = "halted"
    elif maximum == 0:
        criterion = "no_match"
    elif chosen == prior:
        criterion = "prior_optimal"
    elif maximum > prior_volume:
        criterion = "volume_requires_move"
    elif minimum < abs(pstate["demand"] - pstate["supply"]):
        criterion = "imbalance_requires_move"
    else:
        raise ValueError("a moved nearest-prior price has no first or second objective gain")
    if maximum == 0:
        geometry = "not_applicable_no_match"
    elif any(r["lower_minor"] <= prior <= r["upper_minor"] for r in optimal):
        geometry = "prior_in_optimal_set"
    else:
        below = any(r["upper_minor"] < prior for r in optimal)
        above = any(r["lower_minor"] > prior for r in optimal)
        geometry = "both_sides" if below and above else "only_below" if below else "only_above"
    equal_distance = (maximum > 0 and chosen != prior and any(
        p != chosen and abs(p - prior) == abs(chosen - prior) for p in candidates))
    return {"source_call_sha256": digest(call), "prior_minor": prior, "clearing_minor": chosen,
            "direction": direction, "criterion": criterion, "optimal_geometry": geometry,
            "lower_price_equidistant_tie": equal_distance,
            "maximum_volume": maximum, "prior_volume": prior_volume,
            "prior_demand": pstate["demand"], "prior_supply": pstate["supply"],
            "clearing_demand": cstate["demand"], "clearing_supply": cstate["supply"],
            "minimum_abs_imbalance": minimum, "optimal_intervals": optimal,
            "execution_available": call["execution_available"], "orders": decoded}


def order_metrics(order):
    side, accepted = order["side"], order["accepted"]
    values = {"orders_" + side: 1}
    for stage in ("requested", "accepted", "filled"):
        values[stage + "_" + side] = order[stage]
    region = "below" if order["quote_minus_prior_minor"] < 0 else "above" if order["quote_minus_prior_minor"] > 0 else "equal"
    values["accepted_" + side + "_quote_" + region] = accepted
    values["requested_" + side + "_quote_" + region] = order["requested"]
    if order["eligible_at_prior"]:
        values["eligible_at_prior_" + side] = accepted
    if order["eligible_at_clear"]:
        values["eligible_at_clear_" + side] = accepted
    if order["eligible_at_clear"] and not order["eligible_at_prior"]:
        values["entering_" + side] = accepted
    if order["eligible_at_prior"] and not order["eligible_at_clear"]:
        values["exiting_" + side] = accepted
    for reason in ("cash", "inventory"):
        if order[reason + "_clipped"]:
            values[reason + "_clipped_" + side] = order["requested"] - accepted
    return Counter({k: v for k, v in values.items() if v})


def summarize(rows):
    """All conditions retained; conditional return masses are arithmetic, not causes."""
    groups = {}
    for row in rows:
        key = row["seed_id"] + "|" + row["variant"]
        if key not in groups:
            groups[key] = {"books": 0, "directions": Counter(), "criteria": Counter(),
                "geometry": Counter(), "equidistant_lower_ties": 0, "returns": [],
                "return_categories": defaultdict(list), "role_orders": defaultdict(Counter),
                "role_direction_orders": defaultdict(Counter), "order_contexts": defaultdict(Counter),
                "net_accepted_by_direction": Counter(), "net_requested_by_direction": Counter(),
                "quote_mass": defaultdict(list), "session_phases": {}, "matched_volume": 0}
        g, d = groups[key], row["diagnostic"]
        direction, criterion, geometry = d["direction"], d["criterion"], d["optimal_geometry"]
        change = (d["clearing_minor"] - d["prior_minor"]) / d["prior_minor"]
        g["books"] += 1
        g["directions"][direction] += 1
        g["criteria"][criterion + "|" + direction] += 1
        g["geometry"][geometry + "|" + direction] += 1
        g["equidistant_lower_ties"] += d["lower_price_equidistant_tie"]
        g["returns"].append(change)
        g["return_categories"][criterion + "|" + direction].append(change)
        g["matched_volume"] += d["maximum_volume"]
        phase = "initial" if row["session"] == 0 else "warmup_1_9" if row["session"] < 10 else "later_10_42"
        if phase not in g["session_phases"]:
            g["session_phases"][phase] = {"books": 0, "returns": [], "criteria": Counter(), "orders": Counter()}
        p = g["session_phases"][phase]
        p["books"] += 1
        p["returns"].append(change)
        p["criteria"][criterion + "|" + direction] += 1
        net = Counter()
        for order in d["orders"]:
            role, side = order["role"], order["side"]
            metrics = order_metrics(order)
            g["role_orders"][role].update(metrics)
            g["role_direction_orders"][direction + "|" + role].update(metrics)
            context = "|".join(map(str, (direction, role, order["profile"], order["branch"], order["received"])))
            g["order_contexts"][context].update(metrics)
            p["orders"].update(metrics)
            for stage in ("requested", "accepted"):
                net[stage] += (1 if side == "buy" else -1) * order[stage]
            if order["accepted"]:
                g["quote_mass"][role + "|" + side].append(
                    order["quote_minus_prior_minor"] * order["accepted"] / d["prior_minor"])
        for stage in ("requested", "accepted"):
            sign = "buy_excess" if net[stage] > 0 else "sell_excess" if net[stage] < 0 else "balanced"
            g["net_" + stage + "_by_direction"][direction + "|" + sign] += 1
    for g in groups.values():
        g["mean_return"] = math.fsum(g.pop("returns")) / g["books"]
        g["return_categories"] = {k: {"books": len(v), "conditional_mean": math.fsum(v) / len(v),
            "contribution_to_all_book_mean": math.fsum(v) / g["books"]} for k, v in g["return_categories"].items()}
        g["accepted_quantity_weighted_quote_return"] = {
            k: math.fsum(v) / g["role_orders"][k.split("|")[0]]["accepted_" + k.split("|")[1]]
            for k, v in g.pop("quote_mass").items()}
        for p in g["session_phases"].values():
            p["mean_return"] = math.fsum(p.pop("returns")) / p["books"]
        for field in ("directions", "criteria", "geometry", "net_requested_by_direction", "net_accepted_by_direction"):
            g[field] = dict(g[field])
        for field in ("role_orders", "role_direction_orders", "order_contexts"):
            g[field] = {k: dict(v) for k, v in g[field].items()}
        for p in g["session_phases"].values():
            p["criteria"], p["orders"] = dict(p["criteria"]), dict(p["orders"])
    return groups
