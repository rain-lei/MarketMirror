import math
import unittest

from research.simulation.audit_pre_wuhan_lagged_activity_2019 import activity_surprise, solve


class LaggedActivityAuditTest(unittest.TestCase):
    def test_activity_feature_uses_cutoff_and_prior_window_only(self):
        values = [100.0] * 20 + [400.0, 999.0]
        result = activity_surprise(values, cutoff_index=20, lookback=20)
        changed_future = [*values[:21], 1_000_000.0]
        self.assertAlmostEqual(result, activity_surprise(changed_future, 20, 20))
        self.assertAlmostEqual(result, math.log1p(400.0) - math.log1p(100.0))

    def test_activity_feature_rejects_insufficient_history(self):
        with self.assertRaisesRegex(ValueError, "invalid lagged amount window"):
            activity_surprise([10.0, 11.0], cutoff_index=1, lookback=2)

    def test_small_linear_solver(self):
        result = solve([[4.0, 1.0], [1.0, 3.0]], [1.0, 2.0])
        self.assertAlmostEqual(result[0], 1 / 11)
        self.assertAlmostEqual(result[1], 7 / 11)


if __name__ == "__main__":
    unittest.main()
