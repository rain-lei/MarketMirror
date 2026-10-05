import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic.annotation_pack import canonical_hash
from research.semantic.wuhan_model_v3 import run
from research.semantic.wuhan_protocol_v3 import audit_v3, normalize_v3


class WuhanProtocolV3Test(unittest.TestCase):
    def test_gateway_payload_contains_only_question_and_reply_segments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / "pack"
            pack.mkdir()
            segments = [{"source": "question", "text": "投资者问题"},
                        {"source": "reply", "text": "公司回复"}]
            item = {"item_id": "item-private-id", "stock_code": "000001",
                    "source_text_sha256": canonical_hash(segments), "segments": segments,
                    "reference_label": {"events": [{"event_type": "earnings"}]}}
            (pack / "annotation_manifest.json").write_text("{}", encoding="utf-8")
            (pack / "annotation_items.jsonl").write_text(
                json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
            with patch("research.semantic.wuhan_model_v3.verify_pack",
                       return_value=({"item-private-id": item}, {"experiment_id": "fixture"})), \
                 patch("research.semantic.wuhan_model_v3.request_completion",
                       return_value='{"events":[]}') as request:
                run(pack, root / "raw", api_key="synthetic-fixture-only")

            sent_payload = json.loads(request.call_args.args[3][1]["content"])
            self.assertEqual(sent_payload, {"segments": segments})
            raw_artifact = (root / "raw/model_raw_outputs.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("reference_label", raw_artifact)
            self.assertNotIn("item-private-id", json.dumps(sent_payload))

    def test_failed_request_can_resume_and_checkpoint_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / "pack"
            pack.mkdir()
            segments = [{"source": "question", "text": "项目进度？"},
                        {"source": "reply", "text": "项目正在实施。"}]
            item = {"item_id": "item-1", "stock_code": "000001",
                    "source_text_sha256": canonical_hash(segments), "segments": segments}
            (pack / "annotation_manifest.json").write_text("{}", encoding="utf-8")
            (pack / "annotation_items.jsonl").write_text(
                json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
            with patch("research.semantic.wuhan_model_v3.verify_pack",
                       return_value=({"item-1": item}, {"experiment_id": "fixture"})), \
                 patch("research.semantic.wuhan_model_v3.request_completion",
                       side_effect=[RuntimeError("temporary gateway failure"), '{"events":[]}']):
                first = run(pack, root / "raw", api_key="synthetic-fixture-only")
                self.assertEqual(first["request_failures"], 1)
                resumed = run(pack, root / "raw", api_key="synthetic-fixture-only", resume=True)
                self.assertEqual(resumed["request_failures"], 0)
                self.assertEqual(resumed["model_rows"], 1)
                raw_path = root / "raw/model_raw_outputs.jsonl"
                original = raw_path.read_text(encoding="utf-8")
                raw_path.write_text("X" + original[1:], encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "resume checkpoint raw output hash mismatch"):
                    run(pack, root / "raw", api_key="synthetic-fixture-only", resume=True)

    def test_reply_only_protocol_normalizes_and_audits_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / "pack"
            pack.mkdir()
            items = {}
            for index in (1, 2):
                segments = [{"source": "question", "text": "提问中的猜测"},
                            {"source": "reply", "text": f"公司确认第{index}项已开始实施。"}]
                item = {"item_id": f"item-{index}", "stock_code": f"00000{index}",
                        "source_text_sha256": canonical_hash(segments), "segments": segments}
                items[item["item_id"]] = item
            (pack / "annotation_manifest.json").write_text("{}", encoding="utf-8")
            (pack / "annotation_items.jsonl").write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items.values()),
                encoding="utf-8")
            event = {"event_type": "other", "direction": "neutral",
                     "affected_industries": [], "horizon": "unknown",
                     "intensity": 0.3, "uncertainty": 0.2,
                     "evidence_quotes": [{"source": "reply", "quote": "公司确认第1项已开始实施。"}]}
            bad = {**event, "evidence_quotes": [{"source": "question", "quote": "提问中的猜测"}]}
            responses = [json.dumps({"events": [event]}, ensure_ascii=False),
                         json.dumps({"events": [bad]}, ensure_ascii=False)]
            with patch("research.semantic.wuhan_model_v3.verify_pack",
                       return_value=(items, {"experiment_id": "fixture"})), \
                 patch("research.semantic.wuhan_model_v3.request_completion", side_effect=responses) as request:
                with self.assertRaisesRegex(ValueError, "API_KEY is empty"):
                    run(pack, root / "no-key", api_key="")
                self.assertFalse((root / "no-key").exists())
                raw = run(pack, root / "raw", api_key="fixture-key")
            self.assertEqual(raw["model_rows"], 2)
            self.assertEqual(request.call_count, 2)
            self.assertIn("只能是 `reply`", request.call_args.args[3][0]["content"])
            with patch("research.semantic.wuhan_protocol_v3.verify_pack",
                       return_value=(items, {"experiment_id": "fixture"})):
                normalized = normalize_v3(pack, root / "raw", root / "normalized")
                self.assertEqual(normalized["parse_errors"], 1)
                audit = audit_v3(pack, root / "raw", root / "normalized")
                self.assertEqual(audit["model_rows"], 2)
                self.assertTrue(audit["reply_only_evidence"])
                prediction_path = root / "normalized/model_predictions.jsonl"
                prediction_path.write_text(prediction_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "normalized predictions differ"):
                    audit_v3(pack, root / "raw", root / "normalized")


if __name__ == "__main__":
    unittest.main()
