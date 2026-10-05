import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from research.semantic import wuhan_model_v6 as target


class WuhanModelV6Test(unittest.TestCase):
    def test_request_sends_only_visible_question_and_reply(self):
        item = {"item_id": "item-one", "stock_code": "000001",
                "source_text_sha256": "a" * 64,
                "reference_label": "regulation", "financial_fields": {"revenue": 100},
                "segments": [{"source": "question", "text": "政策影响？"},
                             {"source": "reply", "text": "公司回复。"}]}
        items = {"item-one": item}
        pack = {"experiment_id": "sixth-pack"}
        review = {"generated_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}
        requests = []

        def fake_completion(url, key, model, messages, timeout, retries, temperature):
            requests.append({"url": url, "model": model, "messages": messages,
                             "temperature": temperature})
            return '{"events":[]}'

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "research_outputs" / "model"
            output.parent.mkdir()
            prompt = root / "frozen.md"
            prompt.write_text("frozen v6", encoding="utf-8")
            with (patch.object(target, "ROOT", root),
                  patch.object(target, "PROMPT", prompt),
                  patch.object(target, "preflight", return_value=(items, pack, review)),
                  patch.object(target, "_inputs", return_value={"prompt": "test-hash"}),
                  patch.object(target, "_code_hashes", return_value={"runner": "test-hash"}),
                  patch.object(target, "request_completion", side_effect=fake_completion)):
                run = target.run(pack_dir=root / "pack", review_dir=root / "review",
                                 output_dir=output, api_key="test-key")

        self.assertEqual(run["model_rows"], 1)
        self.assertEqual(run["request_failures"], 0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["model"], target.MODEL)
        self.assertEqual(requests[0]["temperature"], 0.0)
        self.assertEqual(requests[0]["messages"][0]["content"], "frozen v6")
        self.assertEqual(json.loads(requests[0]["messages"][1]["content"]),
                         {"segments": item["segments"]})
        for forbidden in ("stock_code", "financial_fields", "reference_label", "item_id"):
            self.assertNotIn(forbidden, requests[0]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
