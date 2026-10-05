import copy
import math
import unittest
from datetime import date, timedelta

from research.baselines.issuer_financial_increment import (
    MARKET_FEATURES, build_panel, comparison, fit_linear, fit_models, metrics, predict, solve, split_dates,
)


def fixture():
    calendar = [(date(2019, 1, 1) + timedelta(days=i)).isoformat() for i in range(37)]
    cfg = {"period_start": calendar[27], "period_end": calendar[-1], "expected_sessions": 10,
           "nominal_training_sessions": 7, "evaluation_sessions": 3, "market_lag_sessions": 2,
           "signal_cutoff_time": "15:00:00+08:00", "return_lookback_sessions": 20,
           "activity_lookback_sessions": 20, "market_momentum_sessions": 5}
    codes = [f"{i:06}" for i in range(1, 13)]
    states, industries, market, amounts = {}, {}, {}, {}
    for k, code in enumerate(codes):
        sector = "C" if k % 2 else "B"
        industries[code] = {"stock_code": code, "status": "verified_table_label", "category_code": sector,
                            "industry_code": sector + "01", "available_at_proxy": calendar[1] + "T00:00:00+08:00"}
        states[code] = {"stock_code": code, "status": "INDEPENDENT_EXTRACTION_AGREEMENT",
                        "industry_code": sector + "01", "agent_signal_enabled": False,
                        "generic_nonfinancial_ratios_applicable": True,
                        "available_at_proxy": calendar[1] + "T00:00:00+08:00",
                        "amounts": {"assets": {"status": "NUMERIC", "value_yuan": str(1000 + k * k * 171)}},
                        "ratios": {"total_liabilities_to_assets": {"status": "OBSERVED_RATIO", "value": str(.1 + k * .031)},
                                   "money_funds_to_assets": {"status": "OBSERVED_RATIO", "value": str(.08 + (k % 5) * .07)}}}
        for i, day in enumerate(calendar):
            market[(code, day)] = (.02 * math.sin(i * (.3 + k * .017)) + .002 * math.cos(i + k),
                                   .012 * math.sin(i * .79) + .007 * math.cos(i * .21))
            amounts[(code, day)] = 300 + k * 45 + (i % 7) * k * k + 33 * abs(math.sin(i * .2 + k))
    return calendar, codes, market, amounts, states, industries, cfg


