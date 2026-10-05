"""Failure cases for real statement periods, signs, pagination and missing cells."""

import unittest

from research.data_pipeline.financial_balance_sheet import geometry_rows
from research.data_pipeline.financial_operating_statements import (
    CASH_FIELDS, PROFIT_FIELDS, ASSET_FIELDS, checks, extract_additional_assets,
    extract_flows, heading, monetary_words, number, parse_statement, period, read_fields,
)


def row(label, values=(), y=100, page=1, edges=(320, 500)):
    words = [(50, y, 160, y + 10, label)] if label else []
    words += [(edge - 55, y, edge, y + 10, value) for edge, value in zip(edges, values) if value is not None]
    return geometry_rows(words, page, 850)[0]


def profit(tax="20.00", net="80.00", title="合并年初到报告期末利润表", dates=("本期发生额", "上期发生额")):
    return [row(title, y=40), row("编制单位：某公司2019年1-9月", y=60), row("单位：元", y=80), row("项目", dates, y=100)] + [
        row(label, (value, value), y=y) for y, label, value in ((120, "营业总收入", "200.00"),
        (140, "营业收入", "200.00"), (160, "营业利润", "100.00"), (180, "利润总额", "100.00"),
        (200, "所得税费用", tax), (220, "净利润", net), (240, "归属于母公司所有者的净利润", net), (260, "少数股东损益", "0.00"))]


def cash(parenthesized=False, fx="1.00"):
    out = "（20.00）" if parenthesized else "20.00"
    amounts = ["10.00", out, "-10.00", "30.00", "10.00", "20.00", "5.00", "15.00", "-10.00", fx, "1.00", "10.00", "11.00"]
    return [row("合并年初到报告期末现金流量表", y=40), row("单位：元", y=60), row("项目", ("本期发生额", "上期发生额"), y=80)] + [
        row(aliases[0], (value, value), y=100 + index * 20) for index, ((_, aliases), value) in enumerate(zip(CASH_FIELDS.items(), amounts))]


