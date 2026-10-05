"""Missing observations, suspension evidence and request failure stay distinct."""
from copy import deepcopy
import unittest

from research.data_pipeline.temporal_transport_trade_status import (
    reconcile_transport_positions, PRESENT, ABSENT, FAILED, PENDING, SUSPENDED,
)


class TestTransportTradeStatus(unittest.TestCase):
    calendar = ["2021-12-16", "2021-12-17"]
    stocks = ["600001", "000001"]

    def source(self, code, fqt, day, status):
        fields = dict(zip([f"f{i}" for i in range(51, 62)],
            [day, "10", "11", "11", "9", "100", "1050", "0", "0", "0", "0"]))
        return {"secid": ("1." if code.startswith("6") else "0.") + code,
            "kind": "stock", "fqt": fqt, "trade_date": day,
            "source_observation_status": status, "model_eligible": False, "certified_trade_status": None,
            "provider_fields": fields if status == PRESENT else {key: None for key in fields},
            "raw_line": ",".join(fields.values()) if status == PRESENT else None}

    def fixture(self):
        rows = [self.source(code, fqt, day, ABSENT if code == "600001" and day == self.calendar[0] else PRESENT)
            for code in self.stocks for fqt in (0, 1, 2) for day in self.calendar]
        evidence = {"intervals": [{"interval_id": "generic", "stock_code": "600001",
            "start_date": self.calendar[0], "resume_date": self.calendar[1], "document_ids": ["issuer"]}],
            "documents": {"issuer": {"stock_code": "600001", "assertions": [
                {"event": "SUSPEND", "date": self.calendar[0]}, {"event": "RESUME", "date": self.calendar[1]}]}}}
        return rows, evidence

    def test_all_parameters_reconcile_without_changing_raw_absences(self):
        rows, evidence = self.fixture()
        before = deepcopy(rows)
        result, summary = reconcile_transport_positions(rows, self.calendar, self.stocks, evidence)
        self.assertEqual(rows, before)
        absent = [r for r in result if r["source_observation_status"] == ABSENT]
        self.assertEqual({r["fqt"] for r in absent}, {0, 1, 2})
        self.assertTrue(all(r["documented_trade_status"] == SUSPENDED and r["raw_line"] is None for r in absent))
        self.assertTrue(summary["all_endpoint_quotes_corroborated"])
        self.assertFalse(summary["model_eligible"])

    def test_entire_request_failure_inside_documented_interval_stays_unknown(self):
        rows, evidence = self.fixture()
        rows[0] = self.source("600001", 0, self.calendar[0], FAILED)
        result, summary = reconcile_transport_positions(rows, self.calendar, self.stocks, evidence)
        failed = result[0]
        self.assertIsNone(failed["documented_trade_status"])
        self.assertEqual(failed["source_observation_status"], FAILED)
        self.assertIsNotNone(failed["separate_documented_interval_id"])
        self.assertTrue(all(v is None for v in failed["provider_fields"].values()))

    def test_pending_request_is_not_reclassified_as_absent_or_suspended(self):
        rows, evidence = self.fixture()
        rows[0] = self.source("600001", 0, self.calendar[0], PENDING)
        result, summary = reconcile_transport_positions(rows, self.calendar, self.stocks, evidence)
        self.assertIsNone(result[0]["documented_trade_status"])
        self.assertEqual(result[0]["source_observation_status"], PENDING)

    def test_quote_inside_documented_suspension_is_a_conflict(self):
        rows, evidence = self.fixture()
        rows[0] = self.source("600001", 0, self.calendar[0], PRESENT)
        with self.assertRaisesRegex(ValueError, "contradicts"):
            reconcile_transport_positions(rows, self.calendar, self.stocks, evidence)

    def test_source_absence_outside_documented_interval_stays_unknown(self):
        rows, evidence = self.fixture()
        rows[-1] = self.source("000001", 2, self.calendar[1], ABSENT)
        result, summary = reconcile_transport_positions(rows, self.calendar, self.stocks, evidence)
        self.assertIsNone(result[-1]["documented_trade_status"])
        self.assertEqual(result[-1]["reconciliation_status"], "ABSENT_SOURCE_UNKNOWN_TRADE_STATUS")

    def test_announced_resumption_requires_positive_volume_quote_for_all_parameters(self):
        rows, evidence = self.fixture()
        rows[1]["provider_fields"]["f56"] = "0"
        rows[1]["raw_line"] = ",".join(rows[1]["provider_fields"].values())
        with self.assertRaisesRegex(ValueError, "positive-volume"):
            reconcile_transport_positions(rows, self.calendar, self.stocks, evidence)
        result, summary = reconcile_transport_positions(rows, self.calendar, self.stocks, evidence, require_endpoints=False)
        self.assertFalse(summary["all_endpoint_quotes_corroborated"])

    def test_omitted_company_parameter_session_fails_full_grid(self):
        rows, evidence = self.fixture()
        with self.assertRaisesRegex(ValueError, "omitted"):
            reconcile_transport_positions(rows[:-1], self.calendar, self.stocks, evidence)

    def test_zero_volume_is_not_documentary_suspension_evidence(self):
        rows, evidence = self.fixture()
        rows[-1]["provider_fields"]["f56"] = "0"
        rows[-1]["raw_line"] = ",".join(rows[-1]["provider_fields"].values())
        result, summary = reconcile_transport_positions(rows, self.calendar, self.stocks, evidence)
        self.assertIsNone(result[-1]["documented_trade_status"])
        self.assertEqual(result[-1]["reconciliation_status"], "PRESENT_SOURCE_BASIS_QC_PENDING")


if __name__ == "__main__":
    unittest.main()
