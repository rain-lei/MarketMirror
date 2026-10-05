"""Read realized model-path risk and execution diagnostics from archived ledgers."""
from __future__ import annotations

import math
import statistics
from collections import Counter

ROLES = ("aggressive", "conservative", "institutional")
ROLE_COUNT_FIELDS = ("account_steps", "post_close_risk_breach_steps", "post_close_concentration_breach_steps",
    "risk_liquidation_requested_steps", "confirmation_or_rebalance_wait_steps", "orders", "requested_quantity",
    "accepted_quantity", "filled_quantity", "fee_minor", "cash_clipped_orders", "cash_clipped_quantity",
    "accepted_unfilled_quantity", "buy_requested_quantity", "sell_requested_quantity", "signed_filled_quantity")


def ratio(numerator, denominator):
    if denominator < 0 or numerator < 0:
        raise ValueError("nonnegative counts required")
    return numerator / denominator if denominator else None


def level_metrics(levels):
    if len(levels) < 3 or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in levels):
        raise ValueError("positive finite initial and closing values required")
    returns = [current / previous - 1 for previous, current in zip(levels, levels[1:])]
    peak, drawdown = levels[0], 0.
    for value in levels[1:]:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    return {"total_return": levels[-1] / levels[0] - 1,
            "step_return_sample_stdev": statistics.stdev(returns), "max_close_drawdown": drawdown,
            "steps": len(returns)}


