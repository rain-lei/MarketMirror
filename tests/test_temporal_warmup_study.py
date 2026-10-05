import copy
import unittest

from research.simulation.temporal_warmup_study import CELLS, summarize


def fixture():
    seeds = [{"seed_id": str(i)} for i in range(5)]
    rows = []
    for seed in seeds:
        for cell in CELLS:
            rows.append({**cell, "seed_id": seed["seed_id"],
                "comparison": {"synthetic": {"mean": .001, "standard_deviation": .02, "zero_return_fraction": .03}},
                "common_factor_metrics": {"synthetic": {"mean_pairwise_stock_return_correlation": .2, "equal_weight_daily_return_std": .01}},
                "joint_checks": {"all_five_pass": False, "values": {"absolute_mean_gap": .002, "volatility_ratio": .8,
                    "zero_fraction_gap": .01, "stock_correlation_gap": .02, "portfolio_volatility_ratio": .8}}})
    return rows, seeds


class TemporalWarmupStudyTests(unittest.TestCase):
    def test_every_original_pair_is_kept_and_undefined_correlation_cannot_pass(self):
        cold, seeds = fixture()
        warm = copy.deepcopy(cold)
        for row in warm:
            row["comparison"]["synthetic"]["mean"] = .002
        warm[-1]["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"] = None
        warm[-1]["joint_checks"]["values"]["stock_correlation_gap"] = None
        result = summarize(warm, cold, seeds)
        self.assertEqual(len(result["paired_warmup_effects"]), 80)
        self.assertAlmostEqual(result["paired_warmup_effects"][0]["metric_changes"]["mean"], .001)
        self.assertTrue(result["paired_warmup_effects"][0]["all_five_gaps_nonworse"])
        self.assertIsNone(result["paired_warmup_effects"][-1]["metric_changes"]["stock_correlation"])
        self.assertFalse(result["paired_warmup_effects"][-1]["all_five_gaps_nonworse"])

    def test_omitting_cold_baseline_or_duplicating_candidate_is_rejected(self):
        cold, seeds = fixture()
        with self.assertRaises(ValueError):
            summarize(cold, cold[:-1], seeds)
        duplicated = copy.deepcopy(cold)
        duplicated[-1] = copy.deepcopy(duplicated[-2])
        with self.assertRaises(ValueError):
            summarize(duplicated, cold, seeds)


if __name__ == "__main__":
    unittest.main()