class OperatingStatementTests(unittest.TestCase):
    def test_relative_prior_dates_are_unknown_not_invented_prior_year(self):
        rows = profit()
        result = parse_statement(rows, heading(rows[0]["text"]), 2019)
        self.assertEqual(result["status"], "SOURCE_DATED_OPERATING_STATEMENT")
        columns = result["column_mapping"]["columns"]
        self.assertEqual(columns[0]["period_start"], "2019-01-01")
        self.assertIsNone(columns[1]["period_start"])
        self.assertIsNone(columns[1]["period_end"])

    def test_actual_explicit_column_dates_override_title_position(self):
        rows = profit(title="合并利润表", dates=("2018年前三季度", "2019年前三季度"))
        result = parse_statement(rows, heading(rows[0]["text"]), 2019)
        self.assertEqual(result["current_ytd_column_index"], 1)

    def test_quarter_is_not_promoted_by_ytd_preparation_date(self):
        rows = profit(title="合并本报告期利润表")
        result = parse_statement(rows, heading(rows[0]["text"]), 2019)
        self.assertEqual(result["status"], "NO_UNIQUE_CURRENT_YTD_NONPARENT_COLUMN")

    def test_parent_only_table_never_becomes_group(self):
        self.assertEqual(extract_flows(profit(title="母公司年初到报告期末利润表"), 2019, 1)["利润表"]["candidates"], [])

    def test_two_current_tables_need_review(self):
        flows = extract_flows(profit() + profit(), 2019, 1)
        self.assertEqual(flows["利润表"]["status"], "MULTIPLE_SOURCE_DATED_YTD_CANDIDATES")

    def test_parenthesized_tax_is_signed_expense(self):
        rows = profit(tax="（20.00）")
        result = parse_statement(rows, heading(rows[0]["text"]), 2019)
        self.assertEqual(result["status"], "SOURCE_DATED_OPERATING_STATEMENT")
        self.assertEqual(result["fields"]["income_tax_expense"]["cells"][0]["reported_value"], "-20.00")

    def test_ordinary_negative_tax_benefit_increases_profit(self):
        rows = profit(tax="-5.00", net="105.00")
        self.assertEqual(parse_statement(rows, heading(rows[0]["text"]), 2019)["status"], "SOURCE_DATED_OPERATING_STATEMENT")

    def test_wrong_profit_identity_blocks_selection(self):
        rows = profit(net="79.00")
        self.assertEqual(parse_statement(rows, heading(rows[0]["text"]), 2019)["status"], "SOURCE_STATEMENT_IDENTITY_MISMATCH")

    def test_parenthesized_outflow_is_added_as_signed_presentation(self):
        rows = cash(parenthesized=True)
        result = parse_statement(rows, heading(rows[0]["text"]), 2019)
        self.assertEqual(result["status"], "SOURCE_DATED_OPERATING_STATEMENT")
        self.assertEqual(result["fields"]["operating_net_cashflow"]["cells"][0]["reported_value"], "-10.00")

    def test_missing_fx_is_uncheckable_and_not_zero(self):
        rows = cash(fx=None)
        result = parse_statement(rows, heading(rows[0]["text"]), 2019)
        self.assertEqual(result["status"], "SOURCE_DATED_OPERATING_STATEMENT")
        self.assertEqual(result["fields"]["fx_effect_on_cash"]["cells"][0]["status"], "REPORTED_BLANK")
        check = next(c for c in result["identity_checks"] if c["name"] == "three_activities_plus_fx_change")
        self.assertEqual(check["status"], "NOT_CHECKABLE_MISSING_SOURCE_VALUE")

    def test_date_day_in_closing_label_is_not_a_money_column(self):
        words = [(50, 100, 55, 110, "9"), (60, 100, 160, 110, "月30日的现金及现金等价物余额"),
                 (280, 100, 320, 110, "10.00"), (460, 100, 500, 110, "20.00")]
        result = geometry_rows(words, 1, 850)[0]
        self.assertEqual(len(monetary_words(result)), 2)
        field = read_fields([result], CASH_FIELDS, {1: [320, 500]}, 1)["cash_equivalent_closing"]
        self.assertEqual(field["cells"][0]["reported_value"], "10.00")

    def test_blank_dash_and_explicit_zero_stay_distinct(self):
        fields = read_fields([row("其他流动资产", ("--", "0.00"))], ASSET_FIELDS, {1: [320, 500]}, 1)
        cells = fields["other_current_assets"]["cells"]
        self.assertEqual(cells[0]["status"], "REPORTED_DASH")
        self.assertIsNone(cells[0]["value_yuan"])
        self.assertEqual(cells[1]["value_yuan"], "0.00")
        self.assertEqual(fields["trading_financial_assets"]["status"], "ROW_ABSENT")

    def test_six_line_parent_profit_label_does_not_lose_amount(self):
        rows = [row(text, values, y=100 + n * 12) for n, (text, values) in enumerate([
            ("1.归属", ()), ("于母公司股", ()), ("东的净利润", ()), ("（净亏损以", ()),
            ("“－”号填", ("10.00", "20.00")), ("列）", ())])]
        result = read_fields(rows, PROFIT_FIELDS, {1: [320, 500]}, 1)
        self.assertEqual(result["net_profit_attributable_to_parent"]["cells"][0]["reported_value"], "10.00")

    def test_cross_page_field_amount_uses_its_own_page_columns(self):
        rows = [row("归属于母公司所有者的", y=775), row("净利润", ("10.00", "20.00"), y=80, page=2, edges=(180, 340))]
        result = read_fields(rows, PROFIT_FIELDS, {1: [320, 500], 2: [180, 340]}, 1)
        self.assertEqual([c["reported_value"] for c in result["net_profit_attributable_to_parent"]["cells"]], ["10.00", "20.00"])

    def test_discontinued_cross_page_suffix_is_not_total_net_profit(self):
        rows = [row("净利润", ("80", "70"), y=700), row("终止经营", y=775), row("净利润", (None, None), y=80, page=2)]
        result = read_fields(rows, PROFIT_FIELDS, {1: [320, 500], 2: [320, 500]}, 1)
        self.assertEqual(result["net_profit"]["status"], "EXACT_LABEL_ROW")

    def test_additional_assets_stop_before_later_parent_table(self):
        start = row("合并资产负债表", y=40); final = row("负债和所有者权益总计", ("100", "100"), y=200)
        candidate = {"heading_evidence": [{k: start[k] for k in ("pdf_page", "bbox", "text")}],
                     "fields": {"liabilities_and_equity": {"rows": [{"row_evidence": [{k: final[k] for k in ("pdf_page", "bbox", "text")}]}]}},
                     "reported_unit": "元", "multiplier_to_yuan": 1, "numeric_right_edges_by_pdf_page": {"1": [320, 500]},
                     "column_mapping": {}, "current_column_index": 0}
        rows = [start, row("交易性金融资产", ("10", "20"), y=100), final, row("交易性金融资产", ("9", "8"), y=300)]
        result = extract_additional_assets(rows, candidate)
        self.assertEqual(result["fields"]["trading_financial_assets"]["cells"][0]["reported_value"], "10")

    def test_wrapped_explicit_full_bank_period(self):
        self.assertEqual(period("自2019年1月1日至2019年9月30日止9个月期间"), ("2019-01-01", "2019-09-30"))

    def test_parentheses_preserve_negative_number(self):
        self.assertEqual(number("(1,234.50)"), "-1234.50")
