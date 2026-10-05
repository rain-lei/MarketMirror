import copy
import unittest

from research.simulation.price_feedback_channel_metrics import expected_coverage, factorial_summary


def cells():
    return [{"name": f"{anchor}_{target}_{quote}", "background_anchor": anchor,
             "target_scale": target, "quote_scale": quote}
            for anchor in ("initial_inventory", "current_inventory")
            for target, quote in ((1.0, 1.0), (0.0, 1.0), (1.0, 0.0), (0.0, 0.0))]


def rows():
    values = {(1.0, 1.0): 1, (0.0, 1.0): 2, (1.0, 0.0): 3, (0.0, 0.0): 7}
    return [{"seed_id": seed, **cell,
             "comparison": {"synthetic": {"mean": values[cell["target_scale"], cell["quote_scale"]],
                                             "standard_deviation": 0.01, "zero_return_fraction": 0.2}},
             "common_factor_metrics": {"synthetic": {"mean_pairwise_stock_return_correlation": 0.3,
                                                       "equal_weight_daily_return_std": 0.01}}}
            for seed in ("a", "b") for cell in cells()]


class PriceFeedbackChannelMetricsTest(unittest.TestCase):
    def test_paired_factorial_arithmetic_preserves_nonadditive_interaction(self):
        result = factorial_summary(rows())
        for paired in result.values():
            self.assertEqual([row["seed_id"] for row in paired], ["a", "b"])
            for row in paired:
                self.assertEqual(row["target_off_minus_baseline"]["mean"], 1)
                self.assertEqual(row["quote_off_minus_baseline"]["mean"], 2)
                self.assertEqual(row["both_off_minus_baseline"]["mean"], 6)
                self.assertEqual(row["interaction"]["mean"], 3)

    def test_missing_correlations_are_retained_without_removing_other_metrics(self):
        data = rows()
        data[1]["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"] = None
        result = factorial_summary(data)["initial_inventory"][0]
        self.assertIsNone(result["interaction"]["stock_correlation"])
        self.assertEqual(result["interaction"]["mean"], 3)

    def test_missing_or_duplicate_factorial_cells_are_rejected(self):
        data = rows()
        for bad in (data[:-1], data + [copy.deepcopy(data[0])]):
            with self.assertRaisesRegex(ValueError, "complete unique"):
                factorial_summary(bad)

    def test_full_five_seed_counts_are_derived_and_agree_with_small_independent_enumeration(self):
        actual = expected_coverage(5, cells(), 41, 43, 12, 3, 12)
        self.assertEqual(actual["full_path_ledger_records"], 70520)
        self.assertEqual(actual["paired_private_receipt_values"], 4442760)
        self.assertEqual(actual["same_state_strategy_allocations_audited"], 846240)
        tiny = expected_coverage(2, cells(), 2, 3, 2, 2, 1)
        enumerated = sum(1 for _seed in range(2) for _cell in cells() for _basket in range(2) for _day in range(3))
        self.assertEqual(tiny["full_path_ledger_records"], enumerated)
        baseline = expected_coverage(1, cells(), 41, 43, 12, 3, 12)
        for key, value in actual.items():
            self.assertEqual(value, baseline[key] * 5)

    def test_incomplete_or_unscoped_control_grid_and_bad_dimensions_are_rejected(self):
        data = cells()
        bad = copy.deepcopy(data)
        bad[1]["target_scale"] = 0.5
        boolean = copy.deepcopy(data)
        boolean[0]["target_scale"] = True
        for wrong in (data[:-1], bad, boolean):
            with self.assertRaisesRegex(ValueError, "full binary"):
                expected_coverage(5, wrong, 41, 43, 12, 3, 12)
        for wrong in (True, 0, -1):
            with self.assertRaisesRegex(ValueError, "positive integers"):
                expected_coverage(wrong, data, 41, 43, 12, 3, 12)


if __name__ == "__main__":
    unittest.main()
