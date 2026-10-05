import unittest
from copy import deepcopy

from research.simulation.audit_pre_wuhan_shock_transmission_2019 import index_rows, paired_group, validate_flow


class PreWuhanShockTransmissionTest(unittest.TestCase):
    def row(self):
        return {"stock_code": "000001", "trade_date": "2019-11-15", "signal_cutoff_date": "2019-11-13",
                "price_before_minor": 10000, "price_after_minor": 10000, "observed_return": 0.01,
                "requested_buy": 1000, "requested_sell": 1000, "accepted_buy": 1000, "accepted_sell": 1000,
                "filled_buy": 800, "filled_sell": 800, "matched_volume": 800,
                "strategy_requested_net": 0, "background_requested_net": 0,
                "strategy_net": 0, "background_net": 0, "net_accepted": 0,
                "strategy_filled_net": 0, "background_filled_net": 0,
                "strategy_role_flow": {}, "cash_clipped_buy_orders": 0, "inventory_clipped_sell_orders": 0}

    def test_role_reallocation_is_visible_even_when_aggregate_net_is_unchanged(self):
        row = self.row()
        changed = deepcopy(row)
        changed["strategy_role_flow"] = {
            "aggressive": {"requested_net": 100, "accepted_net": 100, "filled_net": 0},
            "conservative": {"requested_net": -100, "accepted_net": -100, "filled_net": 0},
        }
        baseline, candidate = index_rows([row]), index_rows([changed])
        group = paired_group(baseline, candidate, list(baseline))
        self.assertEqual(group["changed_days"]["strategy_requested_net"], 0)
        self.assertEqual(group["strategy_role_request_vector_changed_days"], 1)
        self.assertEqual(group["strategy_role_fill_vector_changed_days"], 0)

    def test_double_entry_fills_and_role_sums_are_required(self):
        invalid_fill = self.row()
        invalid_fill["filled_sell"] = 700
        with self.assertRaisesRegex(ValueError, "balance"):
            validate_flow(invalid_fill)
        invalid_roles = self.row()
        invalid_roles["strategy_role_flow"] = {"aggressive": {"requested_net": 100, "accepted_net": 0, "filled_net": 0}}
        with self.assertRaisesRegex(ValueError, "role flow"):
            validate_flow(invalid_roles)

    def test_pairing_rejects_missing_duplicate_or_misaligned_keys(self):
        rows = index_rows([self.row()])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            index_rows([self.row(), self.row()])
        with self.assertRaisesRegex(ValueError, "identical"):
            paired_group(rows, {}, list(rows))
        with self.assertRaisesRegex(ValueError, "unique"):
            paired_group(rows, rows, list(rows) * 2)

    def test_price_level_change_is_separate_from_one_step_return_change(self):
        row = self.row()
        row["price_after_minor"] = 10100
        changed = {**row, "price_before_minor": 20000, "price_after_minor": 20200}
        baseline, candidate = index_rows([row]), index_rows([changed])
        group = paired_group(baseline, candidate, list(baseline))
        self.assertEqual(group["price_level_changed_days"], 1)
        self.assertEqual(group["step_return_changed_days"], 0)
        self.assertEqual(group["mean_absolute_step_return_difference_bps"], 0)


if __name__ == "__main__":
    unittest.main()
