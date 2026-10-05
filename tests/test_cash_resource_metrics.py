import unittest

from research.simulation.cash_resource_metrics import expected_coverage, resource_summary


def row(seed, anchor, feedback, cash, value):
    return {"seed_id": seed, "name": f"{anchor}_{feedback}_{cash}", "background_anchor": anchor,
        "feedback_scale": feedback, "cash_name": cash,
        "comparison": {"synthetic": {"mean": value, "standard_deviation": .02, "zero_return_fraction": .05}},
        "common_factor_metrics": {"synthetic": {"mean_pairwise_stock_return_correlation": None, "equal_weight_daily_return_std": .01}},
        "joint_checks": {"all_five_pass": False, "values": {"absolute_mean_gap": abs(value), "volatility_ratio": 1,
            "zero_fraction_gap": 0, "stock_correlation_gap": None, "portfolio_volatility_ratio": 1}}}


def fixture():
    return [row(s, a, f, c, {"original_cash": .001, "less_background": -.003, "more_background": .007 if f else .008}[c])
            for s in ("x", "y", "z") for a in ("initial_inventory", "current_inventory")
            for f in (1.0, 0.0) for c in ("less_background", "original_cash", "more_background")]


class CashResourceMetricsTest(unittest.TestCase):
    def test_complete_paired_effects_keep_missing_correlation_and_known_interaction(self):
        result = resource_summary(fixture())
        self.assertEqual(len(result["seed_summary"]), 12)
        for a in ("initial_inventory", "current_inventory"):
            for r in result["paired_cash_effects_and_feedback_interaction"][a]:
                self.assertAlmostEqual(r["more_background|1.0"]["mean"], .006)
                self.assertAlmostEqual(r["more_background|0.0"]["mean"], .007)
                self.assertAlmostEqual(r["more_background|interaction"]["mean"], .001)
                self.assertIsNone(r["more_background|interaction"]["stock_correlation"])
        self.assertTrue(all(r["all_five_nonworse_seeds"] == 0 for r in result["seed_summary"].values()))

    def test_missing_and_duplicate_cells_do_not_publish_partial_grid(self):
        for rows in (fixture()[:-1], fixture() + [fixture()[0]]):
            with self.assertRaises(ValueError):
                resource_summary(rows)

    def test_coverage_matches_tiny_enumeration_and_rejects_boolean_dimensions(self):
        c = expected_coverage(2, 3, 4, 2, 3, 4)
        days = [(s, a, f, cash, basket, day) for s in range(2) for a in range(2) for f in range(2)
                for cash in range(3) for basket in range(3) for day in range(4)]
        self.assertEqual(c["full_path_ledger_records"], len(days))
        self.assertEqual(c["same_state_funding_records"], sum(cash != 1 for _, _, _, cash, _, _ in days))
        self.assertEqual(c["paired_private_receipt_values"], 2 * 11 * 3 * 4 * 2 * 7)
        with self.assertRaises(ValueError):
            expected_coverage(True, 3, 4, 2, 3, 4)


if __name__ == "__main__":
    unittest.main()
