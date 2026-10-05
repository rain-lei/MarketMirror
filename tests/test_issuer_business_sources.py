import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from research.data_pipeline import fetch_issuer_business_reports as sources


def fixture():
    report = {"stock_code": "000001", "eligible": True, "report_period_key": "2019_half",
              "report_end_date": "2019-06-30", "available_at_proxy": "2019-08-09T00:00:00+08:00",
              "attachment_url": "https://static.cninfo.com.cn/finalpage/2019-08-08/1206504127.PDF"}
    company = {"stock_code": "000001", "historical_short_name": "issuer", "industry_code": "J66",
               "reports": {"2019_half": {"status": "AVAILABLE_METADATA_ONLY", "selected": report}}}
    missing = {"stock_code": "688018", "historical_short_name": "missing", "industry_code": "C39",
               "reports": {"2019_half": {"status": "MISSING_IN_CURRENT_CATALOG", "selected": None}}}
    settings = {"expected_companies": 2, "expected_selected_reports": 1, "report_period_key": "2019_half",
                "report_end_date": "2019-06-30", "snapshot_at": "2020-01-22T23:59:59+08:00",
                "header_report_token": "2019年半年度报告", "maximum_attempts_per_report": 2, "maximum_pdf_bytes": 100}
    return {"companies": [company, missing]}, settings


def response(url, content=b"%PDF-stub", http_error=None):
    value = Mock()
    value.url = url
    value.status_code = 200 if http_error is None else 503
    value.headers = {"Content-Type": "application/pdf"}
    value.iter_content.return_value = [content]
    value.raise_for_status.side_effect = http_error
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    return value


class IssuerBusinessSourceTest(unittest.TestCase):
    def test_full_cohort_includes_source_missing_without_substitution(self):
        inventory, settings = fixture()
        selected, missing = sources.select_cohort(inventory, settings)
        self.assertEqual([c["stock_code"] for c, _ in selected], ["000001"])
        self.assertEqual([c["stock_code"] for c in missing], ["688018"])

    def test_duplicate_issuer_and_ambiguous_selection_are_rejected(self):
        inventory, settings = fixture()
        inventory["companies"][1]["stock_code"] = "000001"
        with self.assertRaisesRegex(ValueError, "identities"):
            sources.select_cohort(inventory, settings)
        inventory, settings = fixture()
        inventory["companies"][1]["reports"]["2019_half"]["status"] = "AMBIGUOUS"
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            sources.select_cohort(inventory, settings)

    def test_wrong_issuer_period_future_date_and_official_host_are_rejected(self):
        for key, value in (("stock_code", "000002"), ("report_end_date", "2019-09-30"),
                           ("available_at_proxy", "2020-02-01T00:00:00+08:00"),
                           ("attachment_url", "https://static.cninfo.com.cn.evil.example/finalpage/2019-08-08/1206504127.PDF")):
            with self.subTest(key=key):
                inventory, settings = fixture()
                inventory["companies"][0]["reports"]["2019_half"]["selected"][key] = value
                with self.assertRaisesRegex(ValueError, "identity, period"):
                    sources.select_cohort(inventory, settings)

    def test_non_pdf_payload_is_not_a_valid_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "response.pdf"
            path.write_bytes(b"<html>error</html>")
            with self.assertRaisesRegex(ValueError, "not a PDF"):
                sources.container_facts(path, "000001", "2019年半年度报告")

    def test_successful_download_does_not_certify_business_values(self):
        inventory, settings = fixture()
        company = inventory["companies"][0]
        report = company["reports"]["2019_half"]["selected"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(sources, "ROOT", root), patch.object(sources.requests, "get", return_value=response(report["attachment_url"])), \
                    patch.object(sources, "container_facts", return_value={"header_report_period_text_found": True, "pdf_pages": 20}):
                result = sources.fetch_report(company, report, settings, root)
            self.assertEqual(result["status"], "FETCHED")
            self.assertEqual(len(result["attempts"]), 1)
            self.assertEqual((root / result["original_pdf"]["archive_path"]).read_bytes(), b"%PDF-stub")
            self.assertFalse(result["business_exposure_values_verified"])
            self.assertFalse(result["agent_signal_enabled"])

    def test_header_mismatch_is_explicit_qc_not_an_automatic_success(self):
        inventory, settings = fixture()
        company = inventory["companies"][0]
        report = company["reports"]["2019_half"]["selected"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(sources, "ROOT", root), patch.object(sources.requests, "get", return_value=response(report["attachment_url"])), \
                    patch.object(sources, "container_facts", return_value={"header_report_period_text_found": False, "pdf_pages": 20}):
                result = sources.fetch_report(company, report, settings, root)
            self.assertEqual(result["status"], "FETCHED_HEADER_REVIEW_REQUIRED")
            self.assertIsNotNone(result["original_pdf"])

    def test_failed_response_bytes_and_retry_are_preserved(self):
        inventory, settings = fixture()
        company = inventory["companies"][0]
        report = company["reports"]["2019_half"]["selected"]
        attempts = [response(report["attachment_url"], b"service unavailable", RuntimeError("503")), response(report["attachment_url"])]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(sources, "ROOT", root), patch.object(sources.requests, "get", side_effect=attempts), \
                    patch.object(sources.time, "sleep"), patch.object(sources, "container_facts", return_value={"header_report_period_text_found": True}):
                result = sources.fetch_report(company, report, settings, root)
            self.assertEqual(result["status"], "FETCHED")
            self.assertEqual(len(result["attempts"]), 2)
            self.assertIn("503", result["attempts"][0]["error"])
            self.assertEqual((root / result["attempts"][0]["archive_path"]).read_bytes(), b"service unavailable")

    def test_redirect_and_size_limit_never_produce_original_pdf_success(self):
        inventory, settings = fixture()
        company = inventory["companies"][0]
        report = company["reports"]["2019_half"]["selected"]
        for resp in (response(report["attachment_url"].replace("1206504127", "1206504128")),
                     response(report["attachment_url"], b"x" * 101)):
            with self.subTest(url=resp.url), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with patch.object(sources, "ROOT", root), patch.object(sources.requests, "get", return_value=resp), patch.object(sources.time, "sleep"):
                    result = sources.fetch_report(company, report, settings, root)
                self.assertEqual(result["status"], "FAILED")
                self.assertIsNone(result["original_pdf"])
                self.assertEqual(len(result["attempts"]), 2)

    def test_missing_archive_cannot_be_filled_by_a_later_report(self):
        inventory, settings = fixture()
        selected, missing = sources.select_cohort(inventory, settings)
        manifest = {"reports": {"000001": {"stock_code": "000001", "status": "PENDING"},
                                "688018": {"stock_code": "688018", "status": "NO_ELIGIBLE_REPORT", "original_pdf": None, "report_metadata": None}}}
        sources.verify_reports(manifest, selected, missing, settings)
        changed = copy.deepcopy(manifest)
        changed["reports"]["688018"]["report_metadata"] = {"period": "2020"}
        with self.assertRaisesRegex(ValueError, "imputed"):
            sources.verify_reports(changed, selected, missing, settings)


if __name__ == "__main__":
    unittest.main()
