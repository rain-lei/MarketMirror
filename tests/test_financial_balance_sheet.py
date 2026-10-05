"""Meaningful failure cases for time, scope, geometry and missing amounts."""

import unittest

from research.data_pipeline.financial_balance_sheet import (
    available_snapshot, extract_company, geometry_rows, parse_balance_sheet,
    statement_heading,
)


def row(label, values=(), y=100, page=1, edges=(320, 500)):
    words = []
    if label:
        words.append((50, y, 160, y + 10, label))
    for edge, value in zip(edges, values):
        if value is not None:
            words.append((edge - 55, y, edge, y + 10, value))
    return geometry_rows(words, page, 850)[0]


def sheet(cash=("10.00", "20.00"), current="2019年9月30日", previous="2018年12月31日", unit="元"):
    result = [row("合并资产负债表", y=40), row("编制单位：某公司2019年9月30日", y=60),
              row("单位：" + unit, y=80), row("项目", (current, previous), y=100), row("货币资金", cash, y=120)]
    for y, label, values in ((140, "资产总计", ("100.00", "90.00")),
                             (160, "负债合计", ("60.00", "50.00")),
                             (180, "所有者权益合计", ("40.00", "40.00")),
                             (200, "负债和所有者权益总计", ("100.00", "90.00"))):
        result.append(row(label, values, y=y))
    return result


