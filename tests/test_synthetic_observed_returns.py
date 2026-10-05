import math
import unittest

from research.simulation.audit_synthetic_observed_returns import compare_pairs, describe, flat_price_tie


class SyntheticObservedReturnsTests(unittest.TestCase):
    def test_pair_metrics_exclude_zero_from_sign_agreement(self):
        result = compare_pairs([(0.1, 0.2), (-0.1, -0.2), (0.0, 0.1),
                                (0.2, -0.1), (0.0, 0.0)])
        self.assertEqual(result["pairs"], 5)
        self.assertEqual(result["both_nonzero_pairs"], 3)
        self.assertAlmostEqual(result["same_sign_nonzero_fraction"], 2 / 3)
        self.assertEqual(result["synthetic"]["zero_return_fraction"], 2 / 5)
        self.assertEqual(result["observed"]["zero_return_fraction"], 1 / 5)

    def test_constant_series_has_undefined_correlation(self):
        result = compare_pairs([(0.0, -0.2), (0.0, 0.2)])
        self.assertIsNone(result["return_correlation"])
        self.assertIsNone(result["same_sign_nonzero_fraction"])
        self.assertEqual(result["both_nonzero_pairs"], 0)

    def test_invalid_returns_do_not_enter_summary(self):
        with self.assertRaises(ValueError):
            describe([0.01, math.nan])
        with self.assertRaises(ValueError):
            compare_pairs([])

    def test_flat_price_tie_finds_equal_volume_and_imbalance(self):
        call = {"price_before_minor": 100, "price_after_minor": 100,
                "matched_volume": 100, "price_bounds_minor": [90, 110],
                "orders": [{"side": "buy", "limit_price_minor": 102, "accepted_quantity": 100},
                           {"side": "sell", "limit_price_minor": 98, "accepted_quantity": 100}]}
        self.assertTrue(flat_price_tie(call, 1))
        call["orders"][0]["limit_price_minor"] = 100
        call["orders"][1]["limit_price_minor"] = 100
        self.assertFalse(flat_price_tie(call, 1))


if __name__ == "__main__":
    unittest.main()
