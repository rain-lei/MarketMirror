import unittest

from research.simulation.audit_pre_wuhan_market_volume_2019 import align_execution, compare_volume


class MarketVolumeAuditTest(unittest.TestCase):
    def test_execution_volume_uses_prior_session_not_return_observation_day(self):
        calendar = ["2019-10-29", "2019-10-30", "2019-10-31", "2019-11-01"]
        row = {"trade_date": "2019-11-01", "signal_cutoff_date": "2019-10-30"}
        self.assertEqual(align_execution([row], calendar)[0]["execution_reference_date"],
                         "2019-10-31")
        with self.assertRaises(ValueError):
            align_execution([{**row, "signal_cutoff_date": "2019-10-31"}], calendar)

    def test_exact_pairing_and_share_scale(self):
        rows = [{"stock_code": "000001", "trade_date": "2019-11-01",
                 "execution_reference_date": "2019-10-31", "matched_volume": 100},
                {"stock_code": "000001", "trade_date": "2019-11-04",
                 "execution_reference_date": "2019-11-01", "matched_volume": 0},
                {"stock_code": "000002", "trade_date": "2019-11-01",
                 "execution_reference_date": "2019-10-31", "matched_volume": 200}]
        observed = {("000001", "2019-10-31"): 1000,
                    ("000001", "2019-11-01"): 0,
                    ("000002", "2019-10-31"): 2000}
        result = compare_volume(rows, observed)
        self.assertEqual(result["company_days"], 3)
        self.assertEqual(result["simulated_matched_shares"], 300)
        self.assertEqual(result["observed_traded_shares"], 3000)
        self.assertAlmostEqual(result["pooled_simulated_to_observed_ratio"], 0.1)
        self.assertAlmostEqual(result["median_daily_ratio_on_observed_positive"], 0.1)
        self.assertEqual(result["observed_zero_days"], 1)

    def test_rejects_missing_and_duplicate_company_days(self):
        row = {"stock_code": "000001", "trade_date": "2019-11-01",
               "execution_reference_date": "2019-10-31", "matched_volume": 100}
        observed = {("000001", "2019-10-31"): 1000}
        with self.assertRaises(ValueError):
            compare_volume([row, row], observed)
        with self.assertRaises(ValueError):
            compare_volume([row], {**observed, ("000002", "2019-10-31"): 2000})


if __name__ == "__main__":
    unittest.main()