def derive_path_metrics(raw):
    """Compute model performance with initial value included; no return targets.

    Risk uses the archived contemporaneous covariance and role budgets. It is
    an internal constraint diagnostic, not an estimate of real-market VaR.
    """
    result = raw["model_result"]
    specs, summary, trace = result["participant_specs"], result["summary"], result["trace"]
    assets = sorted(summary["assets"])
    if len(trace) < 2 or len(assets) != 3 or len(specs) != 48 or len(trace) != summary["sessions"]:
        raise ValueError("complete registered three-asset path and all accounts required")
    owners = sorted(specs)
    strategy = [n for n in owners if specs[n]["kind"] == "strategy"]
    if len(strategy) != 12 or any(sum(specs[n]["parameters"]["role"] == r for n in strategy) != 4 for r in ROLES):
        raise ValueError("complete four-copy role scope required")
    levels = {n: [summary["accounts"][n]["initial_wealth_minor"]] for n in owners}
    role_levels = {role: [sum(levels[n][0] for n in strategy if specs[n]["parameters"]["role"] == role)] for role in ROLES}
    account_counts = {n: Counter() for n in strategy}
    role_counts, market_counts = {r: Counter({k: 0 for k in ROLE_COUNT_FIELDS}) for r in ROLES}, Counter()
    maxima = {r: {"single_asset_wealth_weight": 0., "total_stock_wealth_weight": 0., "covariance_portfolio_risk": 0.} for r in ROLES}
    market_returns = []
    previous_prices = trace[0]["portfolio_auction"]["prices_before_minor"]
    previous_fee_pool = 0
    for step, day in enumerate(trace):
        auction = day["portfolio_auction"]
        if auction["session"] != step or auction["prices_before_minor"] != previous_prices or set(auction["accounts"]) != set(owners):
            raise ValueError("complete path state sequence required")
        prices = {a: auction["asset_calls"][a]["price_after_minor"] for a in assets}
        if any(type(v) is not int or v <= 0 for v in prices.values()):
            raise ValueError("positive integer closing prices required")
        market_returns.append(sum(prices[a] / previous_prices[a] - 1 for a in assets) / len(assets))
        for owner in owners:
            account = auction["accounts"][owner]
            if set(account["shares"]) != set(assets) or set(account["sellable"]) != set(assets):
                raise ValueError("complete account positions required")
            cash = sum(account["wallets"].values())
            if (any(type(v) is not int or v < 0 for v in account["wallets"].values())
                    or any(type(q) is not int or q < 0 for q in account["shares"].values())):
                raise ValueError("nonnegative ledger resources required")
            nav = cash + sum(account["shares"][a] * prices[a] for a in assets)
            if nav <= 0:
                raise ValueError("positive account value required")
            levels[owner].append(nav)
            if owner not in strategy:
                continue
            role, parameters = specs[owner]["parameters"]["role"], specs[owner]["parameters"]
            weights = {a: account["shares"][a] * prices[a] / nav for a in assets}
            cov = day["covariance"]
            if any(set(cov[a]) != set(assets) or any(not math.isfinite(v) for v in cov[a].values()) for a in assets):
                raise ValueError("finite aligned covariance required")
            risk = math.sqrt(max(0., sum(weights[a] * cov[a][b] * weights[b] for a in assets for b in assets)))
            risk_breach = risk > parameters["risk_budget"] + 1e-9 or sum(weights.values()) > parameters["max_weight"] + 1e-9
            concentration_breach = any(w > day["decisions"][owner]["asset_weight_cap"] + 1e-9 for w in weights.values())
            account_counts[owner].update(risk_breach_sessions=int(risk_breach), concentration_breach_sessions=int(concentration_breach))
            role_counts[role].update(account_steps=1, post_close_risk_breach_steps=int(risk_breach),
                post_close_concentration_breach_steps=int(concentration_breach),
                risk_liquidation_requested_steps=int(day["decisions"][owner]["risk_liquidation"]),
                confirmation_or_rebalance_wait_steps=int("confirmation_or_rebalance_wait" in day["decisions"][owner]["reasons"]))
            maxima[role]["single_asset_wealth_weight"] = max(maxima[role]["single_asset_wealth_weight"], max(weights.values()))
            maxima[role]["total_stock_wealth_weight"] = max(maxima[role]["total_stock_wealth_weight"], sum(weights.values()))
            maxima[role]["covariance_portfolio_risk"] = max(maxima[role]["covariance_portfolio_risk"], risk)
        for role in ROLES:
            role_levels[role].append(sum(levels[n][-1] for n in strategy if specs[n]["parameters"]["role"] == role))
        step_fees = 0
        for asset in assets:
            call = auction["asset_calls"][asset]
            flat = prices[asset] == previous_prices[asset]
            market_counts.update(asset_steps=1, unavailable_asset_steps=int(not call["execution_available"]),
                flat_asset_steps=int(flat), matched_but_flat_asset_steps=int(flat and call["matched_volume"] > 0),
                matched_volume=call["matched_volume"])
            for order in call["orders"]:
                requested, accepted, filled = (order[k] for k in ("quantity", "accepted_quantity", "filled_quantity"))
                if (any(type(v) is not int for v in (requested, accepted, filled, order["fee_minor"]))
                        or not (0 <= filled <= accepted <= requested)
                        or order["side"] not in ("buy", "sell") or order["fee_minor"] < 0):
                    raise ValueError("valid direction and accepted/filled quantities required")
                step_fees += order["fee_minor"]
                if specs[order["owner"]]["kind"] != "strategy":
                    continue
                role = specs[order["owner"]]["parameters"]["role"]
                clipped = "cash_and_fee_reservation" in order["reasons"]
                role_counts[role].update(orders=1, requested_quantity=requested, accepted_quantity=accepted,
                    filled_quantity=filled, fee_minor=order["fee_minor"], cash_clipped_orders=int(clipped),
                    cash_clipped_quantity=requested - accepted if clipped else 0,
                    accepted_unfilled_quantity=accepted - filled,
                    buy_requested_quantity=requested if order["side"] == "buy" else 0,
                    sell_requested_quantity=requested if order["side"] == "sell" else 0,
                    signed_filled_quantity=filled if order["side"] == "buy" else -filled)
        if auction["fee_pool_minor"] - previous_fee_pool != step_fees:
            raise ValueError("fee increments differ from order ledger")
        previous_fee_pool, previous_prices = auction["fee_pool_minor"], prices
    for owner in owners:
        saved = summary["accounts"][owner]
        derived = level_metrics(levels[owner])
        if levels[owner][-1] != saved["final_wealth_minor"] or abs((1 + derived["total_return"]) - saved["wealth_multiple"]) > 1e-12:
            raise ValueError("saved final wealth differs from every closing ledger")
        if abs(derived["max_close_drawdown"] - saved["max_drawdown"]) > 1e-12:
            raise ValueError("saved drawdown excludes or changes an original closing value")
        if owner in strategy and any(account_counts[owner][k] != saved[k] for k in ("risk_breach_sessions", "concentration_breach_sessions")):
            raise ValueError("saved realized risk-breach count differs")
    all_counts = sum(role_counts.values(), Counter())
    for key, field in (("requested_quantity", "strategy_requested"), ("accepted_quantity", "strategy_accepted"),
                       ("filled_quantity", "strategy_filled"), ("cash_clipped_orders", "cash_clipped_orders")):
        if all_counts[key] != summary[field]:
            raise ValueError("saved strategy execution summary differs: " + field)
    if previous_fee_pool != summary["fee_pool_minor"] or previous_prices != summary["final_prices_minor"]:
        raise ValueError("saved closing venue state differs")
    roles = {}
    for role in ROLES:
        counts = dict(role_counts[role])
        performance = level_metrics(role_levels[role])
        if abs(1 + performance["total_return"] - summary["role_wealth_multiple"][role]) > 1e-12:
            raise ValueError("role-level wealth reconstruction differs")
        roles[role] = {**performance, "maxima": maxima[role], "counts": counts,
            "filled_given_accepted": ratio(counts["filled_quantity"], counts["accepted_quantity"]),
            "filled_given_requested": ratio(counts["filled_quantity"], counts["requested_quantity"]),
            "cash_clipped_quantity_fraction": ratio(counts["cash_clipped_quantity"], counts["requested_quantity"])}
    return {"case_id": raw["case_id"], "seed": raw["seed"], "roles": roles,
        "market": {"step_equal_weight_return_sample_stdev": statistics.stdev(market_returns),
            "flat_asset_step_fraction": ratio(market_counts["flat_asset_steps"], market_counts["asset_steps"]),
            "counts": dict(market_counts), "fee_pool_minor": previous_fee_pool},
        "scope": {"paths": 1, "portfolio_steps": len(trace), "asset_calls": len(trace) * len(assets),
                  "account_closes": len(trace) * len(owners), "strategy_account_closes": len(trace) * len(strategy),
                  "final_accounts": len(owners)},
        "performance_is_model_diagnostic_not_real_prediction": True}
