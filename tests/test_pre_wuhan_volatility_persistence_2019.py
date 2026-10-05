import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from research.simulation.audit_pre_wuhan_volatility_persistence_2019 import (
    build_risk_panel,
    score_forecasts,
)


class PreWuhanVolatilityPersistenceTest(unittest.TestCase):
    def test_risk_feature_ends_at_t_minus_two(self):
        start = date(2019, 10, 1)
        rows = []
        stock = [0.01, -0.01, 0.02, 0.03, 0.50]
        market = [0.02, -0.02, 0.01, 0.01, 0.60]
        for index, (sret, mret) in enumerate(zip(stock, market)):
            rows.append(SimpleNamespace(trade_date=start + timedelta(days=index),
                                         stock_return=sret, market_return=mret))
        panel = build_risk_panel({"000001": rows}, ["000001"], [rows[4].trade_date.isoformat()], window=2)
        self.assertEqual(panel[0]["signal_cutoff_date"], rows[2].trade_date.isoformat())
        self.assertAlmostEqual(panel[0]["lagged_stock_volatility_20"], __import__("statistics").stdev(stock[1:3]))
        self.assertAlmostEqual(panel[0]["absolute_observed_return"], 0.50)
        self.assertNotEqual(panel[0]["lagged_stock_volatility_20"], __import__("statistics").stdev(stock[-2:]))

    def test_forecast_scoring_reports_error_and_r2(self):
        score = score_forecasts([1.0, 2.0], [1.0, 1.0])
        self.assertEqual(score["mae"], 0.5)
        self.assertAlmostEqual(score["rmse"], (0.5 ** 0.5))
        self.assertAlmostEqual(score["r2_vs_reference"], -1.0)

    def test_missing_lagged_history_is_rejected(self):
        rows = [SimpleNamespace(trade_date=date(2019, 10, 1) + timedelta(days=index),
                                stock_return=0.01, market_return=0.02) for index in range(3)]
        with self.assertRaisesRegex(ValueError, "history"):
            build_risk_panel({"000001": rows}, ["000001"], [rows[-1].trade_date.isoformat()], window=2)


if __name__ == "__main__":
    unittest.main()
