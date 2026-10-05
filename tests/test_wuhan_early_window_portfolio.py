import unittest
from datetime import date, timedelta

from research.baselines.event_study import DailyObservation
from research.simulation.wuhan_early_window_portfolio import _warm_prepare_steps


class WuhanEarlyWindowPortfolioTest(unittest.TestCase):
    def test_warm_start_pads_only_missing_volatility_history(self):
        rows = [DailyObservation(date(2020, 1, 2) + timedelta(days=index),
                                 0.01 * ((index % 3) - 1),
                                 0.005 * ((index % 2) - 0.5)) for index in range(22)]
        steps = _warm_prepare_steps(rows, "2020-01-10", "2020-01-23", 5, 20)
        self.assertGreater(steps[0]["warm_start_missing_sessions"], 0)
        self.assertEqual(steps[0]["signal_cutoff_date"], "2020-01-08")
        self.assertLess(steps[0]["signal_cutoff_date"], steps[0]["trade_date"])
        self.assertEqual(steps[-1]["warm_start_missing_sessions"], 0)
        self.assertTrue(all(step["warm_start_missing_sessions"] >= 0 for step in steps))

    def test_warm_start_still_requires_two_prior_observations(self):
        rows = [DailyObservation(date(2020, 1, 2), 0.01, 0.02)]
        with self.assertRaisesRegex(ValueError, "strictly lagged information cutoff"):
            _warm_prepare_steps(rows, "2020-01-02", "2020-01-03", 5, 20)


if __name__ == "__main__":
    unittest.main()
