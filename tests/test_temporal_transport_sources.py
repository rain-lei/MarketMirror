"""Source failures must remain isolated and distinct from usable model inputs."""
import json
from pathlib import Path
import tempfile
import unittest

from research.data_pipeline.temporal_transport_sources import (
    ACQUIRED, FAILED, RetryQuota, SourceBatch, key_for, source_url,
)
from research.data_pipeline.audit_eastmoney_carrier import dense_source_rows, raw_series


DATES = ["2022-01-04", "2022-01-05"]
CFG = {"download_period": ["2021-09-01", "2022-03-31"], "expected_session_dates": DATES,
    "minimum_request_interval_seconds": 8.0, "max_workers": 2}
SPEC = {"secid": "0.000001", "fqt": 2, "kind": "stock"}


def response(spec, dates=DATES, zero_volume=False):
    return json.dumps({"rc": 0, "data": {"code": spec["secid"][2:], "market": int(spec["secid"][0]),
        "name": "fixture", "klines": [day + ",10,10,11,9," + ("0" if zero_volume else "10")
            + ",100,2,0,0,1" for day in dates]}}).encode("utf-8")


class NoWait:
    def wait(self):
        pass


class FakeBatch(SourceBatch):
    def __init__(self, *args, behavior=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate = NoWait()
        self.behavior = behavior or (lambda spec, entry: response(spec))
        self.calls = []

    def perform_attempt(self, spec, url, part, entry):
        self.calls.append((key_for(spec), entry["attempt"]))
        body = self.behavior(spec, entry)
        (part / "source_response.json").write_bytes(body)
        return body


class TestTemporalTransportSources(unittest.TestCase):
    def test_failure_isolated_full_grid_retained_no_success_or_zero_fill(self):
        bad = {"secid": "0.000062", "fqt": 0, "kind": "stock"}
        def behavior(spec, entry):
            if spec == bad:
                raise TimeoutError("explicit source failure")
            return response(spec)
        with tempfile.TemporaryDirectory() as name:
            batch = FakeBatch(name, CFG, "frozen", name, "direct_http", behavior=behavior)
            results = batch.acquire_grid([bad, SPEC], 1)
            self.assertEqual(set(results), {key_for(bad), key_for(SPEC)})
            self.assertEqual(results[key_for(bad)]["status"], FAILED)
            self.assertNotIn("inspection", results[key_for(bad)])
            self.assertEqual(results[key_for(SPEC)]["status"], ACQUIRED)
            self.assertFalse(results[key_for(SPEC)]["model_eligible"])
            failed_rows = list(dense_source_rows(None, bad, DATES))
            self.assertTrue(all(all(v is None for v in r["provider_fields"].values()) for r in failed_rows))
            self.assertTrue(all(r["certified_trade_status"] is None for r in failed_rows))

    def test_partial_attempt_preserved_and_not_inferred_success(self):
        with tempfile.TemporaryDirectory() as name:
            part = Path(name) / key_for(SPEC) / "attempt_1"
            part.mkdir(parents=True)
            raw = response(SPEC)
            (part / "source_response.json").write_bytes(raw)
            quota = RetryQuota(Path(name) / "retries", 1)
            batch = FakeBatch(name, CFG, "frozen", name, "direct_http", quota)
            _, record = batch.acquire(SPEC, 2)
            self.assertEqual((part / "source_response.json").read_bytes(), raw)
            self.assertEqual(record["attempts"][0]["status"], "PRESERVED_INTERRUPTED_ATTEMPT_EXIT_UNOBSERVED")
            self.assertIsNone(record["attempts"][0]["observed_exit_code"])
            self.assertEqual(record["successful_attempt"], "attempt_2")
            self.assertEqual(quota.spent, 1)

    def test_missing_row_and_zero_volume_not_certified_suspension(self):
        with tempfile.TemporaryDirectory() as name:
            batch = FakeBatch(name, CFG, "frozen", name, "direct_http",
                behavior=lambda spec, entry: response(spec, DATES[:1], zero_volume=True))
            _, record = batch.acquire(SPEC, 1)
            self.assertEqual(record["status"], ACQUIRED)
            self.assertEqual(record["inspection"]["missing_expected_sessions"], DATES[1:])
            self.assertFalse(record["inspection"]["calendar_coverage_complete"])
            self.assertFalse(record["explicit_trade_status_supplied"])
            rows = list(dense_source_rows(raw_series(response(SPEC, DATES[:1], True),
                SPEC["secid"], *CFG["download_period"], DATES), SPEC, DATES))
            self.assertIsNone(rows[1]["raw_line"])
            self.assertTrue(all(v is None for v in rows[1]["provider_fields"].values()))
            self.assertTrue(all(r["certified_trade_status"] is None for r in rows))

    def test_completed_receipt_reused_and_hash_change_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            batch = FakeBatch(name, CFG, "frozen", name, "direct_http")
            first = batch.acquire(SPEC, 1)
            self.assertEqual(batch.acquire(SPEC, 1), first)
            self.assertEqual(len(batch.calls), 1)
            raw = Path(name) / key_for(SPEC) / "attempt_1/source_response.json"
            raw.write_bytes(b"{}")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                batch.acquire(SPEC, 1)

    def test_wrong_series_and_boolean_market_fail_independent_check(self):
        for alteration in (lambda d: d["data"].update(code="000062"),
                lambda d: d["data"].update(market=False)):
            def behavior(spec, entry):
                data = json.loads(response(spec))
                alteration(data)
                return json.dumps(data).encode("utf-8")
            with tempfile.TemporaryDirectory() as name:
                batch = FakeBatch(name, CFG, "frozen", name, "direct_http", behavior=behavior)
                _, record = batch.acquire(SPEC, 1)
                self.assertEqual(record["status"], FAILED)

    def test_hfq_parameter_identity_and_boolean_fqt_rejected(self):
        url = source_url(SPEC, *CFG["download_period"])
        self.assertIn("fqt=2", url)
        self.assertIn("beg=20210901", url)
        for value in (True, -1, 3):
            with self.assertRaises(ValueError):
                source_url({**SPEC, "fqt": value}, *CFG["download_period"])

    def test_global_retry_reservations_survive_restart_and_ceiling(self):
        with tempfile.TemporaryDirectory() as name:
            quota = RetryQuota(name, 1)
            self.assertTrue(quota.take("first", 2))
            resumed = RetryQuota(name, 1)
            self.assertEqual(resumed.spent, 1)
            self.assertFalse(resumed.take("second", 2))
            with self.assertRaises(ValueError):
                RetryQuota(name, 0)

    def test_quota_exhaustion_is_failure_without_unobserved_request(self):
        with tempfile.TemporaryDirectory() as name:
            def fail(spec, entry):
                raise OSError("source failed")
            quota = RetryQuota(Path(name) / "retries", 0)
            batch = FakeBatch(name, CFG, "frozen", name, "direct_http", quota, behavior=fail)
            _, record = batch.acquire(SPEC, 2)
            self.assertEqual(record["status"], FAILED)
            self.assertEqual(batch.calls, [(key_for(SPEC), 1)])
            self.assertEqual(record["attempts"][1]["status"], "NOT_REQUESTED_FROZEN_RETRY_QUOTA_EXHAUSTED")


if __name__ == "__main__":
    unittest.main()
