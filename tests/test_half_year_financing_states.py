"""Financing facts require same PDF, period, scope and independently read cells."""

import copy
import unittest
from decimal import Decimal

from research.data_pipeline.half_year_balance_context import extract_company
from research.data_pipeline.half_year_financing_states import build_company, proportion, state_asof, strict_issuer_map
from tests.test_financial_balance_sheet import row, sheet


def fixture():
    rows = sheet(current="2019年6月30日")
    rows[2] = row("单位：元币种：人民币", y=80)
    result = extract_company(rows, "2019-06-30")
    company = {**result, "stock_code": "a", "historical_short_name": "A", "industry_code": "C",
               "source_pdf_sha256": "source", "source_pdf_path": "source.pdf", "literal_reader_status": "PASS_LITERAL_PAGE_MAPS",
               "report_metadata": {"stock_code": "a", "report_end_date": "2019-06-30", "available_at_proxy": "2019-09-19T00:00:00+08:00"}}
    audit = {"stock_code": "a", "source_pdf_sha256": "source", "selected_candidate_index": 0,
             "literal_reader_status": "PASS_LITERAL_PAGE_MAPS", "status": "PASS",
             "independent_currency_declarations": [{"status": "PASS_EXPLICIT_CNY_DECLARATION"}],
             "independent_note_presentation_declarations": []}
    restricted = {k: company[k] for k in ("stock_code", "source_pdf_sha256", "source_pdf_path", "report_metadata", "literal_reader_status")}
    restricted["tables"] = [{"table_index": 0, "scope": "CONSOLIDATED_EXPLICIT", "source_number_checks_accepted": True,
                              "unit": {"currency": "CNY_EXPLICIT", "reported_unit": "元", "scale": 1, "evidence": {}},
                              "rows": [{"label_as_printed": "货币资金", "component_kind": "RESTRICTED_MONEY_FUNDS_AS_REPORTED",
                                        "source_status": "NUMERIC", "source_cell": {"pdf_page": 10}, "reason_as_printed": "保证金",
                                        "source_number_independently_verified": True, "verified_ending_value_reported_units": "2.00"}]}]
    return company, audit, restricted


