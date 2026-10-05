import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from openpyxl import Workbook

from research.data_pipeline.extract_financial import extract_financial
from research.data_pipeline.provenance import file_sha256


class FinancialExtractionTest(unittest.TestCase):
    def make_source(self, path: Path, rows: list[list[object]]) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["股票代码", "公司简称", "季度", "提问时间", "回复时间", "Isviolated", "资产总计", "净利润", "p", "p/e"])
        for row in rows:
            sheet.append(row)
        workbook.save(path)
        workbook.close()

    def test_split_context_keep_conflicts_and_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "financial.xlsx"
            self.make_source(source, [
                [1, "平安银行", 4, "2020-01-06", "2021-03-02", 0, -5, 20, 10, 2],
                [1, "平安银行", 4, "2020-01-21", "2021-03-02", 0, -5, 20, 11, 3],
                [1, "平安银行", 4, "2020-02-05", "2020-04-29", 0, -4, 20, None, None],
            ])
            before = file_sha256(source)
            report = extract_financial(source, folder / "out")
            self.assertEqual(report["source_file_hash"], before)
            self.assertEqual(file_sha256(source), before)
            self.assertEqual(report["counts"]["source_rows"], 3)
            self.assertEqual(report["counts"]["snapshot_count"], 2)
            self.assertEqual(report["counts"]["conflicting_company_quarter_groups"], 1)
            self.assertEqual(report["counts"]["question_quarter_mismatches"], 3)
            with closing(sqlite3.connect(folder / "out" / "financial_unverified.sqlite")) as db:
                self.assertEqual(db.execute("SELECT SUM(reference_count) FROM financial_snapshot").fetchone()[0], 3)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM source_row_reference").fetchone()[0], 3)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM financial_snapshot WHERE stock_code = '000001'").fetchone()[0], 2)
                for raw, end, published, status in db.execute(
                    "SELECT metric_values_json, report_period_end, published_at, verification_status FROM financial_snapshot"
                ):
                    self.assertNotIn("p", json.loads(raw))
                    self.assertIsNone(end)
                    self.assertIsNone(published)
                    self.assertEqual(status, "unverified")
                contexts = [json.loads(row[0])["p"] for row in db.execute(
                    "SELECT context_values_json FROM source_row_reference ORDER BY source_row"
                )]
                self.assertEqual(contexts, [10, 11, None])
                ids = db.execute("SELECT snapshot_id FROM financial_snapshot ORDER BY snapshot_id").fetchall()
            rerun = extract_financial(source, folder / "out")
            self.assertEqual(report["counts"], rerun["counts"])
            with closing(sqlite3.connect(folder / "out" / "financial_unverified.sqlite")) as db:
                self.assertEqual(ids, db.execute("SELECT snapshot_id FROM financial_snapshot ORDER BY snapshot_id").fetchall())

    def test_formulas_missing_and_zero_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "financial.xlsx"
            self.make_source(source, [
                [1, "公司", 1, "2020-01-01", None, None, 0, None, 1, None],
                [1, "公司", 1, "2020-01-02", None, None, None, "=SUM(1,2)", 1, None],
            ])
            report = extract_financial(source, folder / "out")
            fields = {item["field_name"]: item for item in report["fields"]}
            self.assertEqual(fields["资产总计"]["missing_count"], 1)
            self.assertEqual(fields["资产总计"]["zero_count"], 1)
            self.assertEqual(fields["净利润"]["non_numeric_count"], 1)
            with closing(sqlite3.connect(folder / "out" / "financial_unverified.sqlite")) as db:
                values = [json.loads(row[0]) for row in db.execute("SELECT metric_values_json FROM financial_snapshot")]
                self.assertTrue(any(v["净利润"] == "=SUM(1,2)" for v in values))

    def test_missing_keys_do_not_merge_unrelated_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "financial.xlsx"
            self.make_source(source, [
                [None, "公司", None, None, None, None, 0, None, None, None],
                [None, "公司", None, None, None, None, 0, None, None, None],
            ])
            report = extract_financial(source, folder / "out")
            self.assertEqual(report["counts"]["snapshot_count"], 2)
            self.assertEqual(report["counts"]["invalid_stock_codes"], 2)
            self.assertEqual(report["counts"]["invalid_quarter_hints"], 2)

    def test_non_financial_workbook_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            workbook = Workbook()
            workbook.active.append(["Scode", "Qsubj"])
            workbook.active.append([1, "没有财务字段"])
            source = folder / "qa.xlsx"
            workbook.save(source)
            workbook.close()
            with self.assertRaisesRegex(ValueError, "no known financial"):
                extract_financial(source, folder / "out")
            self.assertFalse((folder / "out").exists())


if __name__ == "__main__":
    unittest.main()
