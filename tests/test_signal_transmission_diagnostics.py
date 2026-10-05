import unittest

from research.simulation.signal_transmission_diagnostics import parts, quote, panel_statistics, STAGES


class SignalTransmissionDiagnosticsTests(unittest.TestCase):
    def test_public_removal_reclips_private_before_quote(self):
        row = {"risk_budget": {"residual_raw_shift_bps": 600.0, "market_raw_shift_bps": -300.0},
               "public_information_budget": {"public_market_multiplier": 1.0}}
        receipt = {"applied_valuation_shift_bps": 300.0,
                   "public_information_receipt": {"private_received": True}}
        private, market, removed = parts(row, receipt, {"max_shift_bps": 500})
        self.assertEqual((private, market, removed), (600, -300, 500))
        self.assertNotEqual(receipt["applied_valuation_shift_bps"] - market, removed)

    def test_unreceived_private_is_known_absence(self):
        row = {"risk_budget": {"residual_raw_shift_bps": 600.0, "market_raw_shift_bps": 300.0}}
        receipt = {"received": False, "applied_valuation_shift_bps": 0.0}
        self.assertEqual(parts(row, receipt, {"max_shift_bps": 500}), (0, 0, 0))

    def test_quote_rounding_and_band_are_retained(self):
        self.assertEqual(quote(10000, "buy", 0, 10.5, 0, 1, (9000, 11000)), 10010)
        self.assertEqual(quote(10000, "sell", 0, 10.5, 0, 1, (9000, 11000)), 10011)
        self.assertEqual(quote(10000, "buy", 0, 2000, 0, 1, (9000, 11000)), 11000)

    def test_constant_stage_keeps_undefined_primary_correlation(self):
        mask = {"stocks": ["a", "b"], "dates": ["1", "2", "3"],
                "full_cohort_portfolio_common_dates": ["1", "2", "3"],
                "correlation_pairs": [{"left": "a", "right": "b", "known_dates": ["1", "2", "3"], "primary_eligible": True}]}
        rows = [{"stock_code": s, "trade_date": d,
                 "stages": {k: 0 if s == "a" else i for k in STAGES}}
                for s in mask["stocks"] for i, d in enumerate(mask["dates"])]
        result = panel_statistics(rows, mask)
        for stage in STAGES:
            self.assertIsNone(result[stage]["mean_stock_correlation"])
            self.assertEqual(result[stage]["undefined_primary_pairs"], [["a", "b"]])
            self.assertEqual(result[stage]["fixed_primary_pairs"], 1)

    def test_incomplete_stage_panel_is_rejected(self):
        mask = {"stocks": ["a"], "dates": ["1", "2"]}
        with self.assertRaises(ValueError):
            panel_statistics([], mask)


if __name__ == "__main__":
    unittest.main()
