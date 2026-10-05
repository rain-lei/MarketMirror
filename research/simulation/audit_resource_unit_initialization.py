"""Independent Fraction reconstruction; never imports the resource producer."""
from fractions import Fraction
import gzip
import json

from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs


def reconstruct(specs, assets, design, case, arm, prices):
    if arm not in ("uniform_100_original_resources", "raw_initial_fixed_cash_shares",
                   "raw_initial_preserve_wealth_stock_notional"):
        raise ValueError("independent initial audit arm differs")
    old = initial_from_specs(specs, assets, design, case)
    p0, lot, tick = (design["venue"][k] for k in ("price_start_minor", "lot_size", "tick_minor"))
    if set(prices) != set(assets) or any(type(p) is not int or p <= 0 or p % tick for p in prices.values()):
        raise ValueError("independent initial price scope or units differ")
    if arm == "uniform_100_original_resources" and set(prices.values()) != {p0}:
        raise ValueError("independent uniform baseline prices differ")
    accounts, receipts = {}, {}
    for owner, base in old["accounts"].items():
        wallets, quantities, remaining = dict(base["wallets"]), dict(base["shares"]), {}
        for asset in assets:
            remaining[asset] = 0
            if arm == "raw_initial_preserve_wealth_stock_notional":
                value = quantities[asset] * p0
                lots = Fraction(value, prices[asset] * lot)
                amount = (lots.numerator // lots.denominator) * lot
                remaining[asset] = value - amount * prices[asset]
                wallets[asset] += remaining[asset]
                quantities[asset] = amount
        original_wealth = sum(base["wallets"].values()) + sum(base["shares"][a] * p0 for a in assets)
        initial_wealth = sum(wallets.values()) + sum(quantities[a] * prices[a] for a in assets)
        if arm != "raw_initial_fixed_cash_shares" and original_wealth != initial_wealth:
            raise ValueError("independent individual initial wealth not preserved")
        if any(not 0 <= remaining[a] < prices[a] * lot for a in assets):
            raise ValueError("independent initial cash residual exceeds one lot")
        accounts[owner] = {"wallets": wallets, "shares": quantities, "sellable": dict(quantities)}
        receipts[owner] = {"original_wallets": base["wallets"], "original_shares": base["shares"],
            "original_wealth_minor": original_wealth, "initial_wallets": wallets,
            "initial_shares": quantities, "initial_wealth_minor": initial_wealth,
            "wealth_change_minor": initial_wealth-original_wealth,
            "stock_notional_residual_minor": remaining}
    state = {"accounts": accounts, "prices": dict(prices), "fee_pool_minor": 0,
        "initial_cash_minor": sum(sum(a["wallets"].values()) for a in accounts.values()),
        "initial_shares": {a: sum(v["shares"][a] for v in accounts.values()) for a in assets}}
    backgrounds = {a: dict(design["background"]) for a in assets}
    if arm == "raw_initial_preserve_wealth_stock_notional":
        for asset in assets:
            value = design["background"]["initial_shares"] * p0
            share_lots = Fraction(value, lot * prices[asset])
            backgrounds[asset]["initial_shares"] = share_lots.numerator // share_lots.denominator * lot
    record = {"version": "initial-price-resource-contract-v1", "arm": arm,
        "prices_minor": dict(prices), "original_uniform_price_minor": p0, "lot_size": lot,
        "accounts": receipts, "background_initial_targets": {a: backgrounds[a]["initial_shares"] for a in assets},
        "background_order_caps_and_target_offsets_held_in_shares": True,
        "aggregate_original_cash_minor": old["initial_cash_minor"],
        "aggregate_initial_cash_minor": state["initial_cash_minor"],
        "aggregate_original_shares": old["initial_shares"], "aggregate_initial_shares": state["initial_shares"],
        "cross_arm_share_supply_preserved": arm != "raw_initial_preserve_wealth_stock_notional",
        "historical_rules_or_real_investor_resources_certified": False}
    return state, record, backgrounds


def verify_initialization(saved, specs, assets, design, case, arm, prices):
    state, record, backgrounds = reconstruct(specs, assets, design, case, arm, prices)
    if saved != record:
        raise ValueError("independent complete resource initialization receipt differs")
    return state, backgrounds


def independent_initial_quotes(source_path, stocks, secids, reference_day, expected_prices, tick):
    """Reopen original unadjusted carriers, separate from preflight/producer."""
    actual = {}
    with gzip.open(source_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if (row.get("kind") != "stock" or row.get("fqt") != 0
                    or row.get("trade_date") != reference_day or row.get("secid") not in secids.values()):
                continue
            stock = row["secid"].split(".")[1]
            if stock in actual or stock not in stocks or row["secid"] != secids[stock]:
                raise ValueError("original quote duplicate or source identity mismatch")
            text = row["provider_fields"].get("f53")
            if not isinstance(text, str):
                raise ValueError("original initial source quote unknown")
            price = Fraction(text) * 100
            if price.denominator != 1 or price <= 0 or price.numerator % tick:
                raise ValueError("original source quote not an exact positive price minor unit")
            actual[stock] = price.numerator
    if set(actual) != set(stocks) or actual != expected_prices:
        raise ValueError("complete independent original initial quote reconstruction differs")
    return len(actual)
