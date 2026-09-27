"""Atomic multi-asset calls with finite shared or separated cash wallets.

All buy escrows are fixed before any asset clears. Same-session sales cannot
finance another asset; unused escrow and proceeds return after all calls.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass

from .call_auction import AuctionAccount, CallAuction, LimitOrder, fee_minor

VERSION = "finite-portfolio-auction-v1"


@dataclass
class PortfolioAccount:
    wallets: dict[str, int]
    shares: dict[str, int]
    sellable: dict[str, int]

    def validate(self, assets):
        if (set(self.wallets) not in ({"shared"}, set(assets)) or set(self.shares) != set(assets)
                or set(self.sellable) != set(assets) or any(type(v) is not int or v < 0
                for d in (self.wallets, self.shares, self.sellable) for v in d.values())
                or any(self.sellable[a] > self.shares[a] for a in assets)):
            raise ValueError("portfolio accounts require nonnegative integer wallets and exact asset inventories")

    def pool(self, asset):
        return "shared" if "shared" in self.wallets else asset


class PortfolioAuction:
    def __init__(self, accounts, assets, settings):
        if (not isinstance(assets, list) or not assets or len(assets) != len(set(assets))
                or "shared" in assets or any(not isinstance(a, str) or not a for a in assets)
                or not accounts or any(not isinstance(n, str) or not n for n in accounts)):
            raise ValueError("portfolio market requires unique assets and named accounts")
        self.assets, self.settings = sorted(assets), dict(settings)
        for account in accounts.values():
            account.validate(self.assets)
        self.accounts = copy.deepcopy(accounts)
        self.prices = {a: settings["price_start_minor"] for a in self.assets}
        # Validate all exchange settings without changing the archived engine.
        for asset in self.assets:
            self._venue(asset, self.accounts, {})
        self.session, self.fee_pool_minor = -1, 0
        self.initial_cash_minor = sum(sum(a.wallets.values()) for a in accounts.values())
        self.initial_shares = {asset: sum(a.shares[asset] for a in accounts.values()) for asset in self.assets}

    def _venue(self, asset, accounts, escrow):
        s = self.settings
        return CallAuction({n: AuctionAccount(escrow.get((n, asset), 0), a.shares[asset], a.sellable[asset])
                            for n, a in accounts.items()}, self.prices[asset], s["lot_size"], s["tick_minor"],
                           s["fee_bps"], s["price_band_bps"])

    def clear(self, session, books, available):
        if (type(session) is not int or session != self.session + 1 or set(books) != set(self.assets)
                or set(available) != set(self.assets) or any(type(v) is not bool for v in available.values())):
            raise ValueError("portfolio session, asset books or availability invalid")
        before = copy.deepcopy(self.accounts)
        if session:
            for a in before.values():
                a.sellable = dict(a.shares)
        bounds = {}
        for asset in self.assets:
            venue = self._venue(asset, before, {})
            venue._validate_orders(books[asset])
            bounds[asset] = venue.price_bounds()
        costs, groups = {}, {}
        for asset in self.assets:
            for order in books[asset]:
                if order.side != "buy":
                    continue
                eligible = available[asset] and bounds[asset][0] <= order.limit_price_minor <= bounds[asset][1]
                notional = order.quantity * order.limit_price_minor
                cost = notional + fee_minor(notional, self.settings["fee_bps"]) if eligible else 0
                costs[order.owner, asset] = cost
                groups.setdefault((order.owner, before[order.owner].pool(asset)), []).append(asset)
        escrows = {}
        for (name, pool), assets in groups.items():
            total = sum(costs[name, asset] for asset in assets)
            cash = before[name].wallets[pool]
            for asset in assets:
                cost = costs[name, asset]
                # Proportional monetary escrow; floor residual stays in the wallet.
                escrows[name, asset] = cost if total <= cash else cash * cost // total
        after = copy.deepcopy(before)
        for (name, asset), amount in escrows.items():
            after[name].wallets[after[name].pool(asset)] -= amount
        results, fees = {}, 0
        for asset in self.assets:
            venue = self._venue(asset, before, escrows)
            results[asset] = venue.clear(0, books[asset], available[asset])
            fees += venue.fee_pool_minor
            for name, local in venue.accounts.items():
                a = after[name]
                a.wallets[a.pool(asset)] += local.cash_minor
                a.shares[asset], a.sellable[asset] = local.shares, local.sellable_shares
        for a in after.values():
            a.validate(self.assets)
        if (sum(sum(a.wallets.values()) for a in after.values()) + self.fee_pool_minor + fees != self.initial_cash_minor
                or any(sum(a.shares[asset] for a in after.values()) != self.initial_shares[asset] for asset in self.assets)):
            raise AssertionError("portfolio cash, fees or per-asset shares did not conserve")
        saved = {"pipeline_version": VERSION, "session": session, "prices_before_minor": dict(self.prices),
                 "asset_calls": results, "escrows": [{"owner": n, "asset": a, "pool": before[n].pool(a),
                     "requested_cost_minor": costs[n, a], "escrow_minor": escrows[n, a]} for n, a in sorted(escrows)],
                 "accounts": {n: asdict(a) for n, a in after.items()}, "fee_pool_minor": self.fee_pool_minor + fees,
                 "initial_cash_minor": self.initial_cash_minor, "initial_shares": self.initial_shares}
        self.accounts, self.session, self.fee_pool_minor = after, session, self.fee_pool_minor + fees
        self.prices = {a: results[a]["price_after_minor"] for a in self.assets}
        return saved
