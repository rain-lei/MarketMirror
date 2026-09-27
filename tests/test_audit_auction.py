import copy
import unittest

from research.simulation.call_auction import AuctionAccount, CallAuction, LimitOrder
from research.simulation.audit_auction import audit_day


class AuditAuctionTest(unittest.TestCase):
    def record(self):
        accounts = {"buyer": AuctionAccount(10000, 0, 0), "seller": AuctionAccount(0, 10, 10)}
        venue = CallAuction(accounts, 1000, 1, 1, 0, 2000)
        result = venue.clear(0, [LimitOrder("buy", "buyer", "buy", 5, 1100, 0),
                                 LimitOrder("sell", "seller", "sell", 5, 900, 1)])
        row = {"trade_date": "2020-07-03", "signal_cutoff_date": "2020-07-01",
               "execution_reference_date": "2020-07-02", "auction": result}
        previous = {"accounts": {name: vars(account) for name, account in accounts.items()},
                    "price_minor": 1000, "fee_pool_minor": 0, "initial_cash_minor": 10000, "initial_shares": 10}
        settings = {"tick_minor": 1, "lot_size": 1, "fee_bps": 0, "price_band_bps": 2000}
        return row, previous, settings

    def test_trade_reconstruction_detects_balance_fee_and_quantity_corruption(self):
        row, previous, settings = self.record()
        checked = audit_day(row, previous, settings, 0)
        self.assertEqual(checked["accounts"], row["auction"]["accounts"])
        bad = copy.deepcopy(row)
        bad["auction"]["accounts"]["buyer"]["cash_minor"] -= 1
        with self.assertRaisesRegex(ValueError, "account"):
            audit_day(bad, previous, settings, 0)
        bad = copy.deepcopy(row)
        bad["auction"]["orders"][0]["fee_minor"] = 1
        with self.assertRaisesRegex(ValueError, "fee"):
            audit_day(bad, previous, settings, 0)
        bad = copy.deepcopy(row)
        bad["auction"]["trades"][0]["quantity"] = 6
        with self.assertRaisesRegex(ValueError, "fill"):
            audit_day(bad, previous, settings, 0)

    def test_independent_dense_sweep_rejects_a_feasible_but_wrong_price(self):
        row, previous, settings = self.record()
        row["auction"]["price_after_minor"] = 1001
        row["auction"]["trades"][0]["price_minor"] = 1001
        with self.assertRaisesRegex(ValueError, "dense tick"):
            audit_day(row, previous, settings, 0)


if __name__ == "__main__":
    unittest.main()
