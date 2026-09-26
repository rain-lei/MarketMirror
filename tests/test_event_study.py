import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from research.baselines.event_study import DailyObservation, event_study, read_market_csv


class EventStudyTest(unittest.TestCase):
    def observations(self):
        result = []
        for i in range(60):
            market = (i % 7 - 3) * 0.001
            shock = 0.05 if i == 40 else 0
            result.append(DailyObservation(date(2020, 1, 1) + timedelta(days=i), 0.001 + 1.5 * market + shock, market))
        return result

    def test_known_shock_and_disjoint_windows(self):
        rows = self.observations()
        result = event_study(rows, rows[40].trade_date, estimation_window=20,
                             pre_event_gap=0, window_before=5, window_after=3)
        self.assertAlmostEqual(result["cumulative_abnormal_return"], 0.05, places=10)
        self.assertAlmostEqual(result["beta"], 1.5, places=10)
        self.assertLess(result["estimation_end"], result["abnormal_returns"][0]["trade_date"])
        self.assertEqual([r["relative_trade_day"] for r in result["abnormal_returns"]], list(range(-5, 4)))

    def test_early_event_cannot_slice_future_training_data(self):
        rows = self.observations()
        with self.assertRaisesRegex(ValueError, "complete estimation"):
            event_study(rows, rows[1].trade_date, estimation_window=20,
                        pre_event_gap=5, window_before=0)

    def test_incomplete_post_event_window_fails(self):
        rows = self.observations()
        with self.assertRaisesRegex(ValueError, "complete event"):
            event_study(rows, rows[-1].trade_date, estimation_window=20)

    def test_duplicate_dates_nonfinite_and_negative_windows_fail(self):
        rows = self.observations()
        with self.assertRaisesRegex(ValueError, "duplicate trade"):
            event_study(rows + [rows[0]], rows[40].trade_date, estimation_window=20)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            event_study(rows, rows[40].trade_date, window_before=-1)
        rows[0] = DailyObservation(rows[0].trade_date, float("nan"), 0)
        with self.assertRaisesRegex(ValueError, "finite"):
            event_study(rows, rows[40].trade_date, estimation_window=20)

    def test_csv_requires_filter_for_multiple_securities(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "market.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["trade_date", "stock_code", "stock_return", "market_return"])
                writer.writerow(["2020-01-01", "000001", 0.01, 0.005])
                writer.writerow(["2020-01-01", "000002", 0.02, 0.005])
            with self.assertRaisesRegex(ValueError, "multiple securities"):
                read_market_csv(path)
            self.assertEqual(len(read_market_csv(path, "000001")), 1)
            with self.assertRaisesRegex(ValueError, "no market observations"):
                read_market_csv(path, "999999")


if __name__ == "__main__":
    unittest.main()
