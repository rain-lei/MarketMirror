import unittest

from research.simulation.audit_pre_wuhan_timeline_2019 import classify_sessions


class TimelineAuditTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"stock_code": "000001", "trade_date": "2019-11-01",
             "signal_cutoff_date": "2019-10-30", "execution_reference_date": "2019-10-31",
             "matched_volume": 400, "price_before_minor": 10000,
             "price_after_minor": 10010, "observed_return": 0.0},
            {"stock_code": "000001", "trade_date": "2019-11-04",
             "signal_cutoff_date": "2019-10-31", "execution_reference_date": "2019-11-01",
             "matched_volume": 0, "price_before_minor": 10010,
             "price_after_minor": 10010, "observed_return": -0.02},
        ]
        self.observed = {("000001", "2019-10-31"): 1000,
                         ("000001", "2019-11-01"): 0,
                         ("000001", "2019-11-04"): 900}

    def test_reports_both_sides_of_a_suspension_transition(self):
        result = classify_sessions(self.rows, self.observed)
        self.assertEqual(result["status_cross_tab"], {
            "reference_suspended_outcome_trading": 1,
            "reference_trading_outcome_suspended": 1,
        })
        self.assertEqual(result["simulated_trades_on_outcome_suspended_days"], 1)
        self.assertEqual(result["discordant_company_days"], 2)

    def test_never_allows_execution_on_a_suspended_reference(self):
        bad = [dict(row) for row in self.rows]
        bad[1]["matched_volume"] = 100
        with self.assertRaisesRegex(ValueError, "suspended execution reference"):
            classify_sessions(bad, self.observed)

    def test_rejects_incomplete_panel_and_future_information(self):
        with self.assertRaisesRegex(ValueError, "status panels differ"):
            classify_sessions(self.rows, {("000001", "2019-10-31"): 1000})
        bad = [dict(row) for row in self.rows]
        bad[0]["signal_cutoff_date"] = "2019-10-31"
        with self.assertRaisesRegex(ValueError, "not ordered"):
            classify_sessions(bad, self.observed)


if __name__ == "__main__":
    unittest.main()
