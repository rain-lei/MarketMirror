"""Avoid late-source leakage, institutional misapplication and false zeros."""

import unittest

from research.data_pipeline.financial_balance_sheet import extract_company
from research.data_pipeline.issuer_financial_states import company_state, observed_ratio, state_asof
from tests.test_financial_balance_sheet import sheet


def source_company(industry="C39"):
    return {**extract_company(sheet()), "stock_code": "999999", "historical_short_name": "测试发行人",
            "industry_code": industry, "source_pdf_path": "test.pdf", "source_pdf_sha256": "test-source-hash",
            "report_metadata": {"report_end_date": "2019-09-30", "available_at_proxy": "2019-12-08T00:00:00+08:00"}}


def review():
    return {"stock_code": "999999", "status": "PASS", "source_pdf_sha256": "test-source-hash", "selected_candidate_index": 0}


class IssuerFinancialStateTests(unittest.TestCase):
    def test_failed_review_cannot_export_state(self):
        with self.assertRaises(ValueError): company_state(source_company(), {**review(), "status": "FAIL"})

    def test_other_issuer_or_other_source_review_cannot_export_state(self):
        for key, value in (("stock_code", "999998"), ("source_pdf_sha256", "different-bytes"), ("selected_candidate_index", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                company_state(source_company(), {**review(), key: value})

    def test_absent_loan_stays_missing_and_does_not_imply_total_debt_zero(self):
        state = company_state(source_company(), review())
        self.assertIsNone(state["amounts"]["short_term_borrowings"]["value_yuan"])
        self.assertEqual(state["ratios"]["short_term_borrowings_to_assets"]["status"], "MISSING_SOURCE_AMOUNT")
        self.assertIsNone(state["ratios"]["short_term_borrowings_to_assets"]["value"])
        self.assertIs(state["complete_interest_bearing_debt_verified"], False)

    def test_banks_and_brokers_keep_raw_amounts_but_no_generic_ratios(self):
        for industry in ("J66", "J67"):
            with self.subTest(industry=industry):
                state = company_state(source_company(industry), review())
                self.assertEqual(state["amounts"]["liabilities"]["value_yuan"], "60.00")
                self.assertTrue(all(r["status"] == "NOT_APPLICABLE_FINANCIAL_INSTITUTION" and r["value"] is None for r in state["ratios"].values()))

    def test_total_liabilities_ratio_is_named_without_cash_or_debt_invention(self):
        state = company_state(source_company(), review())
        self.assertEqual(state["ratios"]["total_liabilities_to_assets"]["value"], "0.6")
        self.assertEqual(state["ratios"]["money_funds_to_assets"]["value"], "0.1")
        self.assertIs(state["unrestricted_money_funds_verified"], False)
        self.assertIs(state["agent_signal_enabled"], False)

    def test_zero_denominator_does_not_create_zero_or_infinite_ratio(self):
        amounts = {"a": {"status": "NUMERIC", "value_yuan": "10"}, "b": {"status": "NUMERIC", "value_yuan": "0"}}
        result = observed_ratio(amounts, "a", "b", True)
        self.assertEqual(result["status"], "INVALID_AMOUNT_OR_DENOMINATOR")
        self.assertIsNone(result["value"])

    def test_nonfinite_and_negative_amounts_remain_invalid(self):
        for amount in ("NaN", "Infinity", "-1"):
            result = observed_ratio({"a": {"status": "NUMERIC", "value_yuan": amount}, "b": {"status": "NUMERIC", "value_yuan": "10"}}, "a", "b", True)
            self.assertEqual(result["status"], "INVALID_AMOUNT_OR_DENOMINATOR")

    def test_source_availability_and_timezone_are_enforced(self):
        state = company_state(source_company(), review())
        self.assertIsNone(state_asof(state, "2019-11-01T09:15:00+08:00"))
        self.assertIsNotNone(state_asof(state, "2019-12-08T00:00:00+08:00"))
        with self.assertRaises(ValueError): state_asof(state, "2019-12-08T00:00:00")
        self.assertIs(state["comparison_columns_used_as_earlier_publication_snapshots"], False)


if __name__ == "__main__":
    unittest.main()
