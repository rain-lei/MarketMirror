import unittest
from decimal import Decimal

from research.data_pipeline.audit_issuer_restricted_assets import check_current, check_reconciliation, source_cell


class RestrictedAssetIndependentAuditTest(unittest.TestCase):
    def profile(self, text):
        cell = {"text": text, "bbox": [100, 0, 200, 20], "pdf_page": 1}
        return {"current_column": 1, "header_rows": [[None, cell]], "column_edges": [0, 100, 200]}, cell

    def test_independent_header_rejects_beginning_and_increase(self):
        for text in ["期初余额", "2018年12月31日", "本期增加", "期末外币余额"]:
            profile, cell = self.profile(text)
            with self.assertRaisesRegex(ValueError, "current period"):
                check_current(profile, "BORDERED_PHYSICAL_CELLS", {}, {1: [cell]})

    def test_independent_header_checks_original_text_not_candidate_label(self):
        profile, cell = self.profile("期末账面价值")
        with self.assertRaisesRegex(ValueError, "text differs"):
            check_current(profile, "BORDERED_PHYSICAL_CELLS", {}, {1: [{**cell, "text": "期初账面价值"}]})

    def test_geometry_format_cannot_silently_fall_back(self):
        _, cell = self.profile("100")
        with self.assertRaisesRegex(ValueError, "unregistered"):
            source_cell(cell, "CLOSEST_EQUAL_VALUE", {}, {})

    def test_missing_row_not_zero_even_if_other_values_close(self):
        parsed = {"total": {"reported_value": "100"}, "issues": [], "reconciliation": {"status": "EXACT_SOURCE_TOTAL_MATCH", "sum_as_reported": "100"}}
        with self.assertRaisesRegex(ValueError, "missingness"):
            check_reconciliation(parsed, [None, Decimal(100)], Decimal(100))

    def test_blank_printed_total_is_not_absent_total(self):
        parsed = {"total": {"reported_value": None}, "issues": [], "reconciliation": {"status": "NOT_CHECKABLE_MISSING_SOURCE_VALUE", "sum_as_reported": None}}
        self.assertEqual(check_reconciliation(parsed, [Decimal(100)], None), "NOT_CHECKABLE_MISSING_SOURCE_VALUE")

    def test_duplicate_cash_labels_not_deduplicated_to_smaller_total(self):
        parsed = {"total": {"reported_value": "30"}, "issues": [], "reconciliation": {"status": "EXACT_SOURCE_TOTAL_MATCH", "sum_as_reported": "30"}}
        self.assertEqual(check_reconciliation(parsed, [Decimal(10), Decimal(20)], Decimal(30)), "EXACT_SOURCE_TOTAL_MATCH")

    def test_prior_scope_issues_cannot_be_cleared_by_a_total(self):
        parsed = {"total": {"reported_value": "30"}, "issues": ["hierarchical_or_mixed_scope_asset_rows"], "reconciliation": {"status": "EXACT_SOURCE_TOTAL_MATCH", "sum_as_reported": "30"}}
        with self.assertRaisesRegex(ValueError, "differs"):
            check_reconciliation(parsed, [Decimal(30)], Decimal(30))


if __name__ == "__main__":
    unittest.main()
