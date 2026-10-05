import unittest
import subprocess
from unittest.mock import patch

from research.data_pipeline.audit_issuer_business_evidence import check_literals, text_pages


class BusinessEvidenceIndependentAuditTest(unittest.TestCase):
    def test_reader_diagnostic_is_preserved_instead_of_silently_passed(self):
        result = subprocess.CompletedProcess([], 0, b"page one\f", b"Syntax Error: bad stream\n")
        with patch("research.data_pipeline.audit_issuer_business_evidence.subprocess.run", return_value=result):
            pages, diagnostics = text_pages("unused", "unused", "raw")
        self.assertEqual(pages, ["page one"])
        self.assertIn("bad stream", diagnostics)

    def test_reader_nonzero_exit_never_becomes_empty_success(self):
        result = subprocess.CompletedProcess([], 1, b"", b"bad PDF")
        with patch("research.data_pipeline.audit_issuer_business_evidence.subprocess.run", return_value=result):
            with self.assertRaises(subprocess.CalledProcessError):
                text_pages("unused", "unused", "raw")

    def test_unregistered_reading_option_is_rejected_before_subprocess(self):
        with self.assertRaisesRegex(ValueError, "mode is not fixed"):
            text_pages("unused", "unused", "choose_after_result")

    def fixture(self):
        return ["分 地 区\n湖 北 武 汉", "其他"], {
            "pdf_pages": 2, "literal_term_pages": {"分地区": [1], "湖北": [1]},
            "heading_candidates": [{"pdf_page": 1, "line_number": 1, "kind": "heading", "text": "分地区", "quantified_exposure": None}],
            "region_literal_contexts": []}, ["分地区", "湖北"]

    def test_independent_spacing_normalization_preserves_page_identity(self):
        pages, record, terms = self.fixture()
        rebuilt, differences = check_literals(pages, record, terms)
        self.assertEqual(rebuilt, record["literal_term_pages"])
        self.assertEqual(differences, [])

    def test_wrong_page_number_is_not_accepted_as_same_literal_match(self):
        pages, record, terms = self.fixture()
        record["literal_term_pages"]["湖北"] = [2]
        with self.assertRaisesRegex(ValueError, "page coverage"):
            check_literals(pages, record, terms)

    def test_page_count_and_filled_quantity_are_rejected(self):
        pages, record, terms = self.fixture()
        with self.assertRaisesRegex(ValueError, "page count"):
            check_literals(pages[:1], record, terms)
        record["heading_candidates"][0]["quantified_exposure"] = 0
        with self.assertRaisesRegex(ValueError, "unknown quantity"):
            check_literals(pages, record, terms)

    def test_different_anchor_is_explicit_context_qc_not_verified(self):
        pages, record, terms = self.fixture()
        record["heading_candidates"][0]["text"] = "原页没有这个标题"
        _, differences = check_literals(pages, record, terms)
        self.assertEqual(len(differences), 1)
        self.assertIn("do not adopt", differences[0]["reason"])


if __name__ == "__main__":
    unittest.main()
