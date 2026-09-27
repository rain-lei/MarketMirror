"""Reconstruct shared wallets and each asset's bilateral settlement independently."""
from __future__ import annotations

import copy
from fractions import Fraction

from .audit_background import reconstruct_day
from .audit_auction import audit_day as dense_audit_day

VERSION = "portfolio-wallet-reconstruction-v1"


def audit_portfolio_day(record, previous, settings, session, dense=False):
    saved = record["portfolio_auction"]
    assets = sorted(previous["prices"])
    before = copy.deepcopy(previous["accounts"])
    if session:
        for a in before.values():
            a["sellable"] = dict(a["shares"])
    if (saved["session"] != session or saved["prices_before_minor"] != previous["prices"]
            or set(saved["asset_calls"]) != set(assets) or set(saved["accounts"]) != set(before)
            or saved["initial_cash_minor"] != previous["initial_cash_minor"]
            or saved["initial_shares"] != previous["initial_shares"]):
        raise ValueError("portfolio asset coverage, prior state or initial resources differ")
    orders, costs, pools = {}, {}, {}
    for asset, call in saved["asset_calls"].items():
        p, tick = previous["prices"][asset], settings["tick_minor"]
        low = max(tick, -(-(p * (10000 - settings["price_band_bps"])) // (10000 * tick)) * tick)
        high = p * (10000 + settings["price_band_bps"]) // (10000 * tick) * tick
        if call["price_before_minor"] != p:
            raise ValueError("asset price differs from portfolio prior price")
        for o in call["orders"]:
            if o["owner"] not in before:
                raise ValueError("unknown portfolio owner")
            if o["side"] != "buy":
                continue
            key = (o["owner"], asset)
            if key in orders:
                raise ValueError("duplicate portfolio buy")
            orders[key] = o
            eligible = call["execution_available"] and low <= o["limit_price_minor"] <= high
            raw = o["quantity"] * o["limit_price_minor"]
            quotient, remainder = divmod(raw * settings["fee_bps"], 10000)
            costs[key] = raw + quotient + bool(remainder) if eligible else 0
            pool = "shared" if "shared" in before[o["owner"]]["wallets"] else asset
            pools.setdefault((o["owner"], pool), []).append(key)
    amounts = {}
    for (name, pool), keys in pools.items():
        total = sum(costs[k] for k in keys)
        cash = before[name]["wallets"][pool]
        for key in keys:
            exact = Fraction(costs[key]) if total <= cash else Fraction(cash) * Fraction(costs[key], total)
            amounts[key] = exact.numerator // exact.denominator
    expected_escrows = [{"owner": n, "asset": a, "pool": "shared" if "shared" in before[n]["wallets"] else a,
                         "requested_cost_minor": costs[n, a], "escrow_minor": amounts[n, a]} for n, a in sorted(amounts)]
    if saved["escrows"] != expected_escrows:
        raise ValueError("portfolio escrow differs from independent wallet budget")
    after, prices = copy.deepcopy(before), {}
    for (n, a), amount in amounts.items():
        pool = "shared" if "shared" in after[n]["wallets"] else a
        after[n]["wallets"][pool] -= amount
    fee = previous["fee_pool_minor"]
    for asset in assets:
        local = {n: {"cash_minor": amounts.get((n, asset), 0), "shares": a["shares"][asset],
                     "sellable_shares": a["sellable"][asset]} for n, a in before.items()}
        prior = {"accounts": local, "price_minor": previous["prices"][asset], "fee_pool_minor": 0,
                 "initial_cash_minor": sum(a["cash_minor"] for a in local.values()), "initial_shares": previous["initial_shares"][asset]}
        day = {k: record[k] for k in ("trade_date", "signal_cutoff_date", "execution_reference_date")}
        day["auction"] = saved["asset_calls"][asset]
        rebuilt = reconstruct_day(day, prior, settings, 0)
        if dense and dense_audit_day(day, prior, settings, 0) != rebuilt:
            raise ValueError("portfolio interval and full-tick audits differ")
        prices[asset] = rebuilt["price_minor"]
        fee += rebuilt["fee_pool_minor"]
        for n, account in rebuilt["accounts"].items():
            pool = "shared" if "shared" in after[n]["wallets"] else asset
            after[n]["wallets"][pool] += account["cash_minor"]
            after[n]["shares"][asset], after[n]["sellable"][asset] = account["shares"], account["sellable_shares"]
    if (saved["accounts"] != after or saved["fee_pool_minor"] != fee
            or sum(sum(a["wallets"].values()) for a in after.values()) + fee != previous["initial_cash_minor"]
            or any(sum(a["shares"][s] for a in after.values()) != previous["initial_shares"][s] for s in assets)):
        raise ValueError("portfolio wallets or inventories differ from bilateral reconstruction")
    return {"accounts": after, "prices": prices, "fee_pool_minor": fee,
            "initial_cash_minor": previous["initial_cash_minor"], "initial_shares": previous["initial_shares"]}
