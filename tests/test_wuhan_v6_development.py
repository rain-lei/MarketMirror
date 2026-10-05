import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic import wuhan_v6_development as target


class WuhanV6DevelopmentTest(unittest.TestCase):
    def test_request_uses_only_question_and_reply_segments(self):
        items = {
            "item-one": {
                "item_id": "item-one", "stock_code": "000001",
                "source_text_sha256": "a" * 64,
                "financial_fields": {"revenue": 100},
                "review_label": "earnings",
                "segments": [{"source": "question", "text": "问题一"},
                             {"source": "reply", "text": "公司回复一"}],
            },
        }
        identity = {
            "experiment_id": "v6-dev-test", "fifth_pack_experiment_id": "fifth-pack-test",
            "inputs": {"prompt": "test-hash"}, "code": {"runner": "test-hash"},
        }
        requests = []

        def fake_completion(url, key, model, messages, timeout, retries, temperature):
            requests.append({"url": url, "model": model, "messages": messages,
                             "temperature": temperature})
            return '{"events":[]}'

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.md"
            prompt.write_text("v6 development prompt", encoding="utf-8")
            with (patch.object(target, "OUTPUTS", root),
                  patch.object(target, "PROMPT", prompt),
                  patch.object(target, "preflight", return_value=(items, [], identity)),
                  patch.object(target, "request_completion", side_effect=fake_completion)):
                run = target.run_raw(root / "development", api_key="test-key")

        self.assertEqual(run["sample_role"], "seen_development_only")
        self.assertEqual(run["model_rows"], 1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["temperature"], 0.0)
        self.assertEqual(requests[0]["model"], target.MODEL)
        self.assertEqual(requests[0]["messages"][0]["content"], "v6 development prompt")
        self.assertEqual(json.loads(requests[0]["messages"][1]["content"]),
                         {"segments": items["item-one"]["segments"]})
        for forbidden in ("stock_code", "financial_fields", "review_label", "item_id"):
            self.assertNotIn(forbidden, requests[0]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
