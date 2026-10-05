import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy

from research.data_pipeline.documented_trade_status import (
    document_day, read_evidence, reconcile_positions,
)


class DocumentedTradeStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.days = ["2021-01-04", "2021-01-05", "2021-01-06"]
        self.code = "000001"
        self.start = "公司股票自2021年1月4日开市起停牌。"
        self.resume = "公司股票将于2021年1月6日开市起复牌。"
        self.documents = [self.document(self.start, "SUSPEND", self.days[0], "start"),
                          self.document(self.resume, "RESUME", self.days[2], "resume")]
        self.interval = {"interval_id": "fixture", "stock_code": self.code,
                         "start_date": self.days[0], "resume_date": self.days[2],
                         "document_ids": ["start", "resume"]}
        self.rows = [self.position(p, day, i < 2) for p in (0, 1) for i, day in enumerate(self.days)]

    def document(self, excerpt, event, day, name):
        path = self.root / (name + ".json")
        url = "https://issuer.example/" + name + ".pdf"
        year, month, day_number = map(int, day.split("-"))
        signature = f"{year}年{month}月{day_number}日"
        path.write_text(json.dumps({"markdown": "证券代码：000001\n" + excerpt,
                                   "metadata": {"sourceURL": url}}, ensure_ascii=False), encoding="utf-8")
        return {"document_id": name, "stock_code": self.code, "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "url": url,
                "document_date": day, "document_date_excerpt": signature,
                "assertions": [{"event": event, "date": day, "excerpt": excerpt}]}

    def position(self, parameter, day, missing):
        line = None if missing else day + ",10.00,10.10,10.20,9.90,1,1010,3,1,0.10,0.01"
        return {"kind": "stock", "secid": "0." + self.code, "fqt": parameter, "trade_date": day,
                "raw_line": line, "provider_fields": {f"f{n}": None for n in range(51, 62)} if missing else
                dict(zip([f"f{n}" for n in range(51, 62)], line.split(","))),
                "source_observation_status": "SOURCE_ROW_ABSENT_NOT_CERTIFIED_SUSPENSION" if missing else "SOURCE_ROW_PRESENT_QC_PENDING",
                "certified_trade_status": None, "model_eligible": False}

    def run_reconcile(self, rows=None, intervals=None):
        docs = read_evidence(self.root, self.documents)
        return reconcile_positions(self.rows if rows is None else rows, self.days, [self.code],
                                   [self.interval] if intervals is None else intervals, docs)

    def test_documented_interval_keeps_nulls_and_excludes_resumption_day(self):
        old = deepcopy(self.rows)
        rows, summary = self.run_reconcile()
        self.assertEqual(self.rows, old)
        self.assertEqual(summary["documented_suspension_company_days"], 2)
        self.assertEqual(summary["reconciliation_counts"]["ABSENT_SOURCE_WITH_DOCUMENTED_SUSPENSION"], 4)
        for original, row in zip(old, rows):
            self.assertEqual(row["raw_line"], original["raw_line"])
            self.assertEqual(row["documented_trade_status"], "SUSPENDED_FULL_SESSION" if row["trade_date"] < self.days[2] else None)
            self.assertFalse(row["model_eligible"])
            self.assertFalse(row["historical_as_of_availability_certified"])

    def test_absence_and_zero_volume_cannot_supply_status(self):
        rows = deepcopy(self.rows)
        rows[-1]["raw_line"] = rows[-1]["raw_line"].replace(",1,1010,", ",0,0,")
        rows[-1]["provider_fields"]["f56"] = "0"
        rows[-1]["provider_fields"]["f57"] = "0"
        output, summary = self.run_reconcile(rows, [])
        self.assertEqual(summary["documented_suspension_company_days"], 0)
        self.assertTrue(all(row["documented_trade_status"] is None for row in output))

    def test_request_failure_stays_failure_when_status_is_documented(self):
        rows = deepcopy(self.rows)
        rows[0]["source_observation_status"] = "SOURCE_REQUEST_FAILED"
        output, _ = self.run_reconcile(rows)
        self.assertEqual(output[0]["reconciliation_status"], "FAILED_REQUEST_WITH_DOCUMENTED_SUSPENSION")
        self.assertEqual(output[0]["source_observation_status"], "SOURCE_REQUEST_FAILED")
        self.assertTrue(all(v is None for v in output[0]["provider_fields"].values()))

    def test_quoted_source_inside_documented_interval_is_rejected(self):
        rows = deepcopy(self.rows)
        rows[0] = self.position(0, self.days[0], False)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.run_reconcile(rows)

    def test_expected_resumption_is_rejected(self):
        self.documents[1] = self.document("公司股票预计于2021年1月6日开市起复牌。", "RESUME", self.days[2], "resume")
        with self.assertRaisesRegex(ValueError, "expected"):
            self.run_reconcile()

    def test_changed_document_and_wrong_issuer_are_rejected(self):
        (self.root / "start.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_reconcile()
        self.documents[0] = self.document(self.start, "SUSPEND", self.days[0], "start")
        self.documents[0]["stock_code"] = "000002"
        with self.assertRaisesRegex(ValueError, "issuer stock code"):
            self.run_reconcile()

    def test_chinese_signature_date_and_invented_signature_are_checked(self):
        self.assertEqual(document_day("二〇二〇年九月十九日"), "2020-09-19")
        self.assertEqual(document_day("2021 年 2 月 23 日"), "2021-02-23")
        self.documents[0]["document_date"] = "2021-01-07"
        with self.assertRaisesRegex(ValueError, "signature date differs"):
            self.run_reconcile()

    def test_incomplete_duplicate_and_boolean_grid_are_rejected(self):
        variants = [self.rows[:-1], self.rows + [deepcopy(self.rows[0])], deepcopy(self.rows)]
        variants[2][0]["fqt"] = False
        for rows in variants:
            with self.subTest(length=len(rows)), self.assertRaises(ValueError):
                self.run_reconcile(rows)

    def test_filled_missing_value_and_unquoted_resumption_are_rejected(self):
        rows = deepcopy(self.rows)
        rows[0]["provider_fields"]["f56"] = "0"
        with self.assertRaisesRegex(ValueError, "filled"):
            self.run_reconcile(rows)
        rows = deepcopy(self.rows)
        rows[-1] = self.position(1, self.days[2], True)
        with self.assertRaisesRegex(ValueError, "resumption lacks"):
            self.run_reconcile(rows)

    def test_path_escape_and_wrong_url_are_rejected(self):
        self.documents[0]["path"] = "../start.json"
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.run_reconcile()
        self.documents[0]["path"] = "start.json"
        self.documents[0]["url"] += "?different=true"
        with self.assertRaisesRegex(ValueError, "URL differs"):
            self.run_reconcile()

    def test_overlapping_intervals_and_missing_start_assertion_are_rejected(self):
        second = deepcopy(self.interval)
        second["interval_id"] = "overlap"
        with self.assertRaisesRegex(ValueError, "overlapping"):
            self.run_reconcile(intervals=[self.interval, second])
        self.interval["document_ids"] = ["resume"]
        with self.assertRaisesRegex(ValueError, "lacks confirmed start"):
            self.run_reconcile()


if __name__ == "__main__":
    unittest.main()
