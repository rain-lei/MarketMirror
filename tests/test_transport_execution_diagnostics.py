import unittest

from research.simulation.temporal_transport_execution_diagnostics import diagnose_call


def call(buy=100, sell=100, matched=0, before=10000, after=10000, cross=False):
    orders = []
    for side, size in [("buy", buy), ("sell", sell)]:
        if size:
            orders.append({"side": side, "quantity": size, "accepted_quantity": size,
                           "filled_quantity": matched, "reasons": [],
                           "limit_price_minor": 10000 if cross else 9900 if side == "buy" else 10100})
    return {"orders": orders, "matched_volume": matched, "price_before_minor": before,
            "price_after_minor": after, "execution_available": True}


class TransportExecutionDiagnosticsTests(unittest.TestCase):
    def test_matched_unchanged_is_not_no_liquidity(self):
        data = diagnose_call(call(matched=100, cross=True))
        self.assertEqual(data["call_category"], "matched_unchanged")
        self.assertEqual(data["quantities"]["mechanical_accepted_quantity_upper_bound"], 100)

    def test_unmatched_positive_quantities_keep_price_constraint(self):
        self.assertEqual(diagnose_call(call())["call_category"], "accepted_limits_do_not_cross")

    def test_missing_one_side_is_not_a_price_tie(self):
        self.assertEqual(diagnose_call(call(buy=0))["call_category"], "no_accepted_buy")
        self.assertEqual(diagnose_call(call(sell=0))["call_category"], "no_accepted_sell")

    def test_double_sided_fills_are_single_matched_volume(self):
        diagnostic = diagnose_call(call(buy=300, sell=100, matched=100, cross=True, after=10010))
        self.assertEqual(diagnostic["quantities"]["matched_volume"], 100)
        self.assertEqual(diagnostic["call_category"], "matched_price_changed")

    def test_insufficient_cash_reason_keeps_quantity_not_count(self):
        data = call(buy=200)
        data["orders"][0].update({"accepted_quantity": 100, "reasons": ["cash_and_fee_reservation"]})
        self.assertEqual(diagnose_call(data)["quantities"]["buy_cash_and_fee_clipped"], 100)

    def test_impossible_quantity_or_fill_symmetry_is_rejected(self):
        for changed in [{"matched_volume": 200}, {"price_after_minor": 10010}]:
            data = call()
            data.update(changed)
            with self.assertRaises(ValueError):
                diagnose_call(data)
        data = call(matched=100, cross=True)
        data["orders"][0]["filled_quantity"] = 0
        with self.assertRaises(ValueError):
            diagnose_call(data)

    def test_crossed_limits_cannot_be_diagnosed_as_no_trade(self):
        with self.assertRaises(ValueError):
            diagnose_call(call(cross=True))

    def test_halted_state_cannot_hide_execution(self):
        data = call(buy=0, sell=0)
        data["execution_available"] = False
        self.assertEqual(diagnose_call(data)["call_category"], "halted_unchanged")
        data = call(matched=100, cross=True)
        data["execution_available"] = False
        with self.assertRaises(ValueError):
            diagnose_call(data)


if __name__ == "__main__":
    unittest.main()
