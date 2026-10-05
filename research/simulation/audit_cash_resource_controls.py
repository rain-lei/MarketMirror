"""Independent resource split, funding-only acceptance and initial-state audit."""
from __future__ import annotations

import copy
from fractions import Fraction

from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs


def independent_transfer(accounts, specs, assets, control):
    if (set(control) != {"numerator", "denominator"} or any(type(v) is not int for v in control.values())
            or (control["numerator"], control["denominator"]) not in {(2, 5), (1, 1), (4, 1)}
            or set(accounts) != set(specs)):
        raise ValueError("independent resource control or owner scope differs")
    changed = copy.deepcopy(accounts)
    receipts = {}
    for a in assets:
        if any(set(account["wallets"]) != set(assets) or any(type(v) is not int or v < 0 for v in account["wallets"].values())
               for account in accounts.values()):
            raise ValueError("independent cash audit requires valid separated wallets")
        b_names = sorted(n for n, s in specs.items() if s["kind"] == "background" and s["asset"] == a)
        s_names = sorted(n for n, s in specs.items() if s["kind"] == "strategy")
        if not b_names or not s_names or any(accounts[n]["wallets"][a] != 0 for n in set(accounts) - set(b_names) - set(s_names)):
            raise ValueError("independent cash owner groups differ")
        b_cash = sum(accounts[n]["wallets"][a] for n in b_names)
        s_cash = sum(accounts[n]["wallets"][a] for n in s_names)
        num, den = control["numerator"], control["denominator"]
        desired = (b_cash + s_cash) * num * b_cash // (den * s_cash + num * b_cash) if b_cash + s_cash else 0
        amount = desired - b_cash
        paid_by = s_names if amount > 0 else b_names
        received_by = b_names if amount > 0 else s_names
        transfers = {}
        if amount:
            payment = [Fraction(abs(amount) * accounts[n]["wallets"][a], sum(accounts[k]["wallets"][a] for k in paid_by)) for n in paid_by]
            p_floor = [v.numerator // v.denominator for v in payment]
            remain = abs(amount) - sum(p_floor)
            rank = sorted(range(len(paid_by)), key=lambda i: (-(payment[i] - p_floor[i]), paid_by[i]))
            for i in rank[:remain]:
                p_floor[i] += 1
            for n, v in zip(paid_by, p_floor):
                if v:
                    transfers[n] = -v
            base, residual = divmod(abs(amount), len(received_by))
            for i, n in enumerate(received_by):
                v = base + int(i < residual)
                if v:
                    transfers[n] = v
        for n, delta in transfers.items():
            changed[n]["wallets"][a] += delta
        if sum(transfers.values()) != 0 or min(account["wallets"][a] for account in changed.values()) < 0:
            raise ValueError("independent cash transfer fails finite resources")
        receipts[a] = {"background_cash_before_minor": b_cash, "strategy_cash_before_minor": s_cash,
            "background_cash_after_minor": desired, "strategy_cash_after_minor": b_cash + s_cash - desired,
            "total_cash_minor": b_cash + s_cash, "owner_transfers_minor": dict(sorted(transfers.items()))}
    return changed, receipts


def initial_state(specs, assets, base, case, control, saved_receipt):
    state = initial_from_specs(specs, assets, base, case)
    modified, receipt = independent_transfer(state["accounts"], specs, assets, control)
    expected = None if control == {"numerator": 1, "denominator": 1} else {
        "version": "fixed-total-group-cash-odds-transfer-v1", "control": control, "assets": receipt}
    if expected != saved_receipt:
        raise ValueError("saved initial cash transfer receipt differs")
    state["accounts"] = modified
    return state


def verify_initial_wealth(state, summary):
    for name, account in state["accounts"].items():
        wealth = sum(account["wallets"].values()) + sum(account["shares"][a] * state["prices"][a] for a in state["prices"])
        if summary["accounts"][name]["initial_wealth_minor"] != wealth:
            raise ValueError("cash-reallocated initial wealth differs")


def independent_funding(day, previous, specs, venue, control):
    assets = sorted(previous["prices"])
    accounts, transfers = independent_transfer(previous["accounts"], specs, assets, control)
    output, reserved = {}, []
    for a in assets:
        call = day["portfolio_auction"]["asset_calls"][a]
        rows = []
        price = previous["prices"][a]
        lot, tick, fee = venue["lot_size"], venue["tick_minor"], venue["fee_bps"]
        low = max(tick, ((price * (10000 - venue["price_band_bps"]) + 10000 * tick - 1) // (10000 * tick)) * tick)
        high = price * (10000 + venue["price_band_bps"]) // (10000 * tick) * tick
        if call["price_bounds_minor"] != [low, high] or call["price_before_minor"] != price:
            raise ValueError("independent funding-only price bounds differ")
        for order in call["orders"]:
            row = {f: order[f] for f in ("order_id", "owner", "side", "quantity", "limit_price_minor", "sequence")}
            name, limit, quantity = order["owner"], order["limit_price_minor"], order["quantity"]
            can_trade = call["execution_available"] and low <= limit <= high
            if order["side"] == "buy":
                cost = quantity * limit
                fee_exact = Fraction(cost * fee, 10000)
                requested_cost = cost + -(-fee_exact.numerator // fee_exact.denominator) if can_trade else 0
                reserve = requested_cost if accounts[name]["wallets"][a] >= requested_cost else accounts[name]["wallets"][a]
                reserved.append({"owner": name, "asset": a, "pool": a, "requested_cost_minor": requested_cost, "escrow_minor": reserve})
                affordable_lots = Fraction(reserve * 10000, limit * lot * (10000 + fee))
                maximum = affordable_lots.numerator // affordable_lots.denominator * lot
            else:
                maximum = (accounts[name]["shares"][a] if day["portfolio_auction"]["session"] > 0 else accounts[name]["sellable"][a]) // lot * lot
            row["accepted_quantity"] = min(quantity, maximum) if can_trade else 0
            rows.append(row)
        output[a] = rows
    return {"cash_transfers": {"version": "fixed-total-group-cash-odds-transfer-v1", "control": control, "assets": transfers},
            "escrows": sorted(reserved, key=lambda r: (r["owner"], r["asset"])), "orders": output}


def verify_funding(payload, day, previous, specs, venue, control):
    expected = independent_funding(day, previous, specs, venue, control)
    if payload != expected:
        raise ValueError("funding-only cash transfers, submissions or acceptance differ")
    if control == {"numerator": 1, "denominator": 1}:
        actual = {a: [{f: row[f] for f in ("order_id", "owner", "side", "quantity", "limit_price_minor", "sequence", "accepted_quantity")}
                      for row in call["orders"]] for a, call in day["portfolio_auction"]["asset_calls"].items()}
        if payload["orders"] != actual or payload["escrows"] != day["portfolio_auction"]["escrows"]:
            raise ValueError("original-cash fixed submissions do not match actual acceptance")
    return sum(len(v) for v in payload["orders"].values())
