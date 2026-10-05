import unittest

from research.simulation.run_pre_wuhan_t_clock_market_2019 import set_execution_availability


class TClockMarketTest(unittest.TestCase):
    def setUp(self):
        self.steps = [
            {"trade_date": "2019-11-01", "signal_cutoff_date": "2019-10-30",
             "execution_reference_date": "2019-10-31", "observed_return": -0.1},
            {"trade_date": "2019-11-04", "signal_cutoff_date": "2019-10-31",
             "execution_reference_date": "2019-11-01", "observed_return": 0.02},
        ]
        self.volume = {("000001", "2019-10-31"): 0,
                       ("000001", "2019-11-01"): 1000,
                       ("000001", "2019-11-04"): 0}

    def test_only_availability_clock_changes_between_paths(self):
        legacy = set_execution_availability(self.steps, "000001", self.volume,
                                            clock="legacy_reference")
        corrected = set_execution_availability(self.steps, "000001", self.volume,
                                               clock="auction_date")
        self.assertEqual([row["execution_available"] for row in legacy], [False, True])
        self.assertEqual([row["execution_available"] for row in corrected], [True, False])
        for before, old, new in zip(self.steps, legacy, corrected, strict=True):
            self.assertEqual({key: value for key, value in old.items()
                              if key != "execution_available"}, before)
            self.assertEqual({key: value for key, value in new.items()
                              if key != "execution_available"}, before)
            self.assertNotIn("volume", new)
            self.assertEqual(new["observed_return"], before["observed_return"])

    def test_rejects_noncausal_dates_and_unknown_clock(self):
        bad = [dict(self.steps[0], signal_cutoff_date="2019-10-31")]
        with self.assertRaisesRegex(ValueError, "not ordered"):
            set_execution_availability(bad, "000001", self.volume, clock="auction_date")
        with self.assertRaisesRegex(ValueError, "unsupported execution clock"):
            set_execution_availability(self.steps, "000001", self.volume, clock="unknown")


if __name__ == "__main__":
    unittest.main()
