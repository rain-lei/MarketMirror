import copy
import unittest

from research.simulation.own_price_feedback_metrics import DEFAULT_LIMITS, joint_checks, metrics, seed_summary
from research.simulation.verify_pre_wuhan_own_price_feedback_2019 import independent_metrics


def rows_for(scale=1.0, flat_stock=False):
    values = {"000001": [0.02, -0.01, 0.03, -0.02, 0.01, -0.03],
              "000002": [0.01, 0.02, -0.01, 0.03, -0.02, -0.03],
              "000003": [0.0] * 6 if flat_stock else [0.03, -0.02, 0.01, -0.01, -0.03, 0.02]}
    return [{"stock_code": code, "trade_date": f"2019-11-{i + 1:02}", "price_before_minor": 10000,
             "price_after_minor": 10000 + int(round(value * scale * 10000)), "observed_return": value}
            for code, series in values.items() for i, value in enumerate(series)]


class OwnPriceFeedbackMetricsTest(unittest.TestCase):
    def test_identical_prices_jointly_match_all_five_descriptive_properties(self):
        result = metrics(rows_for(), DEFAULT_LIMITS)
        self.assertTrue(result["joint_checks"]["all_five_pass"])
        self.assertTrue(all(result["joint_checks"]["criteria"].values()))

    def test_mean_near_zero_does_not_pass_when_prices_are_flat(self):
        result = metrics(rows_for(0), DEFAULT_LIMITS)
        self.assertTrue(result["joint_checks"]["criteria"]["mean"])
        self.assertFalse(result["joint_checks"]["all_five_pass"])
        self.assertFalse(result["joint_checks"]["criteria"]["volatility"])
        self.assertFalse(result["joint_checks"]["criteria"]["stock_correlation"])
        self.assertIsNone(result["joint_checks"]["values"]["stock_correlation_gap"])

    def test_separate_arithmetic_matches_and_keeps_undefined_correlations(self):
        rows = rows_for(0.5, True)
        producer = metrics(rows, DEFAULT_LIMITS)
        measured = independent_metrics(rows, ["000001", "000002", "000003"], [f"2019-11-{i + 1:02}" for i in range(6)], DEFAULT_LIMITS)
        self.assertAlmostEqual(producer["comparison"]["synthetic"]["standard_deviation"], measured["comparison"]["synthetic"]["standard_deviation"])
        self.assertEqual(producer["common_factor_metrics"]["synthetic"]["pairwise_correlations_defined"], 1)
        self.assertEqual(measured["common_factor_metrics"]["synthetic"]["pairwise_correlations_defined"], 1)
        self.assertEqual(producer["joint_checks"]["criteria"], measured["joint_criteria"])

    def test_seed_summaries_keep_each_pair_and_missing_correlation_cannot_be_nonworse(self):
        variants = []
        for seed in ("first", "second", "third"):
            for label, scale in (("full", 1.0), ("off", 0.0)):
                variants.append({"seed_id": seed, "name": "initial_inventory_feedback_" + label,
                                 "background_anchor": "initial_inventory", **metrics(rows_for(scale), DEFAULT_LIMITS)})
        result = seed_summary(variants)
        self.assertEqual(result["initial_inventory_feedback_full"]["all_five_nonworse_seeds"], 3)
        self.assertEqual(result["initial_inventory_feedback_off"]["all_five_nonworse_seeds"], 0)
        self.assertEqual([row["seed_id"] for row in result["initial_inventory_feedback_off"]["paired_changes"]], ["first", "second", "third"])

    def test_independent_panel_rejects_missing_or_duplicate_company_dates(self):
        rows = rows_for()
        for bad in (rows[:-1], rows + [copy.deepcopy(rows[0])]):
            with self.assertRaisesRegex(ValueError, "incomplete/duplicate"):
                independent_metrics(bad, ["000001", "000002", "000003"], [f"2019-11-{i + 1:02}" for i in range(6)], DEFAULT_LIMITS)


if __name__ == "__main__":
    unittest.main()
