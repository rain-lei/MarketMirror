import copy
import unittest
from research.simulation.initial_allocation_effects import paired_effects
from research.simulation.public_information_study import CELLS


def fixture():
    seeds = [{"seed_id": str(i)} for i in range(5)]
    arms = []
    for treatment in (True, False):
        rows = []
        for seed in seeds:
            for cell in CELLS:
                x = (3 if cell["background_anchor"] == "current_inventory" else 1) if treatment else 0
                rows.append({**cell, **seed,
                    "comparison": {"synthetic": {"mean": x, "standard_deviation": x + 1, "zero_return_fraction": x / 10}},
                    "common_factor_metrics": {"synthetic": {"mean_pairwise_stock_return_correlation": x / 10, "equal_weight_daily_return_std": x + 1}},
                    "joint_checks": {"values": {"absolute_mean_gap": x, "volatility_ratio": x + 1,
                        "zero_fraction_gap": x / 10, "stock_correlation_gap": x / 10, "portfolio_volatility_ratio": x + 1}}})
        arms.append(rows)
    return *arms, seeds


class AllocationEffectTests(unittest.TestCase):
    def test_all_pairs_and_known_difference_of_differences(self):
        new, old, seeds = fixture()
        r = paired_effects(new, old, seeds)
        self.assertEqual(len(r["paired_allocation_effects"]), 80)
        self.assertEqual(len(r["allocation_by_anchor_interactions"]), 40)
        self.assertTrue(all(x["metric_interaction"]["mean"] == 2 for x in r["allocation_by_anchor_interactions"]))

    def test_missing_condition_and_false_anchor_label_rejected(self):
        new, old, seeds = fixture()
        with self.assertRaises(ValueError):
            paired_effects(new[:-1], old, seeds)
        new[0]["background_anchor"] = "current_inventory"
        with self.assertRaises(ValueError):
            paired_effects(new, old, seeds)

    def test_undefined_correlation_propagates(self):
        new, old, seeds = fixture()
        new[0]["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"] = None
        new[0]["joint_checks"]["values"]["stock_correlation_gap"] = None
        r = paired_effects(new, old, seeds)
        self.assertIsNone(r["allocation_by_anchor_interactions"][0]["metric_interaction"]["stock_correlation"])
        self.assertFalse(r["paired_allocation_effects"][0]["all_five_gaps_nonworse"])
