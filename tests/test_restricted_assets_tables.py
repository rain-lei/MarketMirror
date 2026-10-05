import unittest

from research.data_pipeline import restricted_assets_tables as reader


def cell(text, column, row):
    return {"text": text, "bbox": [column*100, row*20, (column+1)*100, (row+1)*20], "pdf_page": 1}


def frame(lines):
    return {"rows": [[cell(text, j, i) if text is not None else None for j, text in enumerate(row)] for i, row in enumerate(lines)],
            "column_count": 3, "column_edges": [0, 100, 200, 300], "pdf_page": 1, "bbox": [0, 0, 300, len(lines)*20], "page_height": 800}


class RestrictedAssetsTablesTest(unittest.TestCase):
    def parse(self, rows):
        source = frame([["项目", "期末账面价值", "受限原因"], *rows])
        return reader.parse(source, reader.profile(source), {"scale": 1})

    def test_restriction_heading_is_not_a_narrative_mention(self):
        self.assertTrue(reader.heading("81、所有权或使用权受到限制的资产"))
        self.assertFalse(reader.heading("说明：所有权或使用权受到限制的资产较多"))

    def test_notes_scope_requires_explicit_consolidated_or_parent_heading(self):
        self.assertEqual(reader.notes_scope("五、合并财务报表主要项目注释(续)"), "CONSOLIDATED_EXPLICIT")
        self.assertEqual(reader.notes_scope("十二、母公司财务报表项目注释"), "PARENT_EXPLICIT")
        self.assertIsNone(reader.notes_scope("本公司及子公司在合并财务报表附注披露"))

    def test_current_column_ignores_beginning_and_movement_values(self):
        source = frame([["项目", "期初余额", "期末余额"], ["货币资金", "90", "30"]])
        self.assertEqual(reader.profile(source)["current_column"], 2)
        self.assertEqual(reader.parse(source, reader.profile(source), {"scale": 1})["rows"][0]["ending_value"]["reported_value"], "30")

    def test_comparison_only_or_ambiguous_current_headers_not_accepted(self):
        self.assertIsNone(reader.profile(frame([["项目", "2018年12月31日", "受限原因"], ["货币资金", "10", "保证金"]])))
        self.assertIsNone(reader.profile(frame([["项目", "期末余额", "期末账面价值"], ["货币资金", "10", "20"]])))

    def test_true_zero_kept_and_blank_not_zero(self):
        parsed = self.parse([["货币资金", "0.00", ""], ["存货", "", ""], ["合计", "0.00", ""]])
        self.assertEqual(parsed["rows"][0]["ending_value"]["reported_value"], "0.00")
        self.assertIsNone(parsed["rows"][1]["ending_value"]["reported_value"])
        self.assertEqual(parsed["reconciliation"]["status"], "NOT_CHECKABLE_MISSING_SOURCE_VALUE")

    def test_dash_and_reason_numbers_not_ending_money(self):
        parsed = self.parse([["货币资金", "—", "参见附注1，期限6个月"], ["合计", "100", ""]])
        self.assertIsNone(parsed["rows"][0]["ending_value"]["reported_value"])
        self.assertIn("期限6个月", parsed["rows"][0]["reason_as_printed"])

    def test_duplicate_asset_labels_are_preserved_not_overwritten(self):
        parsed = self.parse([["货币资金", "10", "保证金"], ["货币资金", "20", "质押"], ["合计", "30", ""]])
        self.assertEqual(len(parsed["rows"]), 2)
        self.assertEqual(parsed["reconciliation"]["status"], "EXACT_SOURCE_TOTAL_MATCH")

    def test_negative_book_value_is_preserved_with_qc(self):
        parsed = self.parse([["货币资金", "(10)", ""], ["合计", "-10", ""]])
        self.assertEqual(parsed["rows"][0]["ending_value"]["reported_value"], "-10")
        self.assertIn("negative_ending_book_value_requires_review", parsed["issues"])

    def test_partial_table_not_normalized_or_filled_to_total(self):
        parsed = self.parse([["货币资金", "30", ""], ["合计", "100", ""]])
        self.assertEqual(parsed["reconciliation"]["status"], "SOURCE_TOTAL_MISMATCH")
        self.assertNotIn("share", parsed["rows"][0])

    def test_cash_subcategory_does_not_become_all_cash(self):
        self.assertEqual(reader.component("其他货币资金"), "RESTRICTED_CASH_SUBCATEGORY_AS_REPORTED")
        self.assertEqual(reader.component("股权资产"), "RESTRICTED_NONCASH_OR_OTHER_ASSET_AS_REPORTED")

    def test_currency_unknown_kept_without_assuming_cny(self):
        self.assertEqual(reader.unit([{"text": "单位：元"}])["currency"], "CURRENCY_UNSPECIFIED")
        self.assertEqual(reader.unit([{"text": "单位：万元 币种：人民币"}])["scale"], 10000)
        self.assertEqual(reader.unit([])["status"], "UNIT_NOT_FOUND")

    def test_continuation_carries_missing_and_prior_issues(self):
        first = self.parse([["货币资金", "", ""]])
        source = frame([["固定资产", "100", ""], ["合计", "100", ""]])
        role = {"current_column": 1, "reason_column": 2, "header_rows": [], "column_edges": [0, 100, 200, 300]}
        parsed = reader.parse(source, role, {"scale": 1}, first["rows"], None, ["prior_label_unresolved"])
        self.assertEqual(parsed["reconciliation"]["status"], "NOT_CHECKABLE_MISSING_SOURCE_VALUE")
        self.assertIn("prior_label_unresolved", parsed["issues"])

    def test_new_section_prevents_false_continuation(self):
        a = {"pdf_page": 1, "bbox": [0, 0, 300, 720]}
        b = {"pdf_page": 2, "bbox": [0, 90, 300, 150]}
        row = {"pdf_page": 2, "bbox": [0, 30, 300, 50], "text": "82、外币货币性项目"}
        self.assertFalse(reader.continuation_safe(a, b, [row], {"pdf_page": 1}))

    def test_unit_change_prevents_false_continuation(self):
        a = {"pdf_page": 1, "bbox": [0, 0, 300, 720]}
        b = {"pdf_page": 2, "bbox": [0, 90, 300, 150]}
        row = {"pdf_page": 2, "bbox": [0, 30, 300, 50], "text": "单位：万元"}
        self.assertFalse(reader.continuation_safe(a, b, [row], {"pdf_page": 1}, reader.unit([{"text": "单位：元"}])))


if __name__ == "__main__":
    unittest.main()
