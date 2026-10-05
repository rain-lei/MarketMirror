import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic import wuhan_model_v5 as target


class WuhanV5ModelPayloadTest(unittest.TestCase):
    def test_request_contains_only_visible_segments_after_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.md"
            prompt.write_text("frozen prompt", encoding="utf-8")
            pack_dir, review_dir = root / "pack", root / "review"
            pack_dir.mkdir()
            review_dir.mkdir()
            output_dir = root / "research_outputs" / "run"
            item = {"item_id": "local-item-id", "source_text_sha256": "local-source-hash",
                    "stock_code": "123456", "financial_fields": {"private": True},
                    "segments": [{"source": "question", "text": "可见提问"},
                                 {"source": "reply", "text": "公司可见回复"}]}
            pack = {"experiment_id": "frozen-pack"}
            review = {"generated_at": "2020-01-01T00:00:00+00:00",
                      "audit": {"reviewed_items": 1},
                      "model_outputs_previously_seen": False,
                      "reference_labels": [{"private": True}]}
            sent = []

            def fake_completion(url, key, model, messages, timeout, retries, temperature):
                sent.append(messages)
                return '{"events":[]}'

            with (patch.object(target, "ROOT", root),
                  patch.object(target, "PROMPT", prompt),
                  patch.object(target, "preflight", return_value=({item["item_id"]: item}, pack, review)),
                  patch.object(target, "_inputs", return_value={"frozen": "hash"}),
                  patch.object(target, "_code_hashes", return_value={"runner": "hash"}),
                  patch.object(target, "request_completion", side_effect=fake_completion)):
                result = target.run(pack_dir, output_dir, api_key="fake", review_dir=review_dir)

            self.assertEqual(result["model_rows"], 1)
            self.assertEqual(result["request_failures"], 0)
            self.assertEqual(len(sent), 1)
            self.assertEqual(json.loads(sent[0][1]["content"]), {"segments": item["segments"]})
            self.assertNotIn("123456", sent[0][1]["content"])
            self.assertNotIn("local-item-id", sent[0][1]["content"])
            self.assertNotIn("private", sent[0][1]["content"])


if __name__ == "__main__":
    unittest.main()
