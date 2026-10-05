"""Independently audit public target/quote channels and frozen private news."""
from __future__ import annotations

import copy
import math
from fractions import Fraction

from .audit_issuer_valuation import expected_receipt
from .audit_quantity_controls import verify_strategy_allocation
from .audit_background_response import expected_demand
from .audit_issuer_valuation import verify_issuer_day
from .audit_public_market_factor import expected_message


def expected_specs(baseline, target_scale, quote_scale, public_controls=None):
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
    if public_controls is not None:
        for spec in specs.values():
            spec["public_factor_controls"] = dict(public_controls)
    return specs


def verify_specs(actual, baseline, target_scale, quote_scale, public_controls=None):
    if actual != expected_specs(baseline, target_scale, quote_scale, public_controls):
        raise ValueError("channel profile/origin changed an undeclared parameter")


def verify_strategy(day, previous, specs, venue, issuer, case, states, session,
                    target_scale, quote_scale, public_controls=None):
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
            original_target_base = parameters["market_sensitivity"] * min(1.0, max(-1.0, target_signal))
            if public_controls is not None:
                target_signal += public_controls["strategy_target_scale"] * observation["public_factor"]["applied_belief_signal"]
            target_base = parameters["market_sensitivity"] * min(1.0, max(-1.0, target_signal))
            target_private = parameters["market_sensitivity"] * min(1.0, max(-1.0, target_signal + receipt["applied_belief_signal"]))
            quote_base = parameters["market_sensitivity"] * min(1.0, max(-1.0, quote_signal))
            shift = parameters["market_sensitivity"] * receipt["applied_valuation_shift_bps"]
            decision = day["decisions"][name]
            fields = [("beliefs", target_private), ("issuer_base_beliefs", target_base),
                      ("issuer_belief_contributions", target_private - target_base), ("issuer_quote_shift_bps", shift)]
            public_shift = 0.0
            if public_controls is not None:
                public_shift = parameters["market_sensitivity"] * public_controls["quote_scale"] * observation["public_factor"]["valuation_shift_bps"]
                fields.extend((("public_target_belief_contributions", target_base - original_target_base), ("public_quote_shift_bps", public_shift)))
            elif "public_target_belief_contributions" in decision or "public_quote_shift_bps" in decision:
                raise ValueError("original path has undeclared public contributions")
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
                    + Fraction(str(shift)) + Fraction(str(public_shift))) / 10000 + Fraction((-1 if side == "buy" else 1) * venue["quote_spread_bps"], 20000))
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


