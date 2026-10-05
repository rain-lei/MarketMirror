"""An independently checked raw source never becomes model success by relabeling."""
import json
from pathlib import Path
import tempfile
import unittest

from research.data_pipeline.audit_temporal_transport_sources import audit_receipt
from research.data_pipeline.provenance import file_sha256
from tests.test_temporal_transport_sources import FakeBatch, CFG, SPEC, response


class TestTemporalTransportSourceAudit(unittest.TestCase):
    def cfg(self):
        return {**CFG, "max_attempts": 2, "maximum_response_bytes": 2_000_000}

    def fixture(self, name, failed=False):
        def behavior(spec, entry):
            if failed:
                raise OSError("observed fixture failure")
            entry["http_status"] = 200
            return response(spec)
        batch = FakeBatch(name, self.cfg(), "frozen", name, "direct_http", behavior=behavior)
        key, record = batch.acquire(SPEC, 1)
        return Path(name) / key, record

    def resign(self, folder, record):
        record["artifacts"] = {p.relative_to(folder).as_posix(): file_sha256(p)
            for p in folder.rglob("*") if p.is_file() and p.name != "receipt.json"}
        (folder / "receipt.json").write_text(json.dumps(record), encoding="utf-8")

    def test_success_rebuilt_but_model_and_trade_status_remain_unknown(self):
        with tempfile.TemporaryDirectory() as name:
            folder, record = self.fixture(name)
            result = audit_receipt(folder, record, SPEC, self.cfg(), "frozen", "direct_http")
            self.assertEqual(set(result["series"]["rows"]), set(CFG["expected_session_dates"]))
            self.assertFalse(result["record"]["model_eligible"])
            self.assertFalse(result["record"]["explicit_trade_status_supplied"])

    def test_failed_request_has_null_series(self):
        with tempfile.TemporaryDirectory() as name:
            folder, record = self.fixture(name, True)
            result = audit_receipt(folder, record, SPEC, self.cfg(), "frozen", "direct_http")
            self.assertIsNone(result["series"])
            self.assertEqual(result["attempt_classes"], ["OBSERVED_SOURCE_FAILURE"])

    def test_failed_request_cannot_relabel_success_without_source(self):
        with tempfile.TemporaryDirectory() as name:
            folder, record = self.fixture(name, True)
            record["status"] = "RAW_SOURCE_ACQUIRED_MODEL_QC_PENDING"
            record["successful_attempt"] = "attempt_1"
            record["inspection"] = {}
            self.resign(folder, record)
            with self.assertRaisesRegex(ValueError, "Promoted source"):
                audit_receipt(folder, record, SPEC, self.cfg(), "frozen", "direct_http")

    def test_recomputed_hash_cannot_certify_raw_source_model_use(self):
        with tempfile.TemporaryDirectory() as name:
            folder, record = self.fixture(name)
            record["model_eligible"] = True
            self.resign(folder, record)
            with self.assertRaisesRegex(ValueError, "certification"):
                audit_receipt(folder, record, SPEC, self.cfg(), "frozen", "direct_http")

    def test_forged_wrong_company_rejected_even_after_all_hashes_recomputed(self):
        with tempfile.TemporaryDirectory() as name:
            folder, record = self.fixture(name)
            path = folder / "attempt_1/source_response.json"
            payload = json.loads(path.read_bytes())
            payload["data"]["code"] = "000062"
            changed = json.dumps(payload).encode("utf-8")
            path.write_bytes(changed)
            record["attempts"][0]["source_response_bytes"] = len(changed)
            (folder / "attempt_1/process.json").write_text(json.dumps(record["attempts"][0]), encoding="utf-8")
            self.resign(folder, record)
            with self.assertRaisesRegex(ValueError, "identity differs"):
                audit_receipt(folder, record, SPEC, self.cfg(), "frozen", "direct_http")

    def test_source_drop_or_extra_file_cannot_escape_manifest(self):
        with tempfile.TemporaryDirectory() as name:
            folder, record = self.fixture(name)
            (folder / "unregistered.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unregistered/missing"):
                audit_receipt(folder, record, SPEC, self.cfg(), "frozen", "direct_http")

    def test_direct_http_cannot_invent_observed_process_exit(self):
        with tempfile.TemporaryDirectory() as name:
            folder, record = self.fixture(name)
            record["attempts"][0]["observed_exit_code"] = 0
            (folder / "attempt_1/process.json").write_text(json.dumps(record["attempts"][0]), encoding="utf-8")
            self.resign(folder, record)
            with self.assertRaisesRegex(ValueError, "fabricated"):
                audit_receipt(folder, record, SPEC, self.cfg(), "frozen", "direct_http")


if __name__ == "__main__":
    unittest.main()
