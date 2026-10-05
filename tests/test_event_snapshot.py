import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from research.data_pipeline.build_dataset import VERSION as QA_VERSION
from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import VERSION as PACK_VERSION
from research.semantic.event_snapshot import (SELECTION_RULE, UNIVERSE_SELECTION_RULE, build_snapshot,
                                              select_latest_confirmed_replies, select_point_in_time_universe)
from research.semantic.prompt_contract import QUOTE_PROMPT_VERSION, prompt_path
from research.semantic.signal_validation import load_pack


class EventSnapshotTest(unittest.TestCase):
    def test_point_in_time_universe_is_deterministic_and_uses_only_valid_codes(self):
        codes = ["000001", "000002", "600519", "not-a-code"]
        first = select_point_in_time_universe(codes, "fixed-seed", 3)
        self.assertEqual(first, select_point_in_time_universe(reversed(codes), "fixed-seed", 3))
        self.assertEqual(set(first), {"000001", "000002", "600519"})
        with self.assertRaisesRegex(ValueError, "smaller"):
            select_point_in_time_universe(codes, "fixed-seed", 4)

    def test_selection_uses_latest_fully_visible_confirmed_reply_per_company(self):
        lower = datetime.fromisoformat("2020-01-01T00:00:00+08:00")
        as_of = datetime.fromisoformat("2020-01-22T23:59:59.999999+08:00")
        rows = [
            ("q1", "000001", "2020-01-03T09:00:00+08:00", "较早问题", "2020-01-04T09:00:00+08:00", "较早回复", 1, 1),
            ("q2", "000001", "2020-01-20T09:00:00+08:00", "较晚问题", "2020-01-23T00:00:00+08:00", "事后回复", 1, 1),
            ("q3", "000001", "2020-01-10T09:00:00+08:00", "另一条有效问题", "2020-01-11T09:00:00+08:00", "最新可见回复", 1, 1),
            ("q4", "000002", "2020-01-23T00:00:00+08:00", "事后问题", "2020-01-23T01:00:00+08:00", "事后回复", 1, 1),
            ("q5", "600519", "2020-01-09T09:00:00+08:00", "未确认问题", "2020-01-10T09:00:00+08:00", "未确认回复", 1, 0),
        ]

        items, counts = select_latest_confirmed_replies(rows, {"000001", "000002", "600519"}, lower, as_of)

        self.assertEqual([item["qa_id"] for item in items], ["q3"])
        self.assertEqual(items[0]["stage"], "reply")
        self.assertEqual([segment["source"] for segment in items[0]["segments"]], ["question", "reply"])
        self.assertEqual(items[0]["available_at"], "2020-01-11T09:00:00+08:00")
        self.assertEqual(counts, {"eligible_question_rows": 4, "eligible_confirmed_reply_rows": 2,
                                  "selected_companies": 1, "companies_without_reply_snapshot": 2})

    def test_builder_emits_a_hash_checked_model_runner_compatible_pack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_dir = root / "research" / "configs"
            config_dir.mkdir(parents=True)
            output_root = root / "research_outputs"
            unified = output_root / "unified"
            unified.mkdir(parents=True)
            database = unified / "dataset.sqlite"
            source_path = root / "source.xlsx"
            source_path.write_bytes(b"synthetic source fixture")
            source_hash = file_sha256(source_path)
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE dataset_metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
                CREATE TABLE qa_record (
                    qa_id TEXT, stock_code TEXT, question_available_at TEXT, question_text TEXT,
                    reply_available_at TEXT, reply_text TEXT, question_eligible INTEGER, reply_eligible INTEGER,
                    source_file_hash TEXT
                );
            """)
            metadata = {"dataset_id": "test-dataset", "pipeline_version": QA_VERSION,
                        "code_sha256": "code-hash", "feature_policy_sha256": "policy-hash"}
            connection.executemany("INSERT INTO dataset_metadata VALUES (?,?)",
                                   [(key, json.dumps(value)) for key, value in metadata.items()])
            connection.executemany("INSERT INTO qa_record VALUES (?,?,?,?,?,?,?,?,?)", [
                ("old", "000001", "2020-01-02T09:00:00.000000+08:00", "旧问题",
                 "2020-01-03T09:00:00.000000+08:00", "旧回复", 1, 1, source_hash),
                ("latest", "000001", "2020-01-15T09:00:00.000000+08:00", "新问题",
                 "2020-01-16T09:00:00.000000+08:00", "新回复", 1, 1, source_hash),
                ("future-reply", "000002", "2020-01-10T09:00:00.000000+08:00", "事前提问",
                 "2020-01-23T00:00:00.000000+08:00", "事后回复不可见", 1, 1, source_hash),
            ])
            connection.commit()
            connection.close()
            manifest = {**metadata, "pipeline_version": QA_VERSION,
                        "sources": [{"path": str(source_path), "sha256": source_hash}],
                        "artifacts": {database.name: {"sha256": file_sha256(database)}}}
            (unified / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            seed = "fixture-seed"
            sample_codes = select_point_in_time_universe(["000001", "000002"], seed, 2)
            universe_config = {"stock_codes": sample_codes, "selection": {
                "selection_rule": UNIVERSE_SELECTION_RULE, "sample_size": 2, "seed": seed,
                "qa_source_sha256": source_hash, "question_window_start": "2020-01-01",
                "snapshot_as_of": "2020-01-22T23:59:59.999999+08:00"}}
            (config_dir / "universe.json").write_text(json.dumps(universe_config), encoding="utf-8")
            (config_dir / "event.json").write_text(json.dumps({
                "run_id": "test", "market_manifest": "unused.json", "data_kind": "observed",
                "stock_codes": ["000001", "000002"], "events": [{
                    "event_id": "test_event", "event_date": "2020-01-23", "visible_date": "2020-01-23",
                    "event_type": "policy", "evidence_source": "https://example.test/event"}]}), encoding="utf-8")
            config = {"run_id": "snapshot-test", "qa_database": "../../research_outputs/unified/dataset.sqlite",
                      "qa_source_sha256": source_hash, "universe_config": "universe.json",
                      "event_config": "event.json", "event_id": "test_event",
                      "question_window_start": "2020-01-01", "snapshot_as_of": "2020-01-22T23:59:59.999999+08:00",
                      "selection_rule": SELECTION_RULE}
            config_path = config_dir / "snapshot.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            output_dir = output_root / "snapshot"

            built = build_snapshot(config_path, output_dir)
            items, loaded_manifest = load_pack(output_dir)

            self.assertEqual(built["pipeline_version"], PACK_VERSION)
            self.assertEqual(loaded_manifest["counts"]["items"], 1)
            self.assertEqual(loaded_manifest["counts"]["companies_without_reply_snapshot"], 1)
            protocol = loaded_manifest["frozen_model_protocol"]
            self.assertEqual(protocol["model_id"], "DeepSeek-V4-Flash-0731-W8A8")
            self.assertEqual(protocol["prompt_version"], QUOTE_PROMPT_VERSION)
            self.assertEqual(protocol["prompt_sha256"], file_sha256(prompt_path(QUOTE_PROMPT_VERSION)))
            self.assertIn(str(prompt_path(QUOTE_PROMPT_VERSION).resolve()), loaded_manifest["input_sha256"])
            item = next(iter(items.values()))
            self.assertEqual(item["qa_id"], "latest")
            self.assertEqual(item["available_at"], "2020-01-16T09:00:00.000000+08:00")
            self.assertNotIn("事后", json.dumps(item, ensure_ascii=False))
            for name, artifact in loaded_manifest["artifacts"].items():
                self.assertEqual(file_sha256(output_dir / name), artifact["sha256"])


if __name__ == "__main__":
    unittest.main()
