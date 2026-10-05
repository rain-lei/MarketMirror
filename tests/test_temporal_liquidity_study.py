import copy
import unittest

from research.simulation.temporal_liquidity_study import ARMS, CELLS, summarize


def rows():
    seeds = [{"seed_id": str(i)} for i in range(5)]
    result = []
    # Analytic two-factor fixture: +1 liquidity, +2 price rule, +4 interaction.
    for seed in seeds:
        for cell in CELLS:
            for arm, value in zip(ARMS, (0., 1., 2., 7.)):
                result.append({**cell, **arm, "seed_id": seed["seed_id"],
                    "comparison": {"synthetic": {"mean": value, "standard_deviation": value + 1, "zero_return_fraction": value / 10}},
                    "common_factor_metrics": {"synthetic": {"mean_pairwise_stock_return_correlation": value / 10, "equal_weight_daily_return_std": value + 1}},
                    "joint_checks": {"all_five_pass": False, "criteria": {"mean": False},
                        "values": {"absolute_mean_gap": value, "volatility_ratio": value + 1, "zero_fraction_gap": value / 10,
                            "stock_correlation_gap": value / 10, "portfolio_volatility_ratio": value + 1}}})
    return result, seeds


class TemporalLiquidityFactorialTests(unittest.TestCase):
    def test_whole_grid_keeps_original_baseline_and_reports_analytic_interaction(self):
        data, seeds = rows()
        result = summarize(data, seeds)
        self.assertEqual(result["full_factorial_conditions"], 320)
        self.assertEqual(len(result["paired_effects"]), 320)
        self.assertEqual(len(result["interactions"]), 80)
        self.assertEqual(result["interactions"][0]["metric_interaction"]["mean"], 4.)
        self.assertEqual(result["interactions"][0]["gap_interaction"]["mean"], 4.)
        self.assertEqual([r["metric_changes"]["mean"] for r in result["paired_effects"][:4]], [1., 5., 2., 6.])

    def test_omitted_original_baseline_and_duplicate_successful_candidate_are_rejected(self):
        data, seeds = rows()
        missing = [r for r in data if r["arm"] != ARMS[0]["arm"]]
        with self.assertRaises(ValueError):
            summarize(missing, seeds)
        changed = copy.deepcopy(data)
        changed[-1] = copy.deepcopy(changed[-2])
        with self.assertRaises(ValueError):
            summarize(changed, seeds)


if __name__ == "__main__":
    unittest.main()
