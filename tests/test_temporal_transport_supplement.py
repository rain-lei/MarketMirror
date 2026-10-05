"""Separate request-profile amendments cannot relax source identity/width."""
import json
from pathlib import Path
import tempfile
import unittest

from research.data_pipeline.temporal_transport_supplement import SupplementBatch, sdk_request_url
from research.data_pipeline.audit_temporal_transport_supplement import check_sdk_source_url
from research.data_pipeline.audit_temporal_transport_sources import audit_receipt
from research.data_pipeline.temporal_transport_sources import RetryQuota, ACQUIRED, FAILED
from tests.test_temporal_transport_sources import CFG, SPEC, NoWait, response

PUBLIC = "a" * 32


class FakeSupplement(SupplementBatch):
    def __init__(self, *args, fail=False, extra_column=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate = NoWait()
        self.calls = []
        self.fail, self.extra_column = fail, extra_column

    def perform_attempt(self, spec, url, part, entry):
        self.calls.append(entry["attempt"])
        entry.update(pid=123, command=[self.cfg["node_executable"], self.cfg["cli_entrypoint"],
            "scrape", url, "--format", "markdown", "-o", str(part / "carrier.md")], observed_exit_code=1 if self.fail else 0)
        (part / "cli.log").write_text("fixture failure" if self.fail else "Scrape ID: 12345678-abcd-1234-abcd-123456789abc", encoding="utf-8")
        if self.fail:
            raise OSError("observed fixture source failure")
        body = response(spec)
        if self.extra_column:
            value = json.loads(body)
            value["data"]["klines"] = [line + ",99" for line in value["data"]["klines"]]
            body = json.dumps(value).encode()
        entry["scrape_id"] = "12345678-abcd-1234-abcd-123456789abc"
        (part / "carrier.md").write_text("```json\n" + body.decode() + "\n```", encoding="utf-8")
        (part / "source_response.json").write_bytes(body)
        return body


class TestTransportSourceSupplement(unittest.TestCase):
    def cfg(self):
        return {**CFG, "documented_public_ut": PUBLIC, "max_attempts": 2,
            "maximum_response_bytes": 2_000_000, "node_executable": "fixture_node", "cli_entrypoint": "fixture_cli"}

    def checker(self, url, spec, start, end):
        return check_sdk_source_url(url, spec, start, end, PUBLIC)

    def test_all_registered_parameters_keep_identity_with_documented_sdk_query(self):
        for fqt in (0, 1, 2):
            spec = {**SPEC, "fqt": fqt}
            url = sdk_request_url(spec, *CFG["download_period"], PUBLIC)
            self.checker(url, spec, *CFG["download_period"])
            for changed in (url + "&fqt=0", url.replace("secid=0.000001", "secid=0.000062"), url + "&end=20990101"):
                with self.assertRaisesRegex(ValueError, "fixed request"):
                    self.checker(changed, spec, *CFG["download_period"])

    def test_success_keeps_public_sdk_url_and_independent_model_gate_closed(self):
        with tempfile.TemporaryDirectory() as name:
            batch = FakeSupplement(name, self.cfg(), "frozen", name, "firecrawl")
            key, record = batch.acquire(SPEC, 1)
            verified = audit_receipt(Path(name) / key, record, SPEC, self.cfg(), "frozen", "firecrawl", url_checker=self.checker)
            self.assertEqual(record["status"], ACQUIRED)
            self.assertIn("f116", record["url"])
            self.assertFalse(verified["record"]["model_eligible"])

    def test_two_failed_attempts_have_no_source_features_and_finite_retry(self):
        with tempfile.TemporaryDirectory() as name:
            quota = RetryQuota(Path(name) / "reservations", 1)
            batch = FakeSupplement(name, self.cfg(), "frozen", name, "firecrawl", quota, fail=True)
            key, record = batch.acquire(SPEC, 2)
            verified = audit_receipt(Path(name) / key, record, SPEC, self.cfg(), "frozen", "firecrawl", url_checker=self.checker)
            self.assertIsNone(verified["series"])
            self.assertEqual(record["status"], FAILED)
            self.assertNotIn("inspection", record)
            self.assertEqual(batch.calls, [1, 2])
            self.assertEqual(quota.spent, 1)

    def test_wider_response_is_failure_instead_of_silent_column_truncation(self):
        with tempfile.TemporaryDirectory() as name:
            batch = FakeSupplement(name, self.cfg(), "frozen", name, "firecrawl", extra_column=True)
            key, record = batch.acquire(SPEC, 1)
            self.assertEqual(record["status"], FAILED)
            self.assertIn("width differs", record["attempts"][0]["error"])
            self.assertNotIn("inspection", record)
            verified = audit_receipt(Path(name) / key, record, SPEC, self.cfg(), "frozen", "firecrawl", url_checker=self.checker)
            self.assertIsNone(verified["series"])

    def test_terminal_receipt_resume_makes_no_new_request(self):
        with tempfile.TemporaryDirectory() as name:
            batch = FakeSupplement(name, self.cfg(), "frozen", name, "firecrawl")
            key, first = batch.acquire(SPEC, 1)
            previous = (Path(name) / key / "receipt.json").read_bytes()
            key, second = batch.acquire(SPEC, 1)
            self.assertEqual(batch.calls, [1])
            self.assertEqual(first, second)
            self.assertEqual(previous, (Path(name) / key / "receipt.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
