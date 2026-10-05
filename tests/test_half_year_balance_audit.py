"""Independent rectangle currency evidence and isolated failures are required."""

import unittest
from types import SimpleNamespace

from research.data_pipeline.audit_issuer_half_balance_sheets import (check_declaration, run_companies,
                                                                   validate_associations, WithoutNoteColumn)
from research.data_pipeline.extract_issuer_half_balance_sheets_v2 import source_qc
from tests.test_issuer_balance_sheet_audit import FakeTable


class PrintedPage:
    width, height = 600, 850

    def __init__(self, text):
        self.text = text

    def crop(self, box):
        self.cropped = box
        return self

    def extract_text(self, layout=False):
        return self.text


def declaration(text="财务附注中报表的单位为：人民币元", unit="元", kind="EXPLICIT_FINANCIAL_STATEMENT_AND_NOTE_PRESENTATION"):
    return {"pdf_page": 1, "bbox": [50, 80, 500, 95], "text": text, "reported_unit": unit,
            "declaration_kind": kind, "currency": "CNY_EXPLICIT"}


class HalfYearIndependentAuditTests(unittest.TestCase):
    def test_explicit_note_column_is_separate_from_currency_columns(self):
        table = FakeTable([["项目", "附注", "2019年6月30日", "2018年12月31日"],
                           ["货币资金", "七、1", "10.00", "20.00"]], [50, 180, 220, 365, 535])
        filtered = WithoutNoteColumn(table, [(180, 220)])
        self.assertEqual(filtered.extract()[1], ["货币资金", "10.00", "20.00"])
        self.assertEqual(len(filtered.note_column_cells), 2)

    def test_financial_column_outside_note_rectangle_cannot_be_dropped(self):
        table = FakeTable([["项目", "10.00", "20.00"]], [50, 180, 365, 535])
        self.assertEqual(WithoutNoteColumn(table, [(180, 220)]).extract(), table.extract())

    def test_matching_universal_declaration_is_checked_in_its_rectangle(self):
        page = PrintedPage("财务附注中报表的单位为：人民币元")
        result = check_declaration(SimpleNamespace(pages=[page]), declaration())
        self.assertEqual(result["status"], "PASS_EXPLICIT_CNY_DECLARATION")
        self.assertEqual(page.cropped, (48, 77, 502, 98))

    def test_matching_text_with_wrong_scale_fails(self):
        document = SimpleNamespace(pages=[PrintedPage("财务附注中报表的单位为：人民币元")])
        self.assertTrue(check_declaration(document, declaration(unit="万元"))["status"].startswith("FAIL"))

    def test_rmb_elsewhere_cannot_replace_missing_rectangle_currency(self):
        document = SimpleNamespace(pages=[PrintedPage("单位：元")])
        self.assertTrue(check_declaration(document, declaration())["status"].startswith("FAIL"))

    def test_local_currency_is_supported_without_universal_inference(self):
        text = "单位：元币种：人民币"
        result = check_declaration(SimpleNamespace(pages=[PrintedPage(text)]), declaration(text, kind="LOCAL_STATEMENT_CURRENCY"))
        self.assertEqual(result["status"], "PASS_EXPLICIT_CNY_DECLARATION")

    def test_unknown_declaration_kind_is_not_accepted(self):
        with self.assertRaises(ValueError):
            check_declaration(SimpleNamespace(pages=[PrintedPage("人民币")]), declaration(kind="GUESS"))

    def test_exception_keeps_failed_issuer_and_reads_next(self):
        companies = [{"stock_code": c, "source_pdf_sha256": c, "selected_candidate_index": 0,
                      "literal_reader_status": "PASS_LITERAL_PAGE_MAPS"} for c in ("a", "b")]
        calls = []
        def audit(company, cfg):
            calls.append(company["stock_code"])
            if company["stock_code"] == "a":
                raise RuntimeError("independent table missing")
            return {"stock_code": "b", "status": "PASS"}
        records = run_companies(companies, {}, audit)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(records[0]["status"], "INDEPENDENT_READ_FAILED_NOT_ADOPTED")
        self.assertEqual(records[1]["status"], "PASS")

    def test_duplicate_company_association_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_associations([{"stock_code": "a"}, {"stock_code": "a"}], {"reports": {"a": {}}}, {"a": {}}, {"expected_companies": 2})

    def test_real_upstream_status_is_counted_and_missing_source_is_separate(self):
        companies = [{"stock_code": "a", "source_pdf_path": "a.pdf", "literal_reader_status": "READER_DIAGNOSTIC_REVIEW_REQUIRED"},
                     {"stock_code": "b", "source_pdf_path": "b.pdf", "literal_reader_status": "PASS_LITERAL_PAGE_MAPS"},
                     {"stock_code": "c", "source_pdf_path": None, "literal_reader_status": "SOURCE_MISSING_AS_EXPECTED"}]
        self.assertEqual(source_qc(companies), [{"stock_code": "a", "literal_reader_status": "READER_DIAGNOSTIC_REVIEW_REQUIRED"}])


if __name__ == "__main__":
    unittest.main()
