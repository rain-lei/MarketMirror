"""Independent decimal reconstruction for the finite neutral endowment audit."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs


def initial_allocated_state(specs, assets, design, case):
    state = initial_from_specs(specs, assets, design, case)
    original = deepcopy(state["accounts"])
    accounts = state["accounts"]
    price, lot = design["venue"]["price_start_minor"], design["venue"]["lot_size"]
    strategies = [n for n, sp in specs.items() if sp["kind"] == "strategy"]
    for name in strategies:
        account = accounts[name]
        wealth = sum(account["wallets"].values()) + sum(account["shares"].values()) * price
        weight = Decimal(str(specs[name]["parameters"]["base_weight"]))
        lots = (weight * wealth / Decimal(len(assets) * price * lot)).to_integral_value(rounding=ROUND_HALF_UP)
        amount = int(lots) * lot
        for asset in assets:
            account["wallets"][asset] += (account["shares"][asset] - amount) * price
            account["shares"][asset] = account["sellable"][asset] = amount
    for asset in assets:
        owners = sorted(n for n, sp in specs.items() if sp["kind"] == "background" and sp["asset"] == asset)
        if not owners:
            raise ValueError("independent initial audit requires finite counterparties")
        remaining = sum(original[n]["shares"][asset] - accounts[n]["shares"][asset] for n in strategies)
        sign = 1 if remaining >= 0 else -1
        units = abs(remaining) // lot
        for index, name in enumerate(owners):
            count = units // len(owners) + int(index < units % len(owners))
            transfer = sign * count * lot
            accounts[name]["wallets"][asset] -= transfer * price
            accounts[name]["shares"][asset] += transfer
            accounts[name]["sellable"][asset] += transfer
    for name, account in accounts.items():
        if any(type(v) is not int or v < 0 for d in account.values() for v in d.values()):
            raise ValueError("independent allocation has negative or noninteger resources")
        if account["shares"] != account["sellable"] or any(v % lot for v in account["shares"].values()):
            raise ValueError("independent allocation settlement or lot mismatch")
        old = original[name]
        if sum(account["wallets"].values()) + price * sum(account["shares"].values()) != sum(old["wallets"].values()) + price * sum(old["shares"].values()):
            raise ValueError("independent individual wealth mismatch")
    if sum(sum(a["wallets"].values()) for a in accounts.values()) != state["initial_cash_minor"]:
        raise ValueError("independent aggregate cash mismatch")
    for asset in assets:
        if sum(a["shares"][asset] for a in accounts.values()) != state["initial_shares"][asset]:
            raise ValueError("independent aggregate shares mismatch")
    return state
