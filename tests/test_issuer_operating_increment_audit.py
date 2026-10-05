import copy
import unittest
from decimal import Decimal, localcontext

from research.baselines.audit_issuer_operating_increment import check_comparison, raw_ratio
from research.baselines.issuer_financial_increment import comparison, metrics


def amount(value, status="NUMERIC"):
    return {"status": status, "value_yuan": value}


def source_fixture():
    operating = {"cashflow": {"amounts": {"operating_net_cashflow": amount("-10"),
                                         "cash_equivalent_closing": amount("0")}},
                 "profit": {"amounts": {"net_profit": amount("-2"), "operating_total_revenue": amount("20"),
                                       "operating_revenue": amount("5")}},
                 "additional_assets": {"amounts": {"trading_financial_assets": amount(None, "REPORTED_BLANK"),
                                                  "other_current_assets": amount("4")}},
                 "selected_revenue_source_field": "operating_total_revenue"}
    return operating, {"amounts": {"assets": amount("3")}}


def comparison_fixture():
    rows = [{"trade_date": day, "target_absolute_return": value}
            for day, value in zip(["d1", "d1", "d2", "d3"], [.2, .3, .4, .1])]
    baseline, extra = [.1, .2, .3, .3], [.2, .3, .4, .4]
    record = comparison({"metrics": metrics(rows, baseline)}, {"metrics": metrics(rows, extra)})
    return record, baseline, extra, [r["target_absolute_return"] for r in rows], [r["trade_date"] for r in rows]


class OperatingIncrementIndependentAuditTest(unittest.TestCase):
    def test_signed_source_amount_is_recomputed_without_exported_ratio(self):
        operating, balance = source_fixture()
        operating["ratios"] = {"operating_net_cashflow_to_period_end_assets": {"value": "999"}}
        with localcontext() as context:
            context.prec = 50
            expected = str(Decimal("-10") / Decimal("3"))
        self.assertEqual(raw_ratio(operating, balance, "operating_net_cashflow_to_period_end_assets"), expected)
        self.assertEqual(raw_ratio(operating, balance, "cash_equivalent_closing_to_assets"), "0")

    def test_blank_and_illegal_denominators_remain_missing(self):
        operating, balance = source_fixture()
        self.assertIsNone(raw_ratio(operating, balance, "trading_financial_assets_to_assets"))
        for value in ("0", "-1", "NaN", "Infinity"):
            with self.subTest(value=value):
                balance["amounts"]["assets"] = amount(value)
                self.assertIsNone(raw_ratio(operating, balance, "operating_net_cashflow_to_period_end_assets"))

    def test_negative_asset_is_not_treated_as_signed_operating_flow(self):
        operating, balance = source_fixture()
        operating["additional_assets"]["amounts"]["other_current_assets"] = amount("-4")
        self.assertIsNone(raw_ratio(operating, balance, "other_current_assets_to_assets"))

    def test_revenue_preference_is_source_based_and_never_skips_numeric_zero(self):
        operating, balance = source_fixture()
        self.assertEqual(raw_ratio(operating, balance, "operating_net_cashflow_to_selected_revenue"), "-0.5")
        operating["profit"]["amounts"]["operating_total_revenue"] = amount("0")
        self.assertIsNone(raw_ratio(operating, balance, "operating_net_cashflow_to_selected_revenue"))
        operating["profit"]["amounts"]["operating_total_revenue"] = amount(None, "REPORTED_BLANK")
        operating["selected_revenue_source_field"] = "operating_revenue"
        self.assertEqual(raw_ratio(operating, balance, "operating_net_cashflow_to_selected_revenue"), "-2")
        operating["selected_revenue_source_field"] = "operating_total_revenue"
        with self.assertRaisesRegex(ValueError, "source revenue"):
            raw_ratio(operating, balance, "operating_net_cashflow_to_selected_revenue")

    def test_signed_improvements_and_all_date_deletions_agree(self):
        args = comparison_fixture()
        check_comparison(*args)
        record = args[0]
        self.assertGreater(record["relative_error_improvement"]["pooled_mae"], 0)
        self.assertLess(record["relative_error_improvement"]["pooled_rmse"], 0)

    def test_changed_improvement_or_sensitivity_range_fails_audit(self):
        args = comparison_fixture()
        changed = copy.deepcopy(args[0])
        changed["relative_error_improvement"]["pooled_mae"] = .9
        with self.assertRaisesRegex(ValueError, "reconstruction differs"):
            check_comparison(changed, *args[1:])
        changed = copy.deepcopy(args[0])
        changed["delete_one_date_improvement_range"]["pooled_mae_improvement"][0] = .9
        with self.assertRaisesRegex(ValueError, "reconstruction differs"):
            check_comparison(changed, *args[1:])

    def test_omitted_date_and_false_lower_error_count_fail_audit(self):
        args = comparison_fixture()
        changed = copy.deepcopy(args[0])
        changed["delete_one_evaluation_date"].pop()
        with self.assertRaisesRegex(ValueError, "coverage differs"):
            check_comparison(changed, *args[1:])
        changed = copy.deepcopy(args[0])
        changed["evaluation_dates_with_lower_mae"] = 99
        with self.assertRaisesRegex(ValueError, "date count differs"):
            check_comparison(changed, *args[1:])


if __name__ == "__main__":
    unittest.main()
