import unittest

from research.simulation.audit_pre_wuhan_price_band_2019 import price_band_metrics


class PreWuhanPriceBandTest(unittest.TestCase):
    def test_counts_model_band_touches_separately_from_observed_tail_magnitudes(self):
        rows = [
            {"stock_code": "A", "trade_date": "2019-11-01", "price_before_minor": 10000,
             "price_after_minor": 11000, "observed_return": 0.101},
            {"stock_code": "B", "trade_date": "2019-11-01", "price_before_minor": 10000,
             "price_after_minor": 9000, "observed_return": -0.10},
        ]
        metrics = price_band_metrics(rows, 1000)
        self.assertEqual(metrics["simulated_upper_band_touches"], 1)
        self.assertEqual(metrics["simulated_lower_band_touches"], 1)
        self.assertEqual(metrics["observed_adjusted_return_magnitude"]["absolute_return_count_at_least_pct"]["10.0"], 2)
        self.assertIn("does not establish", metrics["observed_return_interpretation"])

    def test_rejects_duplicate_and_out_of_band_rows(self):
        row = {"stock_code": "A", "trade_date": "2019-11-01", "price_before_minor": 10000,
               "price_after_minor": 10000, "observed_return": 0.0}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            price_band_metrics([row, dict(row)], 1000)
        outside = {**row, "stock_code": "B", "price_after_minor": 11100}
        with self.assertRaisesRegex(ValueError, "violates"):
            price_band_metrics([outside], 1000)


if __name__ == "__main__":
    unittest.main()
