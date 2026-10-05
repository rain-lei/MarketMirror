"""Prevent unit, consolidation and table-boundary contamination."""

import unittest

from research.data_pipeline.financial_report_pages import locate_balance_sheets, statement_heading


class FinancialReportPageTests(unittest.TestCase):
    def test_narrative_balance_sheet_reference_is_not_a_heading(self):
        for line in ("资产负债表日后事项", "本公司资产负债表中", "2019年第三季度报告"):
            with self.subTest(line=line): self.assertIsNone(statement_heading(line))

    def test_numbered_and_chinese_numbered_headings(self):
        self.assertEqual(statement_heading("1、合并资产负债表"),("资产负债表","consolidated"))
        self.assertEqual(statement_heading("（一）、母公司资产负债表"),("资产负债表","parent"))

    def test_four_column_combined_scope_is_not_simple_consolidated(self):
        for title in ("合并及母公司资产负债表", "合并资产负债表和资产负债表"):
            with self.subTest(title=title): self.assertEqual(statement_heading(title),("资产负债表","combined_consolidated_and_parent"))

    def test_bank_units_thousand_and_million_are_distinct(self):
        for text,scale in (("货币单位：人民币百万元",1000000),("金额单位为人民币千元",1000)):
            item=locate_balance_sheets([f"资产负债表\n2019年9月30日\n{text}\n资产总计"])["balance_sheet_sections"][0]
            self.assertEqual(item["unit_evidence"][0]["reported_multiplier_to_yuan"],scale)

    def test_parent_unit_cannot_overwrite_consolidated_unit(self):
        value=locate_balance_sheets(["合并资产负债表\n单位：元\n货币资金\n母公司资产负债表\n单位：万元\n货币资金"])
        self.assertEqual([x["distinct_reported_units"] for x in value["balance_sheet_sections"]],[["元"],["万元"]])
        self.assertEqual(value["review_queue_status"],"SINGLE_NON_PARENT_SECTION_REVIEW_REQUIRED")

    def test_later_profit_statement_unit_does_not_fill_missing_balance_unit(self):
        value=locate_balance_sheets(["合并资产负债表\n资产总计\n合并利润表\n单位：万元\n营业收入"])
        self.assertEqual(value["balance_sheet_sections"][0]["unit_status"],"MISSING_UNIT")

    def test_conflicting_units_remain_review_state(self):
        section=locate_balance_sheets(["资产负债表\n单位：千元\n单位：元\n资产总计"])["balance_sheet_sections"][0]
        self.assertEqual(section["unit_status"],"CONFLICTING_UNITS")

    def test_split_date_headers_are_kept_as_evidence(self):
        section=locate_balance_sheets(["合并资产负债表\n2019年\n9月30日\n2018年\n12月31日\n单位：元"])["balance_sheet_sections"][0]
        self.assertTrue(section["current_period_header_found"])
        self.assertTrue(section["prior_year_header_found"])

    def test_section_continues_across_pages_but_stops_at_parent(self):
        value=locate_balance_sheets(["合并资产负债表\n单位：元\n资产总计", "负债合计\n母公司资产负债表\n单位：元"])
        section=value["balance_sheet_sections"][0]
        self.assertEqual(section["end_pdf_page"],2)
        self.assertTrue(any(row["text"]=="负债合计" for row in section["statement_lines"]))
        self.assertFalse(any(row["text"]=="母公司资产负债表" for row in section["statement_lines"]))

    def test_multiple_non_parent_tables_are_not_silently_selected(self):
        value=locate_balance_sheets(["合并资产负债表\n单位：元\n合并资产负债表\n单位：元"])
        self.assertEqual(value["review_queue_status"],"MULTIPLE_NON_PARENT_SECTIONS_REVIEW_REQUIRED")

    def test_parent_only_report_does_not_become_consolidated(self):
        value=locate_balance_sheets(["母公司资产负债表\n单位：元"])
        self.assertEqual(value["review_queue_status"],"NO_LITERAL_BALANCE_SHEET_HEADING")
        self.assertEqual(value["section_scope_counts"]["parent"],1)


if __name__ == "__main__":
    unittest.main()