class FinancialBalanceSheetTests(unittest.TestCase):
    def test_current_column_is_dated_not_always_first(self):
        result = parse_balance_sheet(sheet(current="2018年12月31日", previous="2019年9月30日"), "consolidated", "2019-09-30")
        self.assertEqual(result["status"], "RECONCILED_CURRENT_BALANCE_SHEET")
        self.assertEqual(result["current_column_index"], 1)
        self.assertEqual(result["fields"]["money_funds"]["cells"][1]["value_yuan"], "20.00")

    def test_preparation_date_does_not_promote_january_adjustment_columns(self):
        result = parse_balance_sheet(sheet(current="2018年12月31日", previous="2019年1月1日"), "consolidated", "2019-09-30")
        self.assertEqual(result["status"], "NO_UNIQUE_CURRENT_NONPARENT_COLUMN")

    def test_previous_only_cell_does_not_shift_into_current(self):
        result = parse_balance_sheet(sheet(cash=(None, "20.00")), "consolidated", "2019-09-30")
        cells = result["fields"]["money_funds"]["cells"]
        self.assertEqual(cells[0]["status"], "REPORTED_BLANK")
        self.assertIsNone(cells[0]["value_yuan"])
        self.assertEqual(cells[1]["value_yuan"], "20.00")

    def test_dash_is_missing_but_explicit_zero_is_numeric(self):
        result = parse_balance_sheet(sheet(cash=("--", "0.00")), "consolidated", "2019-09-30")
        cells = result["fields"]["money_funds"]["cells"]
        self.assertEqual(cells[0]["status"], "REPORTED_DASH")
        self.assertIsNone(cells[0]["reported_value"])
        self.assertEqual(cells[1]["value_yuan"], "0.00")

    def test_money_units_change_scale_not_column_values(self):
        for unit, expected in (("千元", "100000.00"), ("百万元", "100000000.00")):
            with self.subTest(unit=unit):
                result = parse_balance_sheet(sheet(unit=unit), "consolidated", "2019-09-30")
                self.assertEqual(result["fields"]["assets"]["cells"][0]["value_yuan"], expected)

    def test_missing_or_conflicting_units_are_not_assumed_yuan(self):
        for rows in ([r for i, r in enumerate(sheet()) if i != 2], sheet()[:3] + [row("单位：万元", y=85)] + sheet()[3:]):
            result = parse_balance_sheet(rows, "consolidated", "2019-09-30")
            self.assertEqual(result["status"], "MISSING_OR_CONFLICTING_TABLE_UNIT")

    def test_non_reconciled_total_blocks_snapshot(self):
        rows = sheet()
        rows[7] = row("所有者权益合计", ("39.00", "40.00"), y=180)
        result = extract_company(rows)
        self.assertEqual(result["status"], "NO_RECONCILED_CANDIDATE")
        self.assertEqual(result["candidates"][0]["balance_checks"][0]["status"], "BALANCE_MISMATCH")

    def test_minority_inclusive_total_not_attributable_equity(self):
        rows = sheet()
        rows[7] = row("归属于母公司股东权益合计", ("40.00", "40.00"), y=180)
        self.assertEqual(extract_company(rows)["status"], "NO_RECONCILED_CANDIDATE")

    def test_change_explanation_table_without_totals_is_rejected(self):
        self.assertEqual(extract_company(sheet()[:5])["status"], "NO_RECONCILED_CANDIDATE")

    def test_mixed_profit_title_stops_balance_sheet_scope(self):
        self.assertEqual(statement_heading("合并利润表和利润表")["kind"], "利润表")
        rows = sheet()[:6] + [row("合并利润表和利润表", y=150)] + sheet()[6:]
        self.assertEqual(extract_company(rows)["status"], "NO_RECONCILED_CANDIDATE")

    def test_parent_only_statement_is_not_promoted(self):
        rows = sheet(); rows[0] = row("母公司资产负债表", y=40)
        self.assertEqual(extract_company(rows)["candidates"], [])

    def test_wrapped_total_with_cells_on_intermediate_row(self):
        rows = sheet()[:-1] + [row("负债和所有者权益（或股东权益）", y=200),
                              row("", ("100.00", "90.00"), y=212), row("总计", y=224)]
        result = extract_company(rows)
        self.assertEqual(result["status"], "UNIQUE_RECONCILED_CANDIDATE")
        self.assertEqual(len(result["candidates"][0]["fields"]["liabilities_and_equity"]["rows"][0]["row_evidence"]), 3)

    def test_footer_does_not_become_blank_loan_value(self):
        words = [(50, 760, 160, 770, "租赁负债"), (280, 780, 290, 790, "1"),
                 (300, 780, 305, 790, "/"), (320, 780, 330, 790, "31")]
        rows = geometry_rows(words, 1, 850)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "租赁负债")

    def test_duplicate_current_tables_are_not_silently_selected(self):
        self.assertEqual(extract_company(sheet() + sheet())["status"], "MULTIPLE_RECONCILED_CANDIDATES")

    def test_duplicate_optional_label_is_not_selected_or_zero_filled(self):
        rows = sheet() + [row("货币资金", ("11.00", "20.00"), y=220)]
        result = extract_company(rows)["candidates"][0]
        self.assertEqual(result["fields"]["money_funds"]["status"], "DUPLICATE_LABEL_REVIEW_REQUIRED")
        self.assertIsNone(result["fields"]["money_funds"]["cells"][0]["reported_value"])

    def test_continuation_uses_its_own_physical_columns(self):
        rows = sheet()[:6]
        rows += [row("合并资产负债表（续）", y=40, page=2), row("短期借款", ("5.00", "6.00"), y=80, page=2, edges=(280, 480))]
        rows += [row(label, values, y=y, page=2, edges=(280, 480)) for label, values, y in
                 (("负债合计", ("60.00", "50.00"), 100), ("所有者权益合计", ("40.00", "40.00"), 120),
                  ("负债和所有者权益总计", ("100.00", "90.00"), 140))]
        result = extract_company(rows)
        self.assertEqual(result["status"], "UNIQUE_RECONCILED_CANDIDATE")
        field = result["candidates"][0]["fields"]["short_term_borrowings"]
        self.assertEqual(field["cells"][0]["reported_value"], "5.00")

    def test_combined_broker_columns_are_date_then_scope(self):
        edges = (260, 340, 420, 500)
        rows = [row("合并及母公司资产负债表", y=40), row("单位：元", y=60),
                row("", ("2019年9月30日", "2018年12月31日"), y=80, edges=(315, 475)),
                row("", ("合并数", "公司数", "合并数", "公司数"), y=100, edges=edges)]
        rows += [row(label, values, y=y, edges=edges) for label, values, y in
                 (("货币资金", ("10.00", "9.00", "8.00", "7.00"), 120),
                  ("资产总计", ("100.00", "90.00", "80.00", "70.00"), 140),
                  ("负债合计", ("60.00", "50.00", "40.00", "30.00"), 160),
                  ("股东权益合计", ("40.00", "40.00", "40.00", "40.00"), 180),
                  ("负债和股东权益总计", ("100.00", "90.00", "80.00", "70.00"), 200))]
        result = extract_company(rows)["candidates"][0]
        self.assertEqual(result["status"], "RECONCILED_CURRENT_BALANCE_SHEET")
        self.assertEqual([(c["scope"], c["period_end"]) for c in result["column_mapping"]["columns"]],
                         [("consolidated", "2019-09-30"), ("parent", "2019-09-30"),
                          ("consolidated", "2018-12-31"), ("parent", "2018-12-31")])

    def test_separate_header_rows_still_map_by_geometry(self):
        rows = sheet()
        rows[3:4] = [row("", (None, "2018年12月31日"), y=94), row("项目", ("2019年9月30日", None), y=107)]
        result = extract_company(rows)
        self.assertEqual(result["status"], "UNIQUE_RECONCILED_CANDIDATE")

    def test_revised_source_stays_unavailable_before_own_publication(self):
        company = {**extract_company(sheet()), "report_metadata": {"available_at_proxy": "2019-12-08T00:00:00+08:00"}}
        self.assertIsNone(available_snapshot(company, "2019-11-01T09:15:00+08:00"))
        self.assertIsNotNone(available_snapshot(company, "2019-12-08T00:00:00+08:00"))
        with self.assertRaises(ValueError): available_snapshot(company, "2019-12-08T00:00:00")

    def test_agent_signal_remains_disabled_in_candidate_output(self):
        self.assertIs(extract_company(sheet())["agent_signal_enabled"], False)


if __name__ == "__main__":
    unittest.main()
