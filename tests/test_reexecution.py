import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.registry.reexecute import compare_artifacts, load_config, sqlite_logical_digest


class ReexecutionTest(unittest.TestCase):
    def test_byte_comparison_detects_match_change_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "result.json"
            result.write_text('{"value": 1}\n', encoding="utf-8")
            expected = {"result.json": {"sha256": file_sha256(result)}}
            self.assertEqual(compare_artifacts(expected, root)[0]["status"], "identical")
            result.write_text('{"value": 2}\n', encoding="utf-8")
            self.assertEqual(compare_artifacts(expected, root)[0]["status"], "different_or_missing")
            result.unlink()
            self.assertIsNone(compare_artifacts(expected, root)[0]["regenerated_sha256"])

    def test_artifact_cannot_escape_fresh_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "escapes"):
                compare_artifacts({"../outside.txt": {"sha256": "0" * 64}}, Path(tmp))

    def test_only_top_level_generated_at_can_be_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, fresh = root / "original", root / "fresh"
            original.mkdir()
            fresh.mkdir()
            name = "event_results.json"
            (original / name).write_text(json.dumps({"generated_at": "old", "car": 0.02,
                                                     "nested": {"generated_at": "fixed"}}), encoding="utf-8")
            (fresh / name).write_text(json.dumps({"generated_at": "new", "car": 0.02,
                                                  "nested": {"generated_at": "fixed"}}), encoding="utf-8")
            expected = {name: {"sha256": file_sha256(original / name)}}
            self.assertEqual(compare_artifacts(expected, fresh, original, {name})[0]["status"],
                             "equivalent_except_generated_at")
            (fresh / name).write_text(json.dumps({"generated_at": "new", "car": 0.03,
                                                  "nested": {"generated_at": "fixed"}}), encoding="utf-8")
            self.assertEqual(compare_artifacts(expected, fresh, original, {name})[0]["status"],
                             "different_or_missing")

    def test_nested_timestamp_difference_remains_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, fresh = root / "original", root / "fresh"
            original.mkdir()
            fresh.mkdir()
            name = "event_results.json"
            (original / name).write_text(json.dumps({"generated_at": "old", "nested": {"generated_at": "old"}}), encoding="utf-8")
            (fresh / name).write_text(json.dumps({"generated_at": "new", "nested": {"generated_at": "new"}}), encoding="utf-8")
            self.assertEqual(compare_artifacts({name: {"sha256": file_sha256(original / name)}},
                                               fresh, original, {name})[0]["status"], "different_or_missing")

    def test_sqlite_comparison_ignores_only_build_timestamp_and_row_insertion_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original, fresh = root / "original", root / "fresh"
            original.mkdir()
            fresh.mkdir()
            name = "dataset.sqlite"
            for path, timestamp, order in ((original / name, "old", [1, 2]),
                                           (fresh / name, "new", [2, 1])):
                with closing(sqlite3.connect(path)) as conn:
                    conn.executescript("CREATE TABLE dataset_metadata (key TEXT PRIMARY KEY, value_json TEXT);"
                                       "CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT);")
                    conn.execute("INSERT INTO dataset_metadata VALUES (?, ?)", ("generated_at", json.dumps(timestamp)))
                    conn.execute("INSERT INTO dataset_metadata VALUES (?, ?)", ("dataset_id", json.dumps("fixed")))
                    conn.executemany("INSERT INTO records VALUES (?, ?)", [(i, str(i)) for i in order])
                    conn.commit()
            expected = {name: {"sha256": file_sha256(original / name)}}
            self.assertNotEqual(file_sha256(original / name), file_sha256(fresh / name))
            self.assertEqual(sqlite_logical_digest(original / name), sqlite_logical_digest(fresh / name))
            self.assertEqual(compare_artifacts(expected, fresh, original, compare_sqlite_logically={name})[0]["status"],
                             "equivalent_except_sqlite_generated_at_metadata")
            with closing(sqlite3.connect(fresh / name)) as conn:
                conn.execute("UPDATE records SET value = 'changed' WHERE id = 2")
                conn.commit()
            self.assertNotEqual(sqlite_logical_digest(original / name), sqlite_logical_digest(fresh / name))
            self.assertEqual(compare_artifacts(expected, fresh, original, compare_sqlite_logically={name})[0]["status"],
                             "different_or_missing")

    def test_reexecution_config_rejects_unknown_or_duplicate_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for runs in ([{"run_id": "unknown", "input": "x.json"}],
                         [{"run_id": "synthetic_stress", "input": "x.json"}] * 2):
                path.write_text(json.dumps({"integrity_catalog": "integrity.json", "runs": runs}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
