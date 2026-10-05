import unittest

from research.data_pipeline.issuer_operating_states import ratio, state_asof, current_part
from research.data_pipeline.audit_issuer_operating_statements import (
    clean_label, flow_period, independent_identities, flow_columns, bank_flow_read, verified_scope_title,
)
from types import SimpleNamespace


def cell(value, text=None):
    return {"status": "NUMERIC", "value_yuan": value, "reported_value": value, "source_cell_text": text or value}


class OperatingStateTests(unittest.TestCase):
    def test_negative_operating_cashflow_ratio_is_observed(self):
        result = ratio({"cash": cell("-10"), "assets": cell("100")}, "cash", "assets", True, True)
        self.assertEqual(result["value"], "-0.1")
        self.assertEqual(result["status"], "OBSERVED_SIGNED_RATIO")

    def test_negative_asset_is_invalid_not_clipped(self):
        result = ratio({"cash": cell("-10"), "assets": cell("100")}, "cash", "assets", True, False)
        self.assertIsNone(result["value"])
        self.assertEqual(result["status"], "INVALID_AMOUNT_OR_DENOMINATOR")

    def test_missing_amount_and_zero_denominator_are_not_observed(self):
        for numerator, denominator, wanted in (({"status": "REPORTED_BLANK", "value_yuan": None}, cell("100"), "MISSING_SOURCE_AMOUNT"),
                                               (cell("10"), cell("0"), "INVALID_AMOUNT_OR_DENOMINATOR")):
            self.assertEqual(ratio({"n": numerator, "d": denominator}, "n", "d", True, True)["status"], wanted)

    def test_financial_institution_has_no_generic_ratio(self):
        self.assertEqual(ratio({}, "n", "d", False, True)["status"], "NOT_APPLICABLE_GENERIC_NONFINANCIAL_RATIO")

    def test_revised_source_visibility_uses_own_later_publication(self):
        state = {"status": "INDEPENDENT_OPERATING_EXTRACTION_AGREEMENT", "available_at_proxy": "2019-12-08T00:00:00+08:00"}
        self.assertIsNone(state_asof(state, "2019-11-01T09:15:00+08:00"))
        self.assertIs(state_asof(state, "2019-12-08T00:00:00+08:00"), state)

    def test_naive_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            state_asof({"status": "INDEPENDENT_OPERATING_EXTRACTION_AGREEMENT", "available_at_proxy": "2019-12-08T00:00:00+08:00"}, "2020-01-22T10:00:00")

    def test_quarter_or_parent_export_is_rejected(self):
        for scope, start in (("parent", "2019-01-01"), ("consolidated", "2019-07-01")):
            statement = {"status": "UNIQUE_SOURCE_DATED_YTD_CANDIDATE", "selected_candidate_index": 0,
                         "candidates": [{"status": "SOURCE_DATED_OPERATING_STATEMENT", "current_ytd_column_index": 0, "column_mapping": {"columns": [{"scope": scope, "period_start": start, "period_end": "2019-09-30"}]}}]}
            with self.assertRaises(ValueError):
                current_part(statement, {"report_end_date": "2019-09-30"})

    def test_independent_period_parser_reads_bank_dates_and_quarter(self):
        self.assertEqual(flow_period("自2019年1月\n1日至2019年\n9月30日止"), ("2019-01-01", "2019-09-30"))
        self.assertEqual(flow_period("2019年第三季\n度（7-9月）"), ("2019-07-01", "2019-09-30"))

    def test_independent_relative_prior_remains_undeclared(self):
        table = SimpleNamespace(values=[["项目", "本期发生额", "上期发生额"]],
                                rows=[SimpleNamespace(cells=[(0, 0, 100, 10), (100, 0, 200, 10), (200, 0, 300, 10)])])
        table.extract = lambda: table.values
        columns = flow_columns(table, 2, "consolidated", "合并年初到报告期末利润表", 2019)
        self.assertEqual(columns[0]["period_start"], "2019-01-01")
        self.assertIsNone(columns[1]["period_end"])

    def test_independent_signed_expense_is_not_double_subtracted(self):
        fields = {"profit_total": [cell("100")], "income_tax_expense": [cell("-20", "(20)")],
                  "net_profit": [cell("80")], "net_profit_attributable_to_parent": [cell("80")], "minority_profit": [cell("0")]}
        self.assertTrue(all(c["status"] == "EXACT" for c in independent_identities(fields, 1, "利润表")))

    def test_independent_loss_qualifier_does_not_remove_field_role(self):
        self.assertEqual(clean_label("2.终止经营净利润（净亏损以“-”号填列）"), "终止经营净利润")
        self.assertEqual(clean_label("三、营业利润（续）"), "营业利润（续）")

    def test_unselected_bank_cash_fragment_does_not_consume_next_subtotal(self):
        words = [{"text": text, "x0": x0, "x1": x1, "top": y, "bottom": y + 10} for text, x0, x1, y in
                 (("现金", 85, 105, 500), ("经营活动现金流入小计", 85, 175, 520), ("100", 270, 295, 520))]
        page = SimpleNamespace(height=850, width=600, extract_words=lambda **kwargs: words,
                               extract_text=lambda **kwargs: "单位：元", close=lambda: None)
        page.crop = lambda region: SimpleNamespace(extract_text=lambda **kwargs: "2019年1-9月")
        document = SimpleNamespace(pages=[page])
        anchor = {"complete_pdf_pages_reviewed": [1], "numeric_column_boundaries_by_page": {"1": [235, 310]},
                  "period_header_regions_by_page": {"1": [[235, 190, 310, 240]]},
                  "columns_reviewed_left_to_right": [{"scope": "consolidated", "period_start": "2019-01-01", "period_end": "2019-09-30"}]}
        labels = {"operating_cash_inflow": ["经营活动现金流入小计"], "cash_equivalent_net_change": ["现金及现金等价物净增加额"]}
        read = bank_flow_read(document, {}, labels, anchor)
        self.assertEqual(read["fields"]["operating_cash_inflow"][0]["cells"][0]["reported_value"], "100")

    def test_independent_source_caption_cannot_be_relabeled_consolidated(self):
        document = SimpleNamespace(pages=[SimpleNamespace(extract_text_lines=lambda: [{"top": 40, "text": "母公司利润表"}])])
        candidate = {"heading_evidence": [{"pdf_page": 1, "bbox": [50, 40, 160, 50], "text": "母公司利润表"}], "declared_heading": {"scope": "consolidated"}}
        with self.assertRaises(ValueError):
            verified_scope_title(document, candidate)
