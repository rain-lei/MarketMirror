"""Finite initial reallocation at a common price, preserving every owner's wealth."""
from copy import deepcopy
from fractions import Fraction

VERSION = "wealth-preserving-neutral-initial-allocation-v1"


def allocate(accounts, specs, assets, price, lot, mode=None):
    if mode is None:
        return accounts, None
    if mode != VERSION or type(price) is not int or price <= 0 or type(lot) is not int or lot <= 0:
        raise ValueError("explicit allocation version and positive integer price/lot required")
    if not assets or len(set(assets)) != len(assets) or set(accounts) != set(specs):
        raise ValueError("exact nonempty participant and asset scope required")
    result = deepcopy(accounts)
    wealth = {}
    for name, account in accounts.items():
        account.validate(assets)
        if set(account.wallets) != set(assets) or account.shares != account.sellable or any(q % lot for q in account.shares.values()):
            raise ValueError("allocation requires settled whole-lot shares and separated wallets")
        wealth[name] = sum(account.wallets.values()) + price * sum(account.shares.values())
        if specs[name]["kind"] == "strategy":
            weight = Fraction(str(specs[name]["parameters"]["base_weight"]))
            if not 0 <= weight <= 1:
                raise ValueError("neutral weight outside zero to one")
            target = weight * wealth[name] / (len(assets) * price * lot)
            shares = ((2 * target.numerator + target.denominator) // (2 * target.denominator)) * lot
            for asset in assets:
                delta = shares - account.shares[asset]
                result[name].wallets[asset] -= delta * price
                result[name].shares[asset] = result[name].sellable[asset] = shares
        elif specs[name]["kind"] != "background":
            raise ValueError("unsupported participant kind")
    for asset in assets:
        delta = sum(result[n].shares[asset] - accounts[n].shares[asset]
            for n in accounts if specs[n]["kind"] == "strategy")
        owners = sorted(n for n in accounts if specs[n]["kind"] == "background" and specs[n]["asset"] == asset)
        if not owners:
            raise ValueError("finite background counterparties required for every asset")
        quotient, remainder = divmod(abs(delta) // lot, len(owners))
        for index, name in enumerate(owners):
            change = (-1 if delta > 0 else 1) * (quotient + int(index < remainder)) * lot
            result[name].shares[asset] += change
            result[name].sellable[asset] += change
            result[name].wallets[asset] -= change * price
    for name, account in result.items():
        account.validate(assets)
        if account.shares != account.sellable or sum(account.wallets.values()) + price * sum(account.shares.values()) != wealth[name]:
            raise ValueError("initial allocation changed individual wealth or settlement")
    if sum(sum(a.wallets.values()) for a in accounts.values()) != sum(sum(a.wallets.values()) for a in result.values()):
        raise ValueError("initial allocation changed total cash")
    for asset in assets:
        if sum(a.shares[asset] for a in accounts.values()) != sum(a.shares[asset] for a in result.values()):
            raise ValueError("initial allocation changed total shares")
    return result, {"version": VERSION, "price_minor": price, "lot_size": lot,
        "background_target_changed": False, "individual_wealth_preserved": True,
        "cash_and_shares_preserved": True}
