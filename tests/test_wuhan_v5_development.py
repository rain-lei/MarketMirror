import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic import wuhan_v5_development as target


class WuhanV5DevelopmentTest(unittest.TestCase):
    def test_model_payload_contains_visible_segments_only(self):
        items = {
            "item-one": {"item_id": "item-one", "stock_code": "000001",
                         "source_text_sha256": "a" * 64,
                         "segments": [{"source": "question", "text": "问题一"},
                                      {"source": "reply", "text": "公司回复一"}]},
            "item-two": {"item_id": "item-two", "stock_code": "000002",
                         "source_text_sha256": "b" * 64,
                         "segments": [{"source": "question", "text": "问题二"},
                                      {"source": "reply", "text": "公司回复二"}]},
        }
        identity = {"experiment_id": "dev-test", "source_experiment_id": "source-test",
                    "inputs": {"prompt": "test-hash"}, "code": {"runner": "test-hash"}}
        calls = []

        def fake_completion(url, key, model, messages, timeout, retries, temperature):
            calls.append({"url": url, "key": key, "model": model,
                          "messages": messages, "temperature": temperature})
            return '{"events":[]}'

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(target, "OUTPUTS", root),
                  patch.object(target, "PROMPT", root / "prompt.md"),
                  patch.object(target, "preflight", return_value=(items, [], identity)),
                  patch.object(target, "request_completion", side_effect=fake_completion)):
                (root / "prompt.md").write_text("frozen candidate", encoding="utf-8")
                result = target.run_raw(root / "development", api_key="test-key")
                self.assertEqual(result["model_rows"], 2)
                self.assertEqual(result["request_failures"], 0)
                self.assertEqual(len(calls), 2)
                for index, call in enumerate(calls):
                    self.assertEqual(call["model"], target.MODEL)
                    self.assertEqual(call["temperature"], 0.0)
                    self.assertEqual(call["messages"][0]["content"], "frozen candidate")
                    payload = json.loads(call["messages"][1]["content"])
                    self.assertEqual(payload, {"segments": list(items.values())[index]["segments"]})
                    self.assertNotIn("item_id", call["messages"][1]["content"])
                    self.assertNotIn("stock_code", call["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
