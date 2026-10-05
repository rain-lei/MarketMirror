import unittest

from research.simulation.audit_pre_wuhan_lagged_information_2019 import pearson, summarize


class LaggedInformationAuditTest(unittest.TestCase):
    def setUp(self):
        self.rows = []
        days = [
            ("2019-11-01", "2019-10-30", "2019-10-31", -1.0, -0.02, (0.02, 0.00)),
            ("2019-11-04", "2019-10-31", "2019-11-01", 0.0, 0.00, (-0.01, 0.00)),
            ("2019-11-05", "2019-11-01", "2019-11-04", 1.0, 0.03, (0.03, 0.01)),
        ]
        for day, cutoff, reference, signal, market_return, stock_returns in days:
            for code, stock_return in zip(("000001", "000002"), stock_returns, strict=True):
                self.rows.append({"stock_code": code, "trade_date": day,
                                  "signal_cutoff_date": cutoff,
                                  "execution_reference_date": reference,
                                  "lagged_market_signal": signal,
                                  "same_day_market_return": market_return,
                                  "stock_return": stock_return})

    def test_daily_market_signal_is_counted_once_per_session(self):
        result = summarize(self.rows, companies=2, sessions=3)
        self.assertEqual(result["company_days"], 6)
        self.assertEqual(result["sessions"], 3)
        expected = pearson([-1.0, 0.0, 1.0], [0.01, -0.005, 0.02])
        self.assertAlmostEqual(result["daily_market_correlations"][
            "lagged_signal_vs_mean_stock_return"], expected)

    def test_rejects_future_cutoff_and_inconsistent_benchmark(self):
        future = [dict(row) for row in self.rows]
        future[0]["signal_cutoff_date"] = future[0]["execution_reference_date"]
        with self.assertRaisesRegex(ValueError, "future information"):
            summarize(future, companies=2, sessions=3)
        mismatch = [dict(row) for row in self.rows]
        mismatch[1]["same_day_market_return"] = 0.01
        with self.assertRaisesRegex(ValueError, "benchmark differs"):
            summarize(mismatch, companies=2, sessions=3)

    def test_rejects_duplicate_company_day(self):
        with self.assertRaises(ValueError):
            summarize(self.rows[:-1] + [self.rows[0]], companies=2, sessions=3)


if __name__ == "__main__":
    unittest.main()
