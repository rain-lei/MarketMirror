"""Independently audit quantity rules, allocation and issuer news under controls."""

from __future__ import annotations

import math
from fractions import Fraction

from .audit_background_response import expected_demand
from .audit_issuer_valuation import expected_receipt, verify_issuer_day


def verify_strategy_allocation(day: dict, previous: dict, specs: dict, case: dict, states: dict) -> int:
    """Reconstruct complete target allocation from audited beliefs and prior funds."""
    stocks = sorted(previous["prices"])
    checked = 0
    for name, spec in specs.items():
        if spec["kind"] != "strategy":
            continue
        p, decision, account = spec["parameters"], day["decisions"][name], previous["accounts"][name]
        nav = sum(account["wallets"].values()) + sum(account["shares"][stock] * previous["prices"][stock] for stock in stocks)
        current = {stock: account["shares"][stock] * previous["prices"][stock] / nav for stock in stocks}
        beliefs = decision["beliefs"]  # Independently checked by the private-news audit below.
        score = sum(beliefs.values()) / len(stocks)
        sign = (score > 1e-12) - (score < -1e-12)
        old = states[name]
        streak = old["streak"] + 1 if sign and sign == old["sign"] else 1 if sign else 0
        states[name] = {"streak": streak, "sign": sign}
        maximum = max(beliefs.values())
        exponent = {stock: math.exp(beliefs[stock] - maximum) for stock in stocks}
        denominator = sum(exponent.values())
        mix = {stock: exponent[stock] / denominator for stock in stocks}
        cov = day["covariance"]

        def risk(weights):
            return math.sqrt(max(0.0, sum(weights[left] * cov[left][right] * weights[right] for left in stocks for right in stocks)))

        mix_risk = risk(mix)
        cap = min(p["max_weight"], p["risk_budget"] / max(1e-6, mix_risk))
        asset_cap = case["institutional_asset_cap"] if p["role"] == "institutional" else 1.0
        total = min(cap, max(0.0, p["base_weight"] + 0.4 * score))
        target = {stock: min(asset_cap, total * mix[stock]) for stock in stocks}
        current_risk = risk(current)
        liquidation = sum(current.values()) > cap + 1e-12 or any(value > asset_cap + 1e-12 for value in current.values()) or current_risk > p["risk_budget"] + 1e-12
        reasons = ["portfolio_risk", "institutional_concentration" if p["role"] == "institutional" else "portfolio_allocation"]
        if liquidation:
            target = {stock: min(target[stock], current[stock]) for stock in stocks}
            reasons.append("risk_liquidation_priority")
        elif (streak < p["confirmation_steps"] and sign) or day["portfolio_auction"]["session"] % p["rebalance_interval"]:
            target = dict(current)
            reasons.append("confirmation_or_rebalance_wait")
        target_risk = risk(target)
        if target_risk > p["risk_budget"]:
            target = {stock: value * p["risk_budget"] / target_risk for stock, value in target.items()}
            reasons.append("actual_portfolio_risk_cap")
        delta = {stock: target[stock] - current[stock] for stock in stocks}
        turnover = sum(abs(value) for value in delta.values())
        if not liquidation and turnover < p["min_trade_weight"]:
            delta = dict.fromkeys(stocks, 0.0)
            reasons.append("minimum_portfolio_trade")
        elif not liquidation and turnover > p["max_turnover"]:
            delta = {stock: value * p["max_turnover"] / turnover for stock, value in delta.items()}
            reasons.append("portfolio_turnover_cap")
        expected = {"nav_minor": nav, "current_weights": current, "desired_weights": target, "order_weight_changes": delta,
                    "risk_weight_cap": cap, "portfolio_volatility": mix_risk, "asset_weight_cap": asset_cap,
                    "current_portfolio_risk": current_risk, "desired_portfolio_risk": risk(target), "risk_liquidation": liquidation,
                    "reasons": reasons}
        for field, value in expected.items():
            actual = decision[field]
            if isinstance(value, dict):
                if set(actual) != set(value) or any(not math.isclose(actual[stock], value[stock], rel_tol=0.0, abs_tol=1e-12) for stock in stocks):
                    raise ValueError("independent strategy allocation differs")
            elif isinstance(value, (bool, list)):
                if actual != value:
                    raise ValueError("independent strategy risk flag or wait reasons differ")
            elif not math.isclose(actual, value, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("independent strategy risk or wealth differs")
        checked += 1
    return checked


def verify_quantity_day(day: dict, previous: dict, specs: dict, venue: dict, background: dict, response: dict,
                        shocks: dict, industry: dict, issuer: dict, controls: dict | None, case: dict,
                        states: dict, session: int) -> dict:
    # A projection of the actual strategy records lets the existing independent
    # private-message auditor verify that component unchanged. All background
    # records are separately reconstructed below, and settlement audits use the
    # complete, unprojected auction.
    strategy_specs = {name: spec for name, spec in specs.items() if spec["kind"] == "strategy"}
    view = {**day, "issuer_information_receipts": {name: day["issuer_information_receipts"][name] for name in strategy_specs},
            "background_demands": {stock: {} for stock in day["observations"]},
            "portfolio_auction": {**day["portfolio_auction"], "asset_calls": {
                stock: {**call, "orders": [order for order in call["orders"] if order["owner"] in strategy_specs]}
                for stock, call in day["portfolio_auction"]["asset_calls"].items()}}}
    counts = verify_issuer_day(view, previous, strategy_specs, venue, background, response, shocks, industry, issuer, session)
    counts["strategy_allocation_checks"] = verify_strategy_allocation(day, previous, specs, case, states)
    current_anchor = controls is not None and controls["background_anchor"] == "current_inventory"
    tick = venue["tick_minor"]
    for stock, call in day["portfolio_auction"]["asset_calls"].items():
        names = {name for name, spec in specs.items() if spec["kind"] == "background" and spec["asset"] == stock}
        if set(day["background_demands"][stock]) != names:
            raise ValueError("quantity background participant coverage differs")
        orders = {order["owner"]: order for order in call["orders"]}
        price = previous["prices"][stock]
        group = industry["path_groups"][stock]
        common, sector = shocks["common"][session], industry["by_group"][group][session]
        for name in names:
            shares = previous["accounts"][name]["shares"][stock]
            settings = {**background, "initial_shares": shares} if current_anchor else background
            expected = expected_demand(stock, session, name, shares, price, call["price_bounds_minor"], venue, settings, common + sector, response)
            if current_anchor:
                original = expected_demand(stock, session, name, shares, price, call["price_bounds_minor"], venue, background, 0.0,
                                           {"valuation_response_bps": 0, "pulse_participation_bps": 10000})
                expected["inventory_anchor_response"] = {"reference_anchor_shares": background["initial_shares"], "applied_anchor_shares": shares,
                                                         "original_target_shares": original["target_shares"], "original_requested_quantity": original["requested_quantity"],
                                                         "original_side": original["side"], "original_limit_price_minor": original["limit_price_minor"]}
            if "common_news_response" in expected:
                detail = expected.pop("common_news_response")
                detail["shared_shock"] = detail.pop("common_shock")
                detail.update(common_shock=common, industry_shock=sector, industry_code=industry["membership"][stock], industry_path_group=group)
                expected["shared_news_response"] = detail
            receipt = expected_receipt(issuer["by_stock"][stock][session], issuer["parameters"], stock, name)
            if day["issuer_information_receipts"][name] != {stock: receipt}:
                raise ValueError("quantity background private receipt differs")
            expected["issuer_valuation_response"] = {"receipt": receipt, "base_limit_price_minor": expected["limit_price_minor"]}
            if receipt["received"] and expected["requested_quantity"]:
                shift = Fraction(str(common + sector)) * response["valuation_response_bps"] + Fraction(str(receipt["applied_valuation_shift_bps"]))
                side = expected["side"]
                urgency = background["urgency_bps"] if side == "buy" else -background["urgency_bps"]
                units = Fraction(price, tick) * (1 + (shift + urgency) / 10000)
                rounded = units.numerator // units.denominator if side == "buy" else -(-units.numerator // units.denominator)
                expected["limit_price_minor"] = max(call["price_bounds_minor"][0], min(call["price_bounds_minor"][1], rounded * tick))
            if day["background_demands"][stock][name] != expected:
                raise ValueError("inventory anchor demand differs from independent target/quote reconstruction")
            if expected["requested_quantity"]:
                order = orders.get(name)
                if order is None or any(order[field] != expected[key] for field, key in (("side", "side"), ("quantity", "requested_quantity"), ("limit_price_minor", "limit_price_minor"))):
                    raise ValueError("quantity background submitted order differs")
            elif name in orders:
                raise ValueError("zero inventory target demand submitted an order")
            counts["background_demand_checks"] += 1
            counts["background_receipt_checks"] += 1
            counts["background_received"] += int(receipt["received"])
    if set(day["issuer_information_receipts"]) != set(specs):
        raise ValueError("quantity private receipt owner coverage differs")
    return counts
