import unittest

from research.simulation.portfolio_auction import PortfolioAccount, PortfolioAuction
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_wallet_diagnostic import (
    _aggregate,
    _reallocate,
    load_config,
    verify_group_summaries,
)
from research.simulation.call_auction import LimitOrder


class PortfolioWalletDiagnosticTest(unittest.TestCase):
    def test_config_rotates_both_cash_concentration_stresses(self):
        from pathlib import Path

        _, config = load_config(Path(__file__).parents[1] / "research/configs/portfolio_wallet_reallocation_2020.json")
        self.assertEqual(len(config["cash_allocations"]), 7)
        self.assertEqual(config["cash_allocations"]["asset_2_concentrated_98"], [1, 1, 98])

    def test_matched_resources_reallocation_changes_which_assets_can_buy(self):
        assets = ["A", "B", "C"]
        specs = {"buyer": {"kind": "strategy"}, **{f"seller_{a}": {"kind": "background"} for a in assets}}
        accounts = {"buyer": PortfolioAccount({"shared": 1002}, {a: 0 for a in assets}, {a: 0 for a in assets})}
        for asset in assets:
            accounts[f"seller_{asset}"] = PortfolioAccount({a: 0 for a in assets},
                {a: 20 if a == asset else 0 for a in assets}, {a: 20 if a == asset else 0 for a in assets})
        settings = {"price_start_minor": 100, "lot_size": 1, "tick_minor": 1,
                    "fee_bps": 0, "price_band_bps": 1000}
        books = {a: [LimitOrder(f"buy-{a}", "buyer", "buy", 8, 100, 0),
                     LimitOrder(f"sell-{a}", f"seller_{a}", "sell", 20, 100, 1)] for a in assets}
        outcomes = {}
        for name, weights in (("shared", None), ("equal", [1, 1, 1]), ("skewed", [98, 1, 1])):
            variant = _reallocate(accounts, specs, assets, weights)
            self.assertEqual(sum(variant["buyer"].wallets.values()), 1002)
            self.assertEqual(variant["buyer"].shares, accounts["buyer"].shares)
            market = PortfolioAuction(variant, assets, settings)
            before = {"accounts": {owner: {"wallets": dict(account.wallets), "shares": dict(account.shares),
                                             "sellable": dict(account.sellable)} for owner, account in variant.items()},
                      "prices": {a: 100 for a in assets}, "fee_pool_minor": 0, "initial_cash_minor": 1002,
                      "initial_shares": {a: 20 for a in assets}}
            result = market.clear(0, books, {a: True for a in assets})
            audit = audit_portfolio_day({"trade_date": "2020-07-03", "signal_cutoff_date": "2020-07-01",
                                         "execution_reference_date": "2020-07-02", "portfolio_auction": result},
                                        before, settings, 0)
            self.assertEqual(audit["accounts"], result["accounts"])
            outcomes[name] = {a: next(o["accepted_quantity"] for o in result["asset_calls"][a]["orders"]
                                      if o["owner"] == "buyer") for a in assets}
        self.assertEqual(outcomes["shared"], {"A": 3, "B": 3, "C": 3})
        self.assertEqual(outcomes["equal"], outcomes["shared"])
        self.assertEqual(outcomes["skewed"], {"A": 8, "B": 0, "C": 0})

    def test_group_summary_is_recomputed_from_snapshot_records(self):
        variants = ["shared", "equal"]
        records = []
        for response in (200, 500):
            for use_text in (False, True):
                outcomes = {}
                for name in variants:
                    outcomes[name] = {
                        "strategy_requested": 10,
                        "strategy_accepted": 8,
                        "strategy_filled": 5,
                        "cash_clipped_orders": 1,
                        "filled_by_asset": {"A": 2, "B": 2, "C": 1},
                        "prices_after_minor": {"A": 100, "B": 100, "C": 100},
                        "marked_strategy_nav_after_minor": 1000,
                    }
                records.append({
                    "assets": ["A", "B", "C"],
                    "quote_response_bps": response,
                    "use_text": use_text,
                    "source_cash_minor": 100,
                    "cash_allocation_totals_minor": {name: 100 for name in variants},
                    "outcomes": outcomes,
                })

        groups = _aggregate(records, variants)
        verify_group_summaries(records, variants, groups)
        groups[0]["variants"]["equal"]["strategy_filled"] += 1
        with self.assertRaisesRegex(ValueError, "summary counts differ"):
            verify_group_summaries(records, variants, groups)


if __name__ == "__main__":
    unittest.main()
