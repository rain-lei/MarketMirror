import unittest

from research.simulation.audit_inventory_target_feedback_2019 import analyze_variant, target_shares
from research.simulation.call_auction import AuctionAccount
from research.simulation.participant_market import background_demand


class InventoryTargetFeedbackTests(unittest.TestCase):
    def test_independent_target_formula_matches_background_demand(self):
        settings = {"case_id": "active_quote25", "mode": "active", "participants": 12,
                    "initial_cash": 50000, "initial_shares": 500,
                    "target_range_lots": 4, "max_order_lots": 4,
                    "urgency_bps": 25, "seed": 7}
        venue = {"lot_size": 100, "tick_minor": 1}
        for session in (0, 7, 42):
            for person in (0, 5, 11):
                name = f"background_000001_{person:03d}"
                demand = background_demand("000001", session, name,
                                           AuctionAccount(5000000, 500, 500), 10000,
                                           (9000, 11000), venue, settings)
                self.assertEqual(target_shares("000001", session, person, settings, 100),
                                 demand["target_shares"])

    def test_daily_inventory_reconstruction(self):
        dates = ["2019-11-01", "2019-11-04"]
        rows = [{"stock_code": "000001", "trade_date": dates[0],
                 "background_requested_net": -100, "background_filled_net": 100,
                 "strategy_filled_net": -100, "filled_buy": 100, "filled_sell": 100,
                 "matched_volume": 100},
                {"stock_code": "000001", "trade_date": dates[1],
                 "background_requested_net": -200, "background_filled_net": 0,
                 "strategy_filled_net": 0, "filled_buy": 0, "filled_sell": 0,
                 "matched_volume": 0}]
        settings = {"participants": 1, "initial_shares": 500}
        result = analyze_variant(rows, ["000001"], dates, settings, 100,
                                 {("000001", dates[0]): 400, ("000001", dates[1]): 400})
        self.assertEqual(result["final_background_shares"], 600)
        self.assertEqual([day["background_shares_before"] for day in result["daily"]],
                         [500, 600])
        self.assertEqual(result["days_above_target_and_requesting_sales"], 2)


if __name__ == "__main__":
    unittest.main()
