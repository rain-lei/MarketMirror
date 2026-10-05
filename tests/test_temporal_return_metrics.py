import copy
import unittest

from research.simulation.temporal_return_metrics import freeze_mask, metrics
from research.simulation.own_price_feedback_metrics import DEFAULT_LIMITS


def fixture():
    stocks = ["a", "b", "c"]
    dates = [f"2021-01-{day:02}" for day in (4, 5, 6, 7)]
    values = {"a": [.0, .02, None, -.02], "b": [.03, -.02, .01, -.01], "c": [-.01, .01, .03, .0]}
    joined = {s: [{"trade_date": d, "observed_return": v} for d, v in zip(dates, values[s])] for s in stocks}
    rows = [{"stock_code": s, "trade_date": d, "observed_return": v, "price_before_minor": 10000,
        "price_after_minor": 10000 + (i + 1) * (100 if s != "b" else -100)}
        for s in stocks for i, (d, v) in enumerate(zip(dates, values[s]))]
    return joined, stocks, dates, rows


class TemporalReturnMetricsTests(unittest.TestCase):
    def test_null_targets_and_known_zero_have_different_denominators(self):
        joined, stocks, dates, rows = fixture()
        mask = freeze_mask(joined, stocks, dates)
        result = metrics(rows, mask, DEFAULT_LIMITS)
        self.assertEqual(result["comparison"]["pairs"], 11)
        self.assertEqual(result["comparison"]["observed"]["zero_return_fraction"], 2 / 11)
        self.assertEqual(result["evaluation_coverage"]["full_simulated_company_days"], 12)
        self.assertEqual(result["evaluation_coverage"]["unknown_target_company_days"], 1)

    def test_full_cohort_portfolio_uses_same_predeclared_dates(self):
        joined, stocks, dates, rows = fixture()
        mask = freeze_mask(joined, stocks, dates)
        self.assertEqual(mask["full_cohort_portfolio_common_dates"], [dates[0], dates[1], dates[3]])
        result = metrics(rows, mask, DEFAULT_LIMITS)
        altered = copy.deepcopy(rows)
        for row in altered:
            if row["trade_date"] == dates[2]:
                row["price_after_minor"] = 20000
        again = metrics(altered, mask, DEFAULT_LIMITS)
        self.assertEqual(result["common_factor_metrics"]["synthetic"]["equal_weight_daily_return_std"],
            again["common_factor_metrics"]["synthetic"]["equal_weight_daily_return_std"])

    def test_target_zero_fill_drop_duplicate_and_scope_replacement_rejected(self):
        joined, stocks, dates, rows = fixture()
        mask = freeze_mask(joined, stocks, dates)
        changed = copy.deepcopy(rows)
        next(r for r in changed if r["observed_return"] is None)["observed_return"] = 0.0
        for invalid in (changed, rows[:-1], rows + rows[:1]):
            with self.assertRaises(ValueError):
                metrics(invalid, mask, DEFAULT_LIMITS)

    def test_undefined_synthetic_correlation_does_not_silently_drop_pair(self):
        joined, stocks, dates, rows = fixture()
        mask = freeze_mask(joined, stocks, dates)
        for row in rows:
            if row["stock_code"] == "a":
                row["price_after_minor"] = row["price_before_minor"]
        result = metrics(rows, mask, DEFAULT_LIMITS)
        self.assertIsNone(result["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"])
        self.assertFalse(result["joint_checks"]["criteria"]["stock_correlation"])
        self.assertEqual(len(result["evaluation_coverage"]["undefined_synthetic_primary_pairs"]), 2)

    def test_observed_zero_volatility_keeps_ratios_undefined(self):
        joined, stocks, dates, rows = fixture()
        for steps in joined.values():
            for row in steps:
                row["observed_return"] = 0.0
        for row in rows:
            row["observed_return"] = 0.0
        mask = freeze_mask(joined, stocks, dates)
        result = metrics(rows, mask, DEFAULT_LIMITS)
        self.assertIsNone(result["joint_checks"]["values"]["volatility_ratio"])
        self.assertIsNone(result["joint_checks"]["values"]["portfolio_volatility_ratio"])
        self.assertFalse(result["joint_checks"]["all_five_pass"])
