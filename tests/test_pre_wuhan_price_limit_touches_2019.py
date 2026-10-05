import json
import unittest
from datetime import date, timedelta

from research.simulation.audit_pre_wuhan_price_limit_touches_2019 import (
    RULES,
    classify_bar,
    ordinary_2019_regime,
    validate_rules,
)


class PreWuhanPriceLimitTouchesTest(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads(RULES.read_text(encoding="utf-8"))
        start = date(2019, 10, 31)
        self.calendar = [(start + timedelta(days=i)).isoformat() for i in range(62)
                         if (start + timedelta(days=i)).weekday() < 5]

    def bar(self, preclose="10.00", open_price="10.00", high="10.10", low="9.90", close="10.00"):
        return {"preclose": preclose, "open": open_price, "high": high, "low": low,
                "close": close, "tradestatus": "1", "volume": "1000"}

    def test_rounded_upper_limit_can_have_return_below_or_above_ten_percent(self):
        below = self.bar("1.53", "1.53", "1.68", "1.50", "1.68")
        above = self.bar("1.48", "1.48", "1.63", "1.45", "1.63")
        self.assertLess(float(below["close"]) / float(below["preclose"]) - 1, 0.10)
        self.assertGreater(float(above["close"]) / float(above["preclose"]) - 1, 0.10)
        for bar in (below, above):
            result = classify_bar(bar, 1000)
            self.assertEqual(result["qc_status"], "ordinary_rule_reconstruction")
            self.assertTrue(result["upper_touch"])
            self.assertTrue(result["close_at_upper"])

    def test_half_up_rounding_and_lower_limit(self):
        result = classify_bar(self.bar("1.55", "1.55", "1.71", "1.40", "1.40"), 1000)
        self.assertEqual((result["limit_lower_rmb"], result["limit_upper_rmb"]), ("1.40", "1.71"))
        self.assertTrue(result["upper_touch"])
        self.assertTrue(result["lower_touch"])
        self.assertTrue(result["close_at_lower"])
        self.assertFalse(result["close_at_upper"])

    def test_historical_board_and_st_rules_are_date_specific(self):
        validate_rules(self.rules)
        expected = [("sz.300001", "0", 1000), ("sh.600037", "1", 500), ("sh.688018", "0", 2000)]
        for symbol, is_st, band in expected:
            regime = ordinary_2019_regime(symbol, is_st, "2019-12-02", "2019-07-22",
                                          self.calendar, self.rules)
            self.assertEqual(regime["band_bps"], band)
        changed = {**self.rules, "regular_band_bps": 2000}
        with self.assertRaisesRegex(ValueError, "contract"):
            validate_rules(changed)
        with self.assertRaisesRegex(ValueError, "date"):
            ordinary_2019_regime("sz.300001", "0", "2020-08-24", "2019-07-22", self.calendar, self.rules)
        with self.assertRaisesRegex(ValueError, "board"):
            ordinary_2019_regime("sh.900901", "0", "2019-12-02", "2019-07-22", self.calendar, self.rules)

    def test_star_first_five_listing_sessions_need_separate_review(self):
        for day in ("2019-11-04", "2019-11-08"):
            regime = ordinary_2019_regime("sh.688018", "0", day, "2019-11-04", self.calendar, self.rules)
            self.assertIsNone(regime["band_bps"])
            self.assertEqual(regime["status"], "listing_exemption_requires_review")
        after = ordinary_2019_regime("sh.688018", "0", "2019-11-11", "2019-11-04", self.calendar, self.rules)
        self.assertEqual(after["band_bps"], 2000)

    def test_unresolved_outside_band_and_suspended_flags_stay_null(self):
        suspended = self.bar(high="10.00", low="10.00")
        suspended.update(tradestatus="0", volume="0")
        cases = [(classify_bar({}, None), "unresolved_rule"),
                 (classify_bar(self.bar(high="11.01"), 1000), "bar_outside_reconstructed_band"),
                 (classify_bar(suspended, 1000), "suspended")]
        for result, status in cases:
            self.assertEqual(result["qc_status"], status)
            for flag in ("upper_touch", "lower_touch", "close_at_upper", "close_at_lower"):
                self.assertIsNone(result[flag])

    def test_invalid_tick_ohlc_or_suspended_activity_is_rejected(self):
        bars = [self.bar(high="10.101"), self.bar(low="10.05"), self.bar(high="9.99")]
        for bar in bars:
            with self.assertRaisesRegex(ValueError, "prices"):
                classify_bar(bar, 1000)
        suspended = self.bar(high="10.00", low="10.00")
        suspended["tradestatus"] = "0"
        with self.assertRaisesRegex(ValueError, "suspended"):
            classify_bar(suspended, 1000)


if __name__ == "__main__":
    unittest.main()