class FinancialIncrementTest(unittest.TestCase):
    def test_last_nominal_training_target_is_purged_before_first_signal(self):
        calendar, *_, cfg = fixture()
        split = split_dates(calendar, cfg)
        self.assertEqual(len(split["training_dates"]), 6)
        self.assertEqual(split["purged_dates"], [calendar[33]])
        self.assertEqual(split["model_fit_cutoff_at"], calendar[32] + "T15:00:00+08:00")

    def test_unordered_duplicate_calendar_and_naive_clock_rejected(self):
        calendar, *_, cfg = fixture()
        with self.assertRaisesRegex(ValueError, "unique and ordered"):
            split_dates([*calendar, calendar[-1]], cfg)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            split_dates(calendar, {**cfg, "signal_cutoff_time": "15:00:00"})

    def test_future_target_and_execution_day_do_not_change_feature_history(self):
        args = fixture()
        rows, _ = build_panel(*args)
        calendar, codes, market, amounts, *_ = args
        selected = rows[0]
        market[(codes[0], selected["execution_reference_date"])] = (.8, .9)
        market[(codes[0], selected["trade_date"])] = (.6, .7)
        amounts[(codes[0], selected["execution_reference_date"])] = 1e18
        changed, _ = build_panel(*args)
        self.assertEqual(selected["features"], changed[0]["features"])
        self.assertEqual(changed[0]["target_absolute_return"], .6)

    def test_revision_cannot_backfill_but_enters_at_own_cutoff(self):
        args = fixture()
        args[4][args[1][0]]["available_at_proxy"] = args[0][27] + "T00:00:00+08:00"
        rows, _ = build_panel(*args)
        first = [r for r in rows if r["stock_code"] == args[1][0]][:3]
        self.assertEqual([r["paired_exclusion_reason"] for r in first],
                         ["FINANCIAL_SOURCE_NOT_YET_AVAILABLE", "FINANCIAL_SOURCE_NOT_YET_AVAILABLE", None])
        self.assertIsNone(first[0]["features"])
        self.assertIsNone(first[0]["target_absolute_return"])

    def test_missing_ratio_is_excluded_from_all_models_without_zero(self):
        args = fixture()
        args[4][args[1][0]]["ratios"]["money_funds_to_assets"] = {"status": "MISSING_SOURCE_AMOUNT", "value": None}
        rows, _ = build_panel(*args)
        affected = [r for r in rows if r["stock_code"] == args[1][0]]
        self.assertTrue(all(r["paired_exclusion_reason"] == "MISSING_FIXED_FINANCIAL_SOURCE_VALUE" and r["features"] is None for r in affected))

    def test_financial_institution_and_late_industry_are_not_ordinary_zeroes(self):
        args = fixture()
        args[4][args[1][0]]["generic_nonfinancial_ratios_applicable"] = False
        args[5][args[1][1]]["available_at_proxy"] = args[0][-1] + "T00:00:00+08:00"
        rows, _ = build_panel(*args)
        self.assertEqual(rows[0]["paired_exclusion_reason"], "FINANCIAL_INSTITUTION_OR_UNVERIFIED_SCOPE")
        self.assertEqual(rows[1]["paired_exclusion_reason"], "INDUSTRY_SOURCE_NOT_YET_AVAILABLE")

    def test_historical_industry_and_financial_identity_must_match(self):
        args = fixture()
        args[5][args[1][0]]["industry_code"] = "J66"
        with self.assertRaisesRegex(ValueError, "record differs"):
            build_panel(*args)

    def test_exchange_history_gap_is_rejected(self):
        args = fixture()
        del args[3][(args[1][0], args[0][10])]
        with self.assertRaisesRegex(ValueError, "complete exchange-session"):
            build_panel(*args)

    def test_singular_block_is_not_saved_by_ridge(self):
        with self.assertRaisesRegex(ValueError, "rank-deficient"):
            solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0])

    def test_train_only_standardization_and_no_prediction_clipping(self):
        train = [{"features": {"x": x}, "target_absolute_return": 2 - x} for x in [0., 1., 2., 3.]]
        fit = fit_linear(train, ["x"])
        self.assertEqual(fit["training_means"]["x"], 1.5)
        self.assertAlmostEqual(predict(fit, [{"features": {"x": 5.}}])[0], -3.)
        score = metrics([{"trade_date": "2020-01-01", "target_absolute_return": 1.}], [-3.])
        self.assertEqual(score["negative_prediction_count"], 1)

    def test_evaluation_and_purged_outcomes_cannot_change_any_fit(self):
        panel, split = build_panel(*fixture())
        original = fit_models(panel, split)
        changed = copy.deepcopy(panel)
        for row in changed:
            if row["partition"] != "train" and row["paired_exclusion_reason"] is None:
                row["target_absolute_return"] = .987
        new = fit_models(changed, split)
        for name in original["models"]:
            self.assertEqual(original["models"][name]["fit"], new["models"][name]["fit"])
        self.assertEqual([r["predictions"] for r in original["evaluation_predictions"]],
                         [r["predictions"] for r in new["evaluation_predictions"]])

    def test_overlapping_fit_clock_and_duplicate_panel_rejected(self):
        panel, split = build_panel(*fixture())
        with self.assertRaisesRegex(ValueError, "later than"):
            fit_models(panel, {**split, "model_fit_cutoff_at": split["training_dates"][-2] + "T15:00:00+08:00"})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            fit_models([*panel, panel[0]], split)

    def test_all_models_use_exact_same_paired_predictions_and_fixed_effects(self):
        panel, split = build_panel(*fixture())
        result = fit_models(panel, split)
        self.assertEqual({m["metrics"]["rows"] for m in result["models"].values()}, {36})
        self.assertEqual(result["models"]["company_fixed_market"]["fit"]["within"]["features"], MARKET_FEATURES)
        self.assertTrue(result["models"]["company_fixed_market"]["fit"]["static_financial_block_absorbed_by_company_effects"])
        self.assertFalse(result["agent_signal_enabled"])

    def test_daily_metrics_do_not_treat_unequal_company_counts_as_equal_rows(self):
        rows = [{"trade_date": "d1", "target_absolute_return": 0.}] * 3 + [
                {"trade_date": "d2", "target_absolute_return": 0.}]
        score = metrics(rows, [1., 1., 1., 3.])
        self.assertEqual(score["pooled_mae"], 1.5)
        self.assertEqual(score["equal_session_mae"], 2.)

    def test_comparison_rejects_different_company_date_pairs(self):
        left = metrics([{"trade_date": "d1", "target_absolute_return": 1.}], [0.])
        right = metrics([{"trade_date": "d2", "target_absolute_return": 1.}], [0.])
        with self.assertRaisesRegex(ValueError, "different evaluation samples"):
            comparison({"metrics": left}, {"metrics": right})


if __name__ == "__main__":
    unittest.main()
