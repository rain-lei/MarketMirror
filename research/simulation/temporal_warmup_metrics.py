"""Evaluate only Q1 after carrying the complete preperiod account state."""
from __future__ import annotations

import math


def require(value, message):
    if not value:
        raise ValueError(message)


def evaluation_accounts(trace, specs, offset):
    require(type(offset) is int and 0 < offset < len(trace), "evaluation requires a complete carried preperiod")
    opening = trace[offset - 1]["portfolio_auction"]
    assets = sorted(opening["accounts"][next(iter(specs))]["shares"])
    opening_prices = {s: opening["asset_calls"][s]["price_after_minor"] for s in assets}
    require(trace[offset]["portfolio_auction"]["prices_before_minor"] == opening_prices,
            "evaluation prices were reset instead of carried")
    start, peaks, drawdowns, breaches = {}, {}, {}, {}
    for owner, account in opening["accounts"].items():
        nav = sum(account["wallets"].values()) + sum(account["shares"][s] * opening_prices[s] for s in assets)
        require(nav > 0, "nonpositive carried opening wealth")
        start[owner] = peaks[owner] = nav
        drawdowns[owner] = 0.0
        breaches[owner] = {"risk_breach_sessions": 0, "concentration_breach_sessions": 0}
    require(set(start) == set(specs), "carried evaluation participant scope differs")
    for day in trace[offset:]:
        prices = {s: day["portfolio_auction"]["asset_calls"][s]["price_after_minor"] for s in assets}
        require(set(day["portfolio_auction"]["accounts"]) == set(specs), "evaluation participant scope changed")
        for owner, account in day["portfolio_auction"]["accounts"].items():
            nav = sum(account["wallets"].values()) + sum(account["shares"][s] * prices[s] for s in assets)
            require(nav > 0, "nonpositive evaluation wealth")
            peaks[owner] = max(peaks[owner], nav)
            drawdowns[owner] = max(drawdowns[owner], 1 - nav / peaks[owner])
            if specs[owner]["kind"] == "strategy":
                p = specs[owner]["parameters"]
                weights = {s: account["shares"][s] * prices[s] / nav for s in assets}
                risk = math.sqrt(max(0.0, sum(weights[a] * day["covariance"][a][b] * weights[b]
                                              for a in assets for b in assets)))
                breaches[owner]["risk_breach_sessions"] += int(risk > p["risk_budget"] + 1e-9
                                                               or sum(weights.values()) > p["max_weight"] + 1e-9)
                breaches[owner]["concentration_breach_sessions"] += int(any(
                    w > day["decisions"][owner]["asset_weight_cap"] + 1e-9 for w in weights.values()))
    last = trace[-1]["portfolio_auction"]
    prices = {s: last["asset_calls"][s]["price_after_minor"] for s in assets}
    accounts = {}
    for owner, account in last["accounts"].items():
        nav = sum(account["wallets"].values()) + sum(account["shares"][s] * prices[s] for s in assets)
        accounts[owner] = {"kind": specs[owner]["kind"], "role": specs[owner].get("parameters", {}).get("role"),
            **account, "opening_evaluation_wealth_minor": start[owner], "final_wealth_minor": nav,
            "evaluation_wealth_multiple": nav / start[owner], "evaluation_max_drawdown": drawdowns[owner],
            **breaches[owner]}
    return {"evaluation_sessions": len(trace) - offset, "opening_trade_date": trace[offset - 1]["trade_date"],
        "first_evaluation_date": trace[offset]["trade_date"], "last_evaluation_date": trace[-1]["trade_date"],
        "opening_prices_minor": opening_prices, "opening_fee_pool_minor": opening["fee_pool_minor"],
        "evaluation_fee_increment_minor": last["fee_pool_minor"] - opening["fee_pool_minor"], "accounts": accounts,
        "state_reset_at_evaluation_start": False,
        "interpretation": "Q1 wealth, drawdown and breach counts use carried Dec31 opening state; full 100-session summaries remain separate."}
