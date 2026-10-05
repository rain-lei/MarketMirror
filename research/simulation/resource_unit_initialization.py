"""Explicit initial-price and endowment contracts, separate from historical rules.

The notional arm changes initial share supply. It preserves each owner's wealth
by putting whole-lot rounding residuals in that owner's cash wallet. This is an
exogenous initialization, not a trade, deposit, fee waiver or empirical fit.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from .portfolio_auction import PortfolioAuction
from .portfolio_market import initial_portfolio

VERSION = "initial-price-resource-contract-v1"
BASELINE = "uniform_100_original_resources"
FIXED = "raw_initial_fixed_cash_shares"
NOTIONAL = "raw_initial_preserve_wealth_stock_notional"
ARMS = (BASELINE, FIXED, NOTIONAL)


def validate_prices(assets, prices, venue):
    if (not isinstance(prices, dict) or set(prices) != set(assets)
            or type(venue["tick_minor"]) is not int or venue["tick_minor"] <= 0
            or any(type(p) is not int or p <= 0 or p % venue["tick_minor"] for p in prices.values())):
        raise ValueError("exact asset scope and positive integer tick-aligned initial prices required")


def first_reference_prices(rows, stocks, first_day, reference_day, tick_minor):
    """Use only the explicitly registered initial t-1 quote, never later quotes."""
    if (not isinstance(rows, list) or not stocks or len(set(stocks)) != len(stocks)
            or type(tick_minor) is not int or tick_minor <= 0 or not reference_day < first_day):
        raise ValueError("explicit initial reference calendar and complete stock scope required")
    selected = [r for r in rows if r.get("trade_date") == first_day]
    by_stock = {}
    for row in selected:
        secid = row.get("secid")
        if not isinstance(secid, str) or secid.count(".") != 1:
            raise ValueError("initial source identifier is invalid")
        stock = secid.split(".")[1]
        if stock not in stocks or stock in by_stock or row.get("execution_reference_date") != reference_day:
            raise ValueError("initial reference duplicate, future date or different stock scope")
        raw = row.get("raw_reference_close")
        if not isinstance(raw, str):
            raise ValueError("unknown initial source price cannot become a zero or default")
        try:
            value = Decimal(raw) * 100
        except InvalidOperation as exc:
            raise ValueError("initial price is not decimal source text") from exc
        if not value.is_finite() or value <= 0 or value != value.to_integral_value() or int(value) % tick_minor:
            raise ValueError("initial source price cannot be silently rounded or coerced")
        by_stock[stock] = int(value)
    if set(by_stock) != set(stocks):
        raise ValueError("incomplete initial reference cohort")
    return {s: by_stock[s] for s in stocks}


def initialize(agents, core, background, assets, case, venue, arm=None, prices=None):
    cohort, old, specs, old_wealth = initial_portfolio(agents, core, background, assets, case, venue)
    if arm is None:
        if prices is not None:
            raise ValueError("initial prices require an explicit resource arm")
        return cohort, old, specs, old_wealth, None, {a: dict(background) for a in assets}
    if arm not in ARMS:
        raise ValueError("unsupported initial resource arm")
    validate_prices(assets, prices, venue)
    original_price, lot = venue["price_start_minor"], venue["lot_size"]
    if arm == BASELINE and any(p != original_price for p in prices.values()):
        raise ValueError("original baseline cannot change an initial price")
    accounts, receipts = deepcopy(old), {}
    for name, account in accounts.items():
        if account.shares != account.sellable or any(q % lot for q in account.shares.values()):
            raise ValueError("initialization requires settled whole-lot inventories")
        residuals = dict.fromkeys(assets, 0)
        if arm == NOTIONAL:
            for asset in assets:
                stock_value = old[name].shares[asset] * original_price
                count = stock_value // (prices[asset] * lot) * lot
                residuals[asset] = stock_value - count * prices[asset]
                account.shares[asset] = account.sellable[asset] = count
                account.wallets[account.pool(asset)] += residuals[asset]
        account.validate(assets)
        wealth = sum(account.wallets.values()) + sum(account.shares[a] * prices[a] for a in assets)
        if arm in (BASELINE, NOTIONAL) and wealth != old_wealth[name]:
            raise AssertionError("wealth-preserving initialization changed an owner wealth")
        if any(not 0 <= residuals[a] < lot * prices[a] for a in assets):
            raise AssertionError("initial stock-value rounding residual exceeded one lot")
        receipts[name] = {
            "original_wallets": dict(old[name].wallets), "original_shares": dict(old[name].shares),
            "original_wealth_minor": old_wealth[name], "initial_wallets": dict(account.wallets),
            "initial_shares": dict(account.shares), "initial_wealth_minor": wealth,
            "wealth_change_minor": wealth - old_wealth[name], "stock_notional_residual_minor": residuals,
        }
    wealth = {n: r["initial_wealth_minor"] for n, r in receipts.items()}
    backgrounds = {a: dict(background) for a in assets}
    if arm == NOTIONAL:
        for asset in assets:
            owners = [n for n, s in specs.items() if s["kind"] == "background" and s["asset"] == asset]
            anchors = {accounts[n].shares[asset] for n in owners}
            if len(anchors) != 1:
                raise ValueError("initial background reference anchor must be common within each asset")
            backgrounds[asset]["initial_shares"] = anchors.pop()
    record = {
        "version": VERSION, "arm": arm, "prices_minor": dict(prices),
        "original_uniform_price_minor": original_price, "lot_size": lot, "accounts": receipts,
        "background_initial_targets": {a: backgrounds[a]["initial_shares"] for a in assets},
        "background_order_caps_and_target_offsets_held_in_shares": True,
        "aggregate_original_cash_minor": sum(sum(a.wallets.values()) for a in old.values()),
        "aggregate_initial_cash_minor": sum(sum(a.wallets.values()) for a in accounts.values()),
        "aggregate_original_shares": {a: sum(v.shares[a] for v in old.values()) for a in assets},
        "aggregate_initial_shares": {a: sum(v.shares[a] for v in accounts.values()) for a in assets},
        "cross_arm_share_supply_preserved": arm != NOTIONAL,
        "historical_rules_or_real_investor_resources_certified": False,
    }
    return cohort, accounts, specs, wealth, record, backgrounds


class InitialPricePortfolioAuction(PortfolioAuction):
    """The archived settlement engine with explicit initial prices per asset."""
    def __init__(self, accounts, assets, settings, price_tie_break="nearest_prior", initial_prices=None):
        super().__init__(accounts, assets, settings, price_tie_break)
        if initial_prices is not None:
            validate_prices(self.assets, initial_prices, settings)
            self.prices = dict(initial_prices)
            for asset in self.assets:
                self._venue(asset, self.accounts, {})