def reconstruct_background(day, previous, specs, venue, background, response, shocks, industry, issuer, controls, session, public_controls):
    result = {a: {} for a in previous["prices"]}
    tick = venue["tick_minor"]
    current = controls is not None and controls["background_anchor"] == "current_inventory"
    for stock, call in day["portfolio_auction"]["asset_calls"].items():
        group = industry["path_groups"][stock]
        common, sector = shocks["common"][session], industry["by_group"][group][session]
        price, bounds = previous["prices"][stock], call["price_bounds_minor"]
        for name, spec in specs.items():
            if spec["kind"] != "background" or spec["asset"] != stock:
                continue
            shares = previous["accounts"][name]["shares"][stock]
            settings = {**background, "initial_shares": shares} if current else background
            demand = expected_demand(stock, session, name, shares, price, bounds, venue, settings, common + sector, response)
            if current:
                original = expected_demand(stock, session, name, shares, price, bounds, venue, background, 0.0,
                                           {"valuation_response_bps": 0, "pulse_participation_bps": 10000})
                demand["inventory_anchor_response"] = {"reference_anchor_shares": background["initial_shares"], "applied_anchor_shares": shares,
                    **{"original_" + k: original[k] for k in ("target_shares", "requested_quantity", "side", "limit_price_minor")}}
            if "common_news_response" in demand:
                detail = demand.pop("common_news_response")
                detail["shared_shock"] = detail.pop("common_shock")
                detail.update(common_shock=common, industry_shock=sector, industry_code=industry["membership"][stock], industry_path_group=group)
                demand["shared_news_response"] = detail
            receipt = expected_receipt(issuer["by_stock"][stock][session], issuer["parameters"], stock, name)
            if day["issuer_information_receipts"][name] != {stock: receipt}:
                raise ValueError("public experiment altered a background private receipt")
            demand["issuer_valuation_response"] = {"receipt": receipt, "base_limit_price_minor": demand["limit_price_minor"]}
            shared_shift = Fraction(str(common + sector)) * response["valuation_response_bps"]
            private_shift = Fraction(str(receipt["applied_valuation_shift_bps"]))

            def quote(shift):
                side = demand["side"]
                urgency = background["urgency_bps"] if side == "buy" else -background["urgency_bps"]
                units = Fraction(price, tick) * (1 + (shift + urgency) / 10000)
                rounded = units.numerator // units.denominator if side == "buy" else -(-units.numerator // units.denominator)
                return max(bounds[0], min(bounds[1], rounded * tick))

            if receipt["received"] and demand["requested_quantity"]:
                demand["limit_price_minor"] = quote(shared_shift + private_shift)
            if public_controls is not None and public_controls["quote_scale"]:
                message = day["observations"][stock]["public_factor"]
                applied = public_controls["quote_scale"] * message["valuation_shift_bps"]
                demand["public_factor_quote_response"] = {"base_limit_price_minor": demand["limit_price_minor"],
                    "applied_shift_bps": applied, "history_sha256": message["history_sha256"]}
                if demand["requested_quantity"]:
                    demand["limit_price_minor"] = quote(shared_shift + private_shift + Fraction(str(applied)))
            result[stock][name] = demand
    return result


def verify_day(day, previous, specs, venue, background, response, shocks, industry, issuer, controls,
               case, states, session, target_scale, quote_scale, public_path=None, public_controls=None):
    own = {"target_scale": target_scale, "quote_scale": quote_scale} if target_scale != 1 or quote_scale != 1 else None
    if (day.get("price_feedback_channels") != own or day.get("public_factor_controls") != public_controls
            or day.get("quantity_control_parameters") != controls or set(day["issuer_information_receipts"]) != set(specs)):
        raise ValueError("daily public/private channel or quantity control scope differs")
    for name, spec in specs.items():
        if spec.get("public_factor_controls") != public_controls:
            raise ValueError("participant public factor controls differ")
        scope = set(previous["prices"]) if spec["kind"] == "strategy" else {spec["asset"]}
        receipts = day["issuer_information_receipts"][name]
        if set(receipts) != scope or any(type(r["received"]) is not bool for r in receipts.values()):
            raise ValueError("private receipt asset coverage/boolean type differs")
    for stock, obs in day["observations"].items():
        expected = expected_message(public_path["by_stock"][stock][session], public_path["parameters"]) if public_controls is not None else None
        if obs.get("public_factor") != expected or expected is not None and type(obs["public_factor"]["was_capped"]) is not bool:
            raise ValueError("public message differs from independently audited lagged factor")
    # This empty-participant view checks only the original shared information.
    # Every actual decision/order is reconstructed below; settlement uses all records.
    shared = {**day, "decisions": {}, "issuer_information_receipts": {}, "background_demands": {a: {} for a in previous["prices"]},
        "portfolio_auction": {**day["portfolio_auction"], "asset_calls": {a: {**c, "orders": []} for a, c in day["portfolio_auction"]["asset_calls"].items()}}}
    verify_issuer_day(shared, previous, {}, venue, background, response, shocks, industry, issuer, session)
    counts = verify_strategy(day, previous, specs, venue, issuer, case, states, session, target_scale, quote_scale, public_controls)
    expected_demands = reconstruct_background(day, previous, specs, venue, background, response, shocks, industry, issuer, controls, session, public_controls)
    if day["background_demands"] != expected_demands:
        raise ValueError("independent public background target/physical quote differs")
    counts["background_demand_checks"] = counts["background_receipt_checks"] = 0
    counts["background_received"] = 0
    for stock, demands in expected_demands.items():
        orders = {o["owner"]: o for o in day["portfolio_auction"]["asset_calls"][stock]["orders"]}
        for name, demand in demands.items():
            order = orders.get(name)
            if demand["requested_quantity"]:
                if order is None or any(order[field] != demand[k] for field, k in (("side", "side"), ("quantity", "requested_quantity"), ("limit_price_minor", "limit_price_minor"))):
                    raise ValueError("independent public background submitted order differs")
                if order["order_id"] != f"{session}:{stock}:{name}" or order["sequence"] != day["arrival_order"].index(name):
                    raise ValueError("public background order identity differs")
            elif order is not None:
                raise ValueError("zero public background demand submitted an order")
            counts["background_demand_checks"] += 1
            counts["background_receipt_checks"] += 1
            counts["background_received"] += int(day["issuer_information_receipts"][name][stock]["received"])
    return counts


