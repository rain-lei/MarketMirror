import unittest

from research.simulation.audit_pre_wuhan_common_factor_2019 import common_factor_metrics


class PreWuhanCommonFactorTest(unittest.TestCase):
    def rows(self):
        returns = [0.01, -0.02, 0.03]
        return [{"stock_code": stock, "trade_date": f"2019-11-0{index + 1}",
                 "observed_return": value, "price_before_minor": 100000,
                 "price_after_minor": round(100000 * (1 + value))}
                for stock in ("A", "B", "C") for index, value in enumerate(returns)]

    def test_perfect_shared_shocks_are_identified_as_common(self):
        metrics = common_factor_metrics(self.rows())
        for panel in metrics.values():
            self.assertAlmostEqual(panel["mean_pairwise_stock_return_correlation"], 1.0, places=8)
            self.assertAlmostEqual(panel["leave_one_stock_out_market_factor_mean_r2"], 1.0, places=8)
            self.assertEqual(panel["leave_one_stock_out_r2_defined_stocks"], 3)

    def test_duplicate_stock_date_is_rejected(self):
        rows = self.rows()
        rows.append(dict(rows[0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            common_factor_metrics(rows)

    def test_incomplete_panel_is_rejected(self):
        rows = self.rows()[:-1]
        with self.assertRaisesRegex(ValueError, "complete"):
            common_factor_metrics(rows)


if __name__ == "__main__":
    unittest.main()
