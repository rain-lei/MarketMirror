"""Half-year report context cannot replace actual column dates or money cells."""

import unittest

from research.data_pipeline.half_year_balance_context import extract_company, running_title
from research.data_pipeline.extract_issuer_half_balance_sheets import run_reports
from tests.test_financial_balance_sheet import row, sheet


def half_sheet(**kwargs):
    return sheet(current="2019年6月30日", **kwargs)


class HalfYearContextTests(unittest.TestCase):
    def test_repeated_top_report_title_does_not_declare_column_dates(self):
        first = half_sheet()[:6]
        second = [row("某公司2019年半年度报告全文", y=35, page=2)]
        second += [row(label, values, y=y, page=2) for label, values, y in
                   (("负债合计", ("60.00", "50.00"), 100),
                    ("所有者权益合计", ("40.00", "40.00"), 120),
                    ("负债和所有者权益总计", ("100.00", "90.00"), 140))]
        result = extract_company(first + second, "2019-06-30")
        self.assertEqual(result["status"], "UNIQUE_RECONCILED_CANDIDATE")
        self.assertEqual(len(result["ignored_running_report_titles"]), 1)

    def test_real_conflicting_continuation_header_remains_blocking(self):
        first = half_sheet()[:6]
        second = [row("项目", ("2019年9月30日", "2018年12月31日"), y=50, page=2)]
        second += [row(label, values, y=y, page=2) for label, values, y in
                   (("负债合计", ("60.00", "50.00"), 100),
                    ("所有者权益合计", ("40.00", "40.00"), 120),
                    ("负债和所有者权益总计", ("100.00", "90.00"), 140))]
        self.assertEqual(extract_company(first + second, "2019-06-30")["status"], "NO_RECONCILED_CANDIDATE")

    def test_body_report_title_and_year_only_data_are_not_removed(self):
        self.assertFalse(running_title(row("2019年半年度报告", y=350)))
        self.assertFalse(running_title(row("2019年6月30日", y=35)))

    def test_january_transition_statement_cannot_be_selected(self):
        rows = half_sheet() + sheet(current="2018年12月31日", previous="2019年1月1日")
        result = extract_company(rows, "2019-06-30")
        self.assertEqual(result["selected_candidate_index"], 0)
        self.assertEqual(result["candidates"][1]["status"], "NO_UNIQUE_CURRENT_NONPARENT_COLUMN")

    def test_missing_currency_is_not_assumed_rmb(self):
        candidate = extract_company(half_sheet(), "2019-06-30")["candidates"][0]
        self.assertEqual(candidate["currency_declaration"]["status"], "CURRENCY_UNSPECIFIED")

    def test_local_printed_rmb_is_accepted(self):
        rows = half_sheet()
        rows[2] = row("单位：元币种：人民币", y=80)
        self.assertEqual(extract_company(rows, "2019-06-30")["candidates"][0]
                         ["currency_declaration"]["status"], "CNY_EXPLICIT")

    def test_prior_universal_presentation_requires_same_scale(self):
        rows = [row("财务附注中报表的单位为：人民币元", y=20)] + half_sheet()
        declaration = extract_company(rows, "2019-06-30")["candidates"][0]["currency_declaration"]
        self.assertEqual(declaration["status"], "CNY_EXPLICIT")
        rows[0] = row("财务附注中报表的单位为：人民币万元", y=20)
        self.assertEqual(extract_company(rows, "2019-06-30")["candidates"][0]
                         ["currency_declaration"]["status"], "CURRENCY_UNIT_CONFLICT_REVIEW_REQUIRED")

    def test_later_presentation_is_not_backfilled(self):
        rows = half_sheet() + [row("财务附注中报表的单位为：人民币元", y=250)]
        self.assertEqual(extract_company(rows, "2019-06-30")["candidates"][0]
                         ["currency_declaration"]["status"], "CURRENCY_UNSPECIFIED")

    def test_multiple_current_tables_are_kept_unselected(self):
        self.assertEqual(extract_company(half_sheet() + half_sheet(), "2019-06-30")["status"],
                         "MULTIPLE_RECONCILED_CANDIDATES")

    def test_failed_company_is_explicit_and_later_company_still_runs(self):
        reports = {c: {"historical_short_name": c, "industry_code": "C", "report_metadata": {},
                       "status": "FETCHED", "original_pdf": {"archive_path": c, "sha256": c}}
                   for c in ("a", "b")}
        literal = {c: {"status": "PASS_LITERAL_PAGE_MAPS"} for c in reports}
        calls = []
        def extract(code, report, checked, cfg):
            calls.append(code)
            if code == "a":
                raise ValueError("bad cell")
            return {"stock_code": code, "status": "NO_RECONCILED_CANDIDATE", "candidates": []}
        result = run_reports(reports, literal, {}, extract)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(result[0]["status"], "EXTRACTION_FAILED_REVIEW_REQUIRED")
        self.assertIsNone(result[0]["selected_candidate_index"])
        self.assertEqual(result[1]["stock_code"], "b")


if __name__ == "__main__":
    unittest.main()