class HalfYearFinancingStateTests(unittest.TestCase):
    def test_correct_same_period_row_fraction_is_limited(self):
        result = build_company(*fixture())
        self.assertEqual(result["restricted_cash_rows"][0]["restricted_row_to_money_funds"], "0.2")
        self.assertEqual(result["restricted_cash_rows"][0]["restricted_row_to_assets"], "0.02")
        self.assertIsNone(result["unrestricted_money_funds"])
        self.assertIsNone(result["cash_available_for_trading"])
        self.assertIs(result["agent_signal_enabled"], False)

    def test_different_source_pdf_is_rejected(self):
        company, audit, restricted = fixture()
        restricted["source_pdf_sha256"] = "other source"
        with self.assertRaises(ValueError):
            build_company(company, audit, restricted)

    def test_third_quarter_cannot_replace_half_year_period(self):
        company, audit, restricted = fixture()
        restricted["report_metadata"] = {**company["report_metadata"], "report_end_date": "2019-09-30"}
        with self.assertRaises(ValueError):
            build_company(company, audit, restricted)

    def test_wrong_independent_candidate_index_is_rejected(self):
        company, audit, restricted = fixture()
        audit["selected_candidate_index"] = 1
        with self.assertRaises(ValueError):
            build_company(company, audit, restricted)

    def test_parent_scope_never_becomes_consolidated_fraction(self):
        company, audit, restricted = fixture()
        restricted["tables"][0]["scope"] = "PARENT_EXPLICIT"
        result = build_company(company, audit, restricted)
        self.assertIsNone(result["max_verified_restricted_money_fund_row_to_money_funds"])
        self.assertEqual(result["restricted_cash_rows"][0]["status"], "RESTRICTION_SCOPE_UNVERIFIED_NOT_JOINED")

    def test_unit_only_currency_does_not_join_by_assumption(self):
        company, audit, restricted = fixture()
        restricted["tables"][0]["unit"]["currency"] = "CURRENCY_UNSPECIFIED"
        result = build_company(company, audit, restricted)
        self.assertIsNone(result["restricted_cash_rows"][0]["restricted_row_to_money_funds"])

    def test_independently_read_earlier_universal_unit_allows_join(self):
        company, audit, restricted = fixture()
        restricted["tables"][0]["unit"]["currency"] = "CURRENCY_UNSPECIFIED"
        audit["independent_note_presentation_declarations"] = [{"status": "PASS_EXPLICIT_CNY_DECLARATION", "reported_unit": "元",
                                                               "pdf_page": 2, "source_rectangle": [50, 50, 500, 65]}]
        result = build_company(company, audit, restricted)
        self.assertEqual(result["restricted_cash_rows"][0]["restricted_row_to_money_funds"], "0.2")

    def test_wrong_or_later_presentation_cannot_fill_currency(self):
        for unit, page in (("万元", 2), ("元", 12)):
            with self.subTest(unit=unit, page=page):
                company, audit, restricted = fixture()
                restricted["tables"][0]["unit"]["currency"] = "CURRENCY_UNSPECIFIED"
                audit["independent_note_presentation_declarations"] = [{"status": "PASS_EXPLICIT_CNY_DECLARATION", "reported_unit": unit,
                                                                       "pdf_page": page, "source_rectangle": [50, 50, 500, 65]}]
                self.assertIsNone(build_company(company, audit, restricted)["restricted_cash_rows"][0]["restricted_row_to_money_funds"])

    def test_explicit_different_scales_convert_before_fraction(self):
        company, audit, restricted = fixture()
        restricted["tables"][0]["unit"].update(reported_unit="万元", scale=10000)
        restricted["tables"][0]["rows"][0]["verified_ending_value_reported_units"] = "0.0002"
        value = build_company(company, audit, restricted)["restricted_cash_rows"][0]["restricted_row_to_money_funds"]
        self.assertEqual(Decimal(value), Decimal("0.2"))

    def test_duplicate_rows_are_kept_separate_and_not_summed(self):
        company, audit, restricted = fixture()
        second = copy.deepcopy(restricted["tables"][0]["rows"][0])
        second.update(verified_ending_value_reported_units="3.00", reason_as_printed="冻结")
        restricted["tables"][0]["rows"].append(second)
        result = build_company(company, audit, restricted)
        self.assertEqual(len(result["restricted_cash_rows"]), 2)
        self.assertEqual(result["max_verified_restricted_money_fund_row_to_money_funds"], "0.3")
        self.assertIsNone(result["total_restricted_money_funds"])

    def test_cash_subcategory_stays_distinct_from_money_funds(self):
        company, audit, restricted = fixture()
        restricted["tables"][0]["rows"][0]["component_kind"] = "RESTRICTED_CASH_SUBCATEGORY_AS_REPORTED"
        result = build_company(company, audit, restricted)
        self.assertIsNone(result["max_verified_restricted_money_fund_row_to_money_funds"])
        self.assertEqual(result["max_verified_cash_subcategory_row_to_money_funds"], "0.2")

    def test_cash_restriction_exceeding_total_is_qc(self):
        company, audit, restricted = fixture()
        restricted["tables"][0]["rows"][0]["verified_ending_value_reported_units"] = "20.00"
        result = build_company(company, audit, restricted)
        self.assertIsNone(result["restricted_cash_rows"][0]["restricted_row_to_money_funds"])
        self.assertEqual(result["restricted_cash_rows"][0]["status"], "RESTRICTION_ROW_EXCEEDS_TOTAL_MONEY_FUNDS_REVIEW_REQUIRED")

    def test_missing_debt_remains_missing_not_complete_debt_zero(self):
        result = build_company(*fixture())
        self.assertIsNone(result["debt_components"]["bonds_payable"]["verified_value_reported_units"])
        self.assertIsNone(result["complete_interest_bearing_debt"])
        self.assertIsNone(result["cash_to_complete_interest_bearing_debt"])

    def test_upstream_reader_warning_blocks_all_accepted_fields(self):
        company, audit, restricted = fixture()
        for item in (company, audit, restricted):
            item["literal_reader_status"] = "READER_DIAGNOSTIC_REVIEW_REQUIRED"
        result = build_company(company, audit, restricted)
        self.assertEqual(result["status"], "SOURCE_READER_QC_NOT_ADOPTED")
        self.assertEqual(result["balance_fields"], {})

    def test_failed_independent_read_blocks_adoption(self):
        company, audit, restricted = fixture()
        audit["status"] = "FAIL_INDEPENDENT_SOURCE_CHECKS_NOT_ADOPTED"
        self.assertEqual(build_company(company, audit, restricted)["balance_fields"], {})

    def test_real_zero_is_numeric_negative_and_zero_denominator_are_qc(self):
        self.assertEqual(proportion("0.00", "10")["value"], "0.00")
        for numerator, denominator in ((None, "10"), ("-2", "10"), ("2", "0")):
            self.assertIsNone(proportion(numerator, denominator)["value"])

    def test_financial_industry_cannot_use_generic_company_debt_ratios(self):
        company, audit, restricted = fixture()
        company["industry_code"] = "J67"
        result = build_company(company, audit, restricted)
        self.assertTrue(result["balance_fields"])
        self.assertEqual(result["balance_ratios"], {})
        self.assertEqual(result["restricted_cash_rows"], [])

    def test_revised_disclosure_stays_unavailable_before_own_date(self):
        state = build_company(*fixture())
        self.assertIsNone(state_asof(state, "2019-08-31T23:59:59+08:00"))
        self.assertIsNotNone(state_asof(state, "2019-09-19T00:00:00+08:00"))
        with self.assertRaises(ValueError):
            state_asof(state, "2019-09-19T00:00:00")

    def test_duplicate_issuer_join_is_rejected(self):
        with self.assertRaises(ValueError):
            strict_issuer_map([{"stock_code": "a"}, {"stock_code": "a"}])


if __name__ == "__main__":
    unittest.main()