def submitted_orders(day):
    keys = ("order_id", "owner", "side", "quantity", "limit_price_minor", "sequence")
    return {a: [{k: o[k] for k in keys} for o in c["orders"]] for a, c in day["portfolio_auction"]["asset_calls"].items()}


def verify_same_state(payload, factual, previous, baseline_specs, venue, background, response, shocks, industry, issuer,
                      controls, case, streaks, session, target_scale, quote_scale, public_path, public_controls):
    if set(payload) != {"decisions", "orders"} or set(payload["orders"]) != set(previous["prices"]):
        raise ValueError("same-state public payload scope differs")
    for orders in payload["orders"].values():
        if any(set(o) != {"order_id", "owner", "side", "quantity", "limit_price_minor", "sequence"}
               or type(o["quantity"]) is not int or o["quantity"] <= 0 for o in orders):
            raise ValueError("same-state public payload cannot contain acceptance, fills or prices")
        if [o["sequence"] for o in orders] != sorted(o["sequence"] for o in orders):
            raise ValueError("same-state public order sequence differs")
    specs = copy.deepcopy(baseline_specs)
    if public_controls is not None:
        for spec in specs.values():
            spec["public_factor_controls"] = dict(public_controls)
    view = {**factual, "decisions": payload["decisions"], "observations": {a: dict(o) for a, o in factual["observations"].items()},
        "portfolio_auction": {**factual["portfolio_auction"], "asset_calls": {a: {**c, "orders": payload["orders"][a]} for a, c in factual["portfolio_auction"]["asset_calls"].items()}}}
    if public_controls is not None:
        view["public_factor_controls"] = dict(public_controls)
        for a, obs in view["observations"].items():
            obs["public_factor"] = expected_message(public_path["by_stock"][a][session], public_path["parameters"])
    view["background_demands"] = reconstruct_background(view, previous, specs, venue, background, response, shocks, industry, issuer, controls, session, public_controls)
    counts = verify_day(view, previous, specs, venue, background, response, shocks, industry, issuer, controls, case,
                        copy.deepcopy(streaks), session, target_scale, quote_scale, public_path if public_controls else None, public_controls)
    if public_controls is None:
        if payload["decisions"] != factual["decisions"] or payload["orders"] != submitted_orders(factual):
            raise ValueError("same-state public none cell differs from complete factual submissions")
    elif public_controls["strategy_target_scale"] == 0:
        targets = {n: {k: v for k, v in d.items() if k not in {"public_target_belief_contributions", "public_quote_shift_bps"}} for n, d in payload["decisions"].items()}
        if targets != factual["decisions"]:
            raise ValueError("quote-only public channel altered target decisions")
        counts["quote_only_same_state_strategy_targets"] = len(targets)
    elif public_controls["quote_scale"] == 0:
        if view["background_demands"] != factual["background_demands"]:
            raise ValueError("target-only public channel altered background demand")
        for a, orders in payload["orders"].items():
            actual = [o for o in orders if specs[o["owner"]]["kind"] == "background"]
            original = [o for o in submitted_orders(factual)[a] if specs[o["owner"]]["kind"] == "background"]
            if actual != original:
                raise ValueError("target-only public channel altered background submitted orders")
        counts["target_only_same_state_backgrounds"] = sum(s["kind"] == "background" for s in specs.values())
    return counts
