import copy
import unittest

from research.data_pipeline.build_issuer_restricted_asset_states import construct_company


class RestrictedAssetStatesTest(unittest.TestCase):
    def data(self):
        row = {"label_as_printed": "货币资金", "component_kind": "RESTRICTED_MONEY_FUNDS_AS_REPORTED", "ending_value": {"reported_value": "10", "source_cell": None, "status": "NUMERIC"},
               "value_at_reported_unit_scale": "100000", "label_cell": None, "reason_as_printed": "保证金", "reason_cells": []}
        parsed = {"unit": {"currency": "CNY_EXPLICIT"}, "rows": [row], "total": {"reported_value": "10"}, "issues": [], "reconciliation": {"status": "EXACT_SOURCE_TOTAL_MATCH"}}
        c = {"stock_code": "123456", "historical_short_name": "示例", "source_pdf_path": "source.pdf", "source_pdf_sha256": "a"*64, "report_metadata": {"report_end_date": "2019-06-30"},
             "literal_reader_status": "PASS_LITERAL_PAGE_MAPS", "status": "CANDIDATES", "tables": [{"parsed": parsed, "frames": [{"pdf_page": 2}], "scope": "CONSOLIDATED_EXPLICIT", "scope_evidence": None, "source_reader_qc_required": False}]}
        return c, {"stock_code": "123456", "tables": [{"table_index": 0, "status": "PASS_SOURCE_CELLS_AND_CALCULATION"}]}

    def test_adopted_fact_does_not_create_spendable_cash_or_cross_period_ratio(self):
        c, a = self.data()
        s = construct_company(c, a)
        self.assertEqual(s["tables"][0]["rows"][0]["verified_ending_value_yuan"], "100000")
        self.assertIsNone(s["unrestricted_money_funds"])
        self.assertIsNone(s["cash_to_debt_ratio"])
        self.assertIsNone(s["financing_impact_coefficient"])
        self.assertFalse(s["agent_signal_enabled"])

    def test_reader_diagnostic_does_not_become_verified_money(self):
        c, a = self.data()
        c["literal_reader_status"] = "READER_DIAGNOSTIC_REVIEW_REQUIRED"
        s = construct_company(c, a)
        self.assertEqual(s["status"], "SOURCE_READER_QC_NOT_ADOPTED")
        self.assertIsNone(s["tables"][0]["rows"][0]["verified_ending_value_reported_units"])

    def test_unknown_currency_keeps_source_value_without_cny(self):
        c, a = self.data()
        c["tables"][0]["parsed"]["unit"]["currency"] = "CURRENCY_UNSPECIFIED"
        row = construct_company(c, a)["tables"][0]["rows"][0]
        self.assertEqual(row["verified_ending_value_reported_units"], "10")
        self.assertIsNone(row["verified_ending_value_yuan"])

    def test_missing_independent_table_not_silently_accepted(self):
        c, a = self.data()
        a["tables"] = []
        s = construct_company(c, a)
        self.assertTrue(s["independent_table_coverage_incomplete"])
        self.assertFalse(s["tables"][0]["source_number_checks_accepted"])

    def test_duplicate_audit_association_rejected(self):
        c, a = self.data()
        a["tables"].append(copy.deepcopy(a["tables"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            construct_company(c, a)

    def test_wrong_issuer_audit_rejected(self):
        c, a = self.data()
        a["stock_code"] = "654321"
        with self.assertRaisesRegex(ValueError, "identity"):
            construct_company(c, a)

    def test_source_blank_not_zero_even_in_independently_checked_table(self):
        c, a = self.data()
        c["tables"][0]["parsed"]["rows"][0]["ending_value"].update(reported_value=None, status="REPORTED_BLANK")
        row = construct_company(c, a)["tables"][0]["rows"][0]
        self.assertIsNone(row["verified_ending_value_reported_units"])
        self.assertFalse(row["source_number_independently_verified"])

    def test_true_zero_preserved_without_cash_availability(self):
        c, a = self.data()
        row = c["tables"][0]["parsed"]["rows"][0]
        row["ending_value"]["reported_value"] = "0.00"
        row["value_at_reported_unit_scale"] = "0.00"
        actual = construct_company(c, a)["tables"][0]["rows"][0]
        self.assertEqual(actual["verified_ending_value_yuan"], "0.00")
        self.assertIsNone(actual["cash_available_for_trading"])


if __name__ == "__main__":
    unittest.main()
