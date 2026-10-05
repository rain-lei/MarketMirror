import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import canonical_hash
from research.semantic import wuhan_v6_snapshot as target


class WuhanV6SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.question_at = "2020-01-20T10:00:00.000000+08:00"
        self.reply_at = "2020-01-21T11:00:00.000000+08:00"
        self.cutoff = "2020-01-22T23:59:59.999999+08:00"
        self.pair = {"qa_id": "qa-1", "stock_code": "000001",
                     "question_available_at": self.question_at,
                     "reply_available_at": self.reply_at,
                     "question_topic": "policy"}
        self.row = ("qa-1", "000001", self.question_at, "政策审批进展如何？",
                    self.reply_at, "已收到正式受理通知，仍在审查。", 1, 1)

    def test_materializes_only_frozen_source_pair(self):
        items = target.select_fixed_items([self.row], [self.pair], self.cutoff)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["qa_id"], "qa-1")
        self.assertEqual(items[0]["question_topic"], "policy")
        self.assertEqual([part["source"] for part in items[0]["segments"]],
                         ["question", "reply"])
        self.assertEqual(items[0]["segments"][1]["text"], self.row[5])

    def test_rejects_future_or_changed_reply(self):
        future = (*self.row[:4], "2020-01-23T00:00:00.000000+08:00", *self.row[5:])
        changed_pair = {**self.pair,
                        "reply_available_at": "2020-01-23T00:00:00.000000+08:00"}
        with self.assertRaisesRegex(ValueError, "not visible"):
            target.select_fixed_items([future], [changed_pair], self.cutoff)
        with self.assertRaisesRegex(ValueError, "differs"):
            target.select_fixed_items([self.row], [{**self.pair, "question_topic": "liquidity"}],
                                      self.cutoff)
        with self.assertRaisesRegex(ValueError, "differs"):
            target.select_fixed_items([(*self.row[:5], "", 1, 1)], [self.pair], self.cutoff)

    def test_archived_pack_is_reconstructed_from_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "qa.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE qa_record (qa_id TEXT, stock_code TEXT, "
                "question_available_at TEXT, question_text TEXT, reply_available_at TEXT, "
                "reply_text TEXT, question_eligible INTEGER, reply_eligible INTEGER, "
                "source_file_hash TEXT)"
            )
            source_hash = "a" * 64
            connection.execute("INSERT INTO qa_record VALUES (?,?,?,?,?,?,?,?,?)",
                               (*self.row, source_hash))
            connection.commit()
            connection.close()
            config_path = root / "snapshot.json"
            universe_path = root / "universe.json"
            prompt_path = root / "frozen-prompt.md"
            prompt_path.write_text("frozen v6 prompt", encoding="utf-8")
            output = root / "research_outputs" / "sixth-source"
            output.parent.mkdir()
            config_path.write_text(json.dumps({"run_id": "test-sixth",
                                               "qa_source_sha256": source_hash,
                                               "snapshot_as_of": self.cutoff}), encoding="utf-8")
            universe_path.write_text(json.dumps({
                "selected_pairs": [self.pair], "stock_codes": ["000001"],
                "selection_audit": {"sample_size": 1, "topic_quotas": {"policy": 1},
                                    "selection_rule": "test-frozen-rule"}},
                ensure_ascii=False), encoding="utf-8")

            def fake_inputs(config, universe):
                paths = (config.resolve(), universe.resolve(), database.resolve(),
                         prompt_path.resolve())
                return ({str(path): file_sha256(path) for path in paths}, database,
                        {"sources": [{"sha256": source_hash, "path": "test-workbook"}]})

            with (patch.object(target, "ROOT", root),
                  patch.object(target, "PROMPT", prompt_path),
                  patch.object(target, "verify_existing", return_value={"companies": 1}),
                  patch.object(target, "_inputs", side_effect=fake_inputs),
                  patch.object(target, "_code_hashes", return_value={"builder": "test-hash"})):
                manifest = target.build_snapshot(config_path, universe_path, output)
                items, verified = target.verify_snapshot(config_path, universe_path, output)
                self.assertEqual(len(items), 1)
                self.assertEqual(verified["experiment_id"], manifest["experiment_id"])

                item_path = output / target.ITEMS_NAME
                forged = json.loads(item_path.read_text(encoding="utf-8"))
                forged["segments"][1]["text"] = "伪造的公司回复"
                forged["source_text_sha256"] = canonical_hash(forged["segments"])
                item_path.write_text(json.dumps(forged, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
                manifest_path = output / target.MANIFEST_NAME
                saved = json.loads(manifest_path.read_text(encoding="utf-8"))
                saved["artifacts"][target.ITEMS_NAME]["sha256"] = file_sha256(item_path)
                manifest_path.write_text(json.dumps(saved, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "differs from frozen source"):
                    target.verify_snapshot(config_path, universe_path, output)


if __name__ == "__main__":
    unittest.main()
