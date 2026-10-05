"""Fixed-total, per-asset transfers; no cash creation or same-session funding."""
from __future__ import annotations

import copy
from fractions import Fraction

VERSION = "fixed-total-group-cash-odds-transfer-v1"
CONTROLS = {
    "less_background": {"numerator": 2, "denominator": 5},
    "original_cash": {"numerator": 1, "denominator": 1},
    "more_background": {"numerator": 4, "denominator": 1},
}
SUBMISSION_FIELDS = ("order_id", "owner", "side", "quantity", "limit_price_minor", "sequence")


def validate_control(control):
    if (not isinstance(control, dict) or set(control) != {"numerator", "denominator"}
            or any(type(control[k]) is not int or control[k] <= 0 for k in control)
            or control not in CONTROLS.values()):
        raise ValueError("cash control must be an explicitly declared rational odds multiplier")


def split_amount(total, weights):
    """Integer largest-remainder split; names break equal remainders."""
    if (type(total) is not int or total < 0 or not weights or any(type(w) is not int or w < 0 for w in weights.values())
            or sum(weights.values()) == 0):
        raise ValueError("cash transfer split requires nonnegative integer amount and nonzero weights")
    denominator = sum(weights.values())
    out = {name: total * value // denominator for name, value in weights.items()}
    left = total - sum(out.values())
    ranking = sorted(weights, key=lambda name: (-(total * weights[name] % denominator), name))
    for name in ranking[:left]:
        out[name] += 1
    return out


def transfer_cash(accounts, specs, assets, control):
    """Change current cash only; donation proportional, receipt uniform by owner.

    Group target uses current background/strategy cash odds. Zero group odds
    remain zero. Donors pay no more than their wallets; receivers may start at
    zero. No original owner, shares, sellable inventory or per-asset total changes.
    """
    validate_control(control)
    names = set(accounts)
    if names != set(specs) or not assets or len(assets) != len(set(assets)):
        raise ValueError("cash control needs exact owners and unique assets")
    changed, receipt = copy.deepcopy(accounts), {}
    ratio = Fraction(control["numerator"], control["denominator"])
    for stock in sorted(assets):
        for account in accounts.values():
            if (set(account["wallets"]) != set(assets) or set(account["shares"]) != set(assets)
                    or set(account["sellable"]) != set(assets) or any(type(v) is not int or v < 0
                        for field in ("wallets", "shares", "sellable") for v in account[field].values())
                    or any(account["sellable"][a] > account["shares"][a] for a in assets)):
                raise ValueError("cash reallocation requires finite separated wallets and valid inventory")
        strategies = {n for n, s in specs.items() if s["kind"] == "strategy"}
        backgrounds = {n for n, s in specs.items() if s["kind"] == "background" and s["asset"] == stock}
        if not strategies or not backgrounds or any(s["kind"] not in {"strategy", "background"} for s in specs.values()):
            raise ValueError("cash control requires both real owner groups")
        outside = names - strategies - backgrounds
        if any(accounts[n]["wallets"][stock] != 0 for n in outside):
            raise ValueError("another-stock background holds undeclared local cash")
        b = sum(accounts[n]["wallets"][stock] for n in backgrounds)
        s = sum(accounts[n]["wallets"][stock] for n in strategies)
        target = int(Fraction(b + s) * ratio * b / (s + ratio * b)) if b + s else 0
        change = target - b
        deltas = dict.fromkeys(names, 0)
        if change:
            donor, recipient = (strategies, backgrounds) if change > 0 else (backgrounds, strategies)
            paid = split_amount(abs(change), {n: accounts[n]["wallets"][stock] for n in donor})
            received = split_amount(abs(change), dict.fromkeys(recipient, 1))
            for name, value in paid.items():
                deltas[name] -= value
            for name, value in received.items():
                deltas[name] += value
        for name, value in deltas.items():
            changed[name]["wallets"][stock] += value
        if (sum(deltas.values()) != 0 or any(a["wallets"][stock] < 0 for a in changed.values())
                or sum(a["wallets"][stock] for a in changed.values()) != b + s):
            raise AssertionError("finite cash transfer failed per-asset conservation")
        receipt[stock] = {"background_cash_before_minor": b, "strategy_cash_before_minor": s,
            "background_cash_after_minor": target, "strategy_cash_after_minor": b + s - target,
            "total_cash_minor": b + s, "owner_transfers_minor": {n: v for n, v in sorted(deltas.items()) if v}}
    return changed, receipt


def initial_portfolio_with_cash_control(initializer, agents, core, background, assets, case, venue, control):
    validate_control(control)
    if case["cash_mode"] != "separated" or case.get("initial_cash_weights") is not None:
        raise ValueError("fixed-resource experiment requires original equal separated initial wallets")
    cohort, accounts, specs, wealth = initializer(agents, core, background, assets, case, venue)
    if control == CONTROLS["original_cash"]:
        return cohort, accounts, specs, wealth, None
    raw = {n: {"wallets": a.wallets, "shares": a.shares, "sellable": a.sellable} for n, a in accounts.items()}
    changed, receipt = transfer_cash(raw, specs, assets, control)
    for name, state in changed.items():
        accounts[name].wallets = state["wallets"]
    wealth = {n: sum(a.wallets.values()) + sum(a.shares[s] * venue["price_start_minor"] for s in assets)
              for n, a in accounts.items()}
    return cohort, accounts, specs, wealth, {"version": VERSION, "control": dict(control), "assets": receipt}


def funding_only(day, previous, specs, venue, control):
    """Existing submissions held fixed; output escrows/acceptance, never fills."""
    assets = sorted(previous["prices"])
    accounts, receipt = transfer_cash(previous["accounts"], specs, assets, control)
    books, escrows = {}, []
    for stock in assets:
        call, prior = day["portfolio_auction"]["asset_calls"][stock], previous["prices"][stock]
        tick, lot = venue["tick_minor"], venue["lot_size"]
        low = max(tick, -(-(prior * (10000 - venue["price_band_bps"])) // (10000 * tick)) * tick)
        high = prior * (10000 + venue["price_band_bps"]) // (10000 * tick) * tick
        if call["price_before_minor"] != prior or call["price_bounds_minor"] != [low, high]:
            raise ValueError("funding-only source price/band differs")
        output = []
        for o in call["orders"]:
            entry = {k: o[k] for k in SUBMISSION_FIELDS}
            name, limit, quantity = o["owner"], o["limit_price_minor"], o["quantity"]
            if name not in specs or quantity <= 0 or quantity % lot or o["side"] not in ("buy", "sell"):
                raise ValueError("invalid funding-only original submitted order")
            eligible = call["execution_available"] and low <= limit <= high
            amount, cost = 0, 0
            if o["side"] == "buy":
                raw = quantity * limit
                cost = raw + -(-(raw * venue["fee_bps"]) // 10000) if eligible else 0
                amount = min(accounts[name]["wallets"][stock], cost)
                escrows.append({"owner": name, "asset": stock, "pool": stock,
                                "requested_cost_minor": cost, "escrow_minor": amount})
                accepted = min(quantity, amount * 10000 // (limit * lot * (10000 + venue["fee_bps"])) * lot) if eligible else 0
            else:
                sellable = accounts[name]["shares"][stock] if day["portfolio_auction"]["session"] else accounts[name]["sellable"][stock]
                accepted = min(quantity, sellable // lot * lot) if eligible else 0
            entry["accepted_quantity"] = accepted
            output.append(entry)
        books[stock] = output
    return {"cash_transfers": {"version": VERSION, "control": dict(control), "assets": receipt},
            "escrows": sorted(escrows, key=lambda o: (o["owner"], o["asset"])), "orders": books}
