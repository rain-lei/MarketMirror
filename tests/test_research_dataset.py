import csv
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from openpyxl import Workbook

from research.data_pipeline.asof_features import export_asof_features
from research.data_pipeline.build_dataset import build_dataset, normalize_timestamp, normalize_reply_timestamp, valid_stock_code
from research.data_pipeline.extract_financial import extract_financial
from research.data_pipeline.provenance import file_sha256


HEADERS = ["股票代码", "公司简称", "提问时间", "提问内容", "上市公司是否回复", "回复时间", "回复内容", "Isviolated", "季度", "用户名"]


def write_source(path, rows, financial=False):
    book = Workbook()
    book.active.title = "问答"
    book.active.append(HEADERS + (["资产总计", "p"] if financial else []))
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()


def read_features(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class ResearchDatasetTest(unittest.TestCase):
    def test_timestamp_offsets_date_precision_and_stock_codes(self):
        expected = "2020-01-01T08:00:00.000000+08:00"
        self.assertEqual(normalize_timestamp("2020-01-01T00:00:00Z"), expected)
        self.assertEqual(normalize_timestamp(datetime(2020, 1, 1, tzinfo=timezone.utc)), expected)
        self.assertEqual(normalize_timestamp("2020/01/01 08:00:00"), expected)
        self.assertIsNone(normalize_timestamp("2020-01-01"))
        self.assertIsNone(normalize_timestamp(date(2020, 1, 1)))
        self.assertEqual(valid_stock_code(1.0), "000001")
        for invalid in [True, 1.5, -1, 1234567, "SZ000001"]:
            self.assertIsNone(valid_stock_code(invalid))

    def test_multi_source_financial_linkage_provenance_and_repeatability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, recent = root / "history.xlsx", root / "recent.xlsx"
            write_source(source, [
                [1, "旧简称", "2020-01-02 10:00:00", "政策变化", "已回复", "2020-02-01 10:00:00", "注意风险", 1, 4, "甲", -5, 9],
                [1, "新简称", "2020-01-03 10:00:00", "流动性", "未回复", "2020-02-01 10:00:00", None, 1, 4, "乙", -5, 10],
            ], financial=True)
            write_source(recent, [[2, "其他公司", "2026-01-02 10:00:00", "营收", "未回复", None, None, None, None, "丙"]])
            source_hash = file_sha256(source)
            extract_financial(source, root / "fin")
            fin_db = root / "fin" / "financial_unverified.sqlite"
            financial_hash = file_sha256(fin_db)
            first = build_dataset([source, recent], root / "dataset", [fin_db])
            self.assertEqual(first["counts"]["qa_rows"], 3)
            self.assertEqual(first["counts"]["linked_financial_rows"], 2)
            self.assertEqual(first["counts"]["financial_snapshots"], 1)
            self.assertEqual(first["counts"]["companies"], 2)
            with closing(sqlite3.connect(root / "dataset" / "dataset.sqlite")) as db:
                ids = db.execute("SELECT qa_id FROM qa_record ORDER BY qa_id").fetchall()
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                aliases = db.execute("SELECT company_name FROM company_alias WHERE company_id='cn-stock-code:000001'").fetchall()
                self.assertEqual({a[0] for a in aliases}, {"旧简称", "新简称"})
                self.assertEqual(db.execute("SELECT COUNT(*) FROM financial_snapshot WHERE published_at IS NOT NULL").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT source_row FROM qa_record WHERE snapshot_id IS NOT NULL ORDER BY source_row").fetchall(), [(2,), (3,)])
            second = build_dataset([recent, source], root / "dataset", [fin_db])
            self.assertEqual(first["dataset_id"], second["dataset_id"])
            with closing(sqlite3.connect(root / "dataset" / "dataset.sqlite")) as db:
                self.assertEqual(ids, db.execute("SELECT qa_id FROM qa_record ORDER BY qa_id").fetchall())
            self.assertEqual(file_sha256(source), source_hash)
            self.assertEqual(file_sha256(fin_db), financial_hash)
            self.assertEqual(second["artifacts"]["dataset.sqlite"]["sha256"], file_sha256(root / "dataset" / "dataset.sqlite"))

    def test_date_only_replies_become_visible_only_on_following_day(self):
        self.assertEqual(normalize_reply_timestamp("2020-01-03"), ("2020-01-04T00:00:00.000000+08:00", True))
        self.assertEqual(normalize_reply_timestamp("2020-02-30"), (None, False))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "qa.xlsx"
            write_source(source, [
                [1, "公司", "2020-01-02 10:00:00", "政策", "已回复", "2020-01-03", "利润", None, None, "甲"],
                [1, "公司", "2020-01-02 00:00:00", "风险", "已回复", "2020-01-01", "疫情", None, None, "乙"],
            ])
            report = build_dataset([source], root / "dataset")
            self.assertEqual(report["counts"]["reply_date_only_next_day_bound"], 2)
            self.assertEqual(report["counts"]["eligible_reply_rows"], 1)
            database, output = root / "dataset" / "dataset.sqlite", root / "features.csv"
            for cutoff, expected in [("2020-01-03 23:59:59", 0), ("2020-01-04 00:00:00", 1)]:
                result = export_asof_features(database, output, "2020-01-01 00:00:00", cutoff)
                self.assertEqual(result["visible_reply_rows"], expected)
            self.assertEqual(read_features(output)[0]["reply_pandemic_count"], "0")

    def test_future_questions_replies_labels_and_financial_values_cannot_enter_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "history.xlsx"
            rows = [
                [1, "公司", "2020-01-02 09:00:00", "政策风险", "已回复", "2020-01-05 10:00:00", "疫情债务", 1, 4, "甲", 999, 99],
                [1, "公司", "2020-01-03 10:00:00", "营收", "已回复", "2020-01-03 11:00:00", "利润增长", 1, 4, "乙", 999, 99],
                [1, "公司", "2020-01-10 10:00:00", "疫情风险", "已回复", "2020-01-10 11:00:00", "债务", 1, 4, "丙", 999, 99],
            ]
            write_source(source, rows, financial=True)
            extract_financial(source, root / "fin")
            build_dataset([source], root / "dataset", [root / "fin" / "financial_unverified.sqlite"])
            output = root / "features.csv"
            export_asof_features(root / "dataset" / "dataset.sqlite", output,
                                 "2020-01-01 00:00:00", "2020-01-04 00:00:00")
            first = read_features(output)[0]
            self.assertEqual(first["question_count"], "2")
            self.assertEqual(first["known_reply_count"], "1")
            self.assertEqual(first["no_visible_reply_count"], "1")
            self.assertEqual(first["reply_pandemic_count"], "0")
            self.assertEqual(first["reply_earnings_count"], "1")
            self.assertFalse(any("label" in k or "financial" in k or "user" in k for k in first))
            for row in rows:
                row[7], row[8], row[10], row[11] = 0, 1, -999, -99
            rows[0][6] = "未来回复已改成回购"
            rows[2][3] = "未来提问已改成诉讼"
            write_source(source, rows, financial=True)
            extract_financial(source, root / "fin")
            build_dataset([source], root / "dataset", [root / "fin" / "financial_unverified.sqlite"])
            export_asof_features(root / "dataset" / "dataset.sqlite", output,
                                 "2020-01-01 00:00:00", "2020-01-04 00:00:00")
            self.assertEqual(first, read_features(output)[0])
            report = export_asof_features(root / "dataset" / "dataset.sqlite", output,
                                         "2020-01-01 00:00:00", "2020-01-06 00:00:00")
            self.assertEqual(report["visible_reply_rows"], 2)

    def test_invalid_rows_unconfirmed_replies_and_duplicate_occurrences_are_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "qa.xlsx"
            valid = [1, "公司", "2020-01-02 10:00:00", "政策", "未回复", "2020-01-02 11:00:00", "疫情", None, None, "甲"]
            write_source(source, [valid, valid,
                [1, "公司", "2020-01-03 10:00:00", "利润", "已回复", "2020-01-01 00:00:00", "债务", None, None, "乙"],
                [1, "公司", "2020-01-04", "风险", "未回复", None, None, None, None, "丙"],
                ["bad", "公司", "2020-01-04 10:00:00", "风险", "未回复", None, None, None, None, "丙"],
                [1, "公司", "2020-01-04 10:00:00", "=SUM(1,2)", "未回复", None, None, None, None, "丙"],
                [1, "公司", "2020-01-04 10:00:00", 123, "未回复", None, None, None, None, "丙"],
            ])
            report = build_dataset([source], root / "dataset")
            self.assertEqual(report["counts"]["qa_rows"], 7)
            self.assertEqual(report["counts"]["eligible_question_rows"], 3)
            self.assertEqual(report["counts"]["eligible_reply_rows"], 0)
            self.assertEqual(report["counts"]["duplicate_content_excess_rows"], 1)
            output = root / "features.csv"
            result = export_asof_features(root / "dataset" / "dataset.sqlite", output,
                                          "2020-01-01 00:00:00", "2020-01-10 00:00:00")
            self.assertEqual(result["question_rows"], 3)
            self.assertEqual(result["visible_reply_rows"], 0)
            self.assertEqual(read_features(output)[0]["reply_pandemic_count"], "0")

    def test_failed_build_does_not_replace_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "qa.xlsx"
            rows = [[1, "公司", "2020-01-02 10:00:00", "政策", "未回复", None, None, None, None, "甲"]]
            write_source(source, rows)
            build_dataset([source], root / "dataset")
            before = file_sha256(root / "dataset" / "dataset.sqlite")
            manifest_before = (root / "dataset" / "run_manifest.json").read_bytes()
            write_source(source, [rows[0] + [99, 5]], financial=True)
            with self.assertRaisesRegex(ValueError, "requires its matching"):
                build_dataset([source], root / "dataset")
            self.assertEqual(file_sha256(root / "dataset" / "dataset.sqlite"), before)
            self.assertEqual((root / "dataset" / "run_manifest.json").read_bytes(), manifest_before)
            self.assertEqual(list((root / "dataset").glob("*.tmp*")), [])

    def test_mismatched_financial_source_and_duplicate_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, copy = root / "qa.xlsx", root / "copy.xlsx"
            row = [1, "公司", "2020-01-02 10:00:00", "政策", "未回复", None, None, None, 1, "甲", 99, 5]
            write_source(source, [row], financial=True)
            extract_financial(source, root / "fin")
            shutil.copyfile(source, copy)
            with self.assertRaisesRegex(ValueError, "duplicate source"):
                build_dataset([source, copy], root / "dataset")
            row[3] = "已修改的数据"
            write_source(source, [row], financial=True)
            with self.assertRaisesRegex(ValueError, "does not match"):
                build_dataset([source], root / "dataset", [root / "fin" / "financial_unverified.sqlite"])

    def test_modified_database_invalid_windows_filters_and_overwrites_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "qa.xlsx"
            write_source(source, [[1, "公司", "2020-01-02 10:00:00", "政策", "未回复", None, None, None, None, "甲"]])
            build_dataset([source], root / "dataset")
            database, output = root / "dataset" / "dataset.sqlite", root / "features.csv"
            for start, end in [("2020-01-01", "2020-01-02 00:00:00"), ("2020-02-01 00:00:00", "2020-01-01 00:00:00")]:
                with self.assertRaises(ValueError):
                    export_asof_features(database, output, start, end)
            for code in ["bad", "000002"]:
                with self.assertRaises(ValueError):
                    export_asof_features(database, output, "2020-01-01 00:00:00", "2020-01-03 00:00:00", code)
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                export_asof_features(database, source, "2020-01-01 00:00:00", "2020-01-03 00:00:00")
            report = export_asof_features(database, output, "2020-01-03 00:00:00", "2020-01-04 00:00:00", "000001")
            self.assertEqual(report["question_rows"], 0)
            self.assertEqual(read_features(output)[0]["question_count"], "0")
            with closing(sqlite3.connect(database)) as db:
                db.execute("UPDATE qa_record SET question_text = '篡改'")
                db.commit()
            with self.assertRaisesRegex(ValueError, "dataset hash differs"):
                export_asof_features(database, output, "2020-01-01 00:00:00", "2020-01-03 00:00:00")


if __name__ == "__main__":
    unittest.main()
