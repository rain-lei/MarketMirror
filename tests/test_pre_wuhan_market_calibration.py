import unittest

from research.simulation.calibrate_pre_wuhan_market import accepted_order_flow, choose_codes


class PreWuhanMarketCalibrationTests(unittest.TestCase):
    def test_code_selection_is_order_invariant(self):
        selected, dropped = choose_codes(["000003", "000001", "000002"], 2)
        reverse_selected, reverse_dropped = choose_codes(["000002", "000001", "000003"], 2)
        self.assertEqual((selected, dropped), (reverse_selected, reverse_dropped))
        self.assertEqual(set(selected) | set(dropped), {"000001", "000002", "000003"})
        self.assertFalse(set(selected) & set(dropped))

    def test_invalid_selection_fails(self):
        for codes, count in ((["000001", "000001"], 1), (["000001"], 0),
                             (["000001"], 2)):
            with self.subTest(codes=codes, count=count), self.assertRaises(ValueError):
                choose_codes(codes, count)

    def test_accepted_flow_separates_strategy_and_background(self):
        call = {"matched_volume": 100,
                "orders": [{"owner": "s", "side": "buy", "quantity": 400,
                            "accepted_quantity": 300, "filled_quantity": 100,
                            "reasons": ["cash_and_fee_reservation"]},
                           {"owner": "b", "side": "sell", "quantity": 500,
                            "accepted_quantity": 500, "filled_quantity": 100, "reasons": []},
                           {"owner": "s2", "side": "sell", "quantity": 200,
                            "accepted_quantity": 100, "filled_quantity": 0,
                            "reasons": ["sellable_inventory"]}]}
        specs = {"s": {"kind": "strategy"}, "s2": {"kind": "strategy"},
                 "b": {"kind": "background"}}
        self.assertEqual(accepted_order_flow(call, specs),
                         {"accepted_buy": 300, "accepted_sell": 600,
                          "requested_buy": 400, "requested_sell": 700,
                          "filled_buy": 100, "filled_sell": 100,
                          "strategy_net": 200, "background_net": -500, "net_accepted": -300,
                          "strategy_requested_net": 200, "background_requested_net": -500,
                          "strategy_filled_net": 100, "background_filled_net": -100,
                          "cash_clipped_buy_orders": 1, "inventory_clipped_sell_orders": 1,
                          "strategy_role_flow": {"unknown": {"requested_net": 200,
                                                                 "accepted_net": 200,
                                                                 "filled_net": 100}}})


if __name__ == "__main__":
    unittest.main()
