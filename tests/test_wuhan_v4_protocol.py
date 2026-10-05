import json
import unittest

from research.semantic.wuhan_protocol_v4 import VERSION, _normalize


def make_event(source, quote):
    return {
        "event_type": "earnings",
        "direction": "neutral",
        "affected_industries": [],
        "horizon": "unknown",
        "intensity": 0.5,
        "uncertainty": 0.5,
        "evidence_quotes": [{"source": source, "quote": quote}],
    }


class WuhanV4ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.item = {
            "item_id": "item-1",
            "source_text_sha256": "source-hash",
            "segments": [
                {"source": "question", "text": "是否盈利？"},
                {"source": "reply", "text": "公司已实现盈利。"},
            ],
        }

    def raw_row(self, event):
        return {
            "item_id": "item-1",
            "source_text_sha256": "source-hash",
            "model_id": "DeepSeek-V4-Flash-0731-W8A8",
            "prompt_version": VERSION,
            "raw_response": json.dumps({"events": [event]}, ensure_ascii=False),
        }

    def test_reply_only_quote_is_grounded(self):
        rows = _normalize({"item-1": self.item}, [self.raw_row(make_event("reply", "公司已实现盈利。"))])

        self.assertIsNone(rows[0]["parse_error"])
        self.assertEqual(rows[0]["events"][0]["evidence_spans"], [
            {"source": "reply", "start": 0, "end": 8, "quote": "公司已实现盈利。"}
        ])

    def test_question_only_evidence_fails_closed(self):
        rows = _normalize({"item-1": self.item}, [self.raw_row(make_event("question", "是否盈利？"))])

        self.assertEqual(rows[0]["events"], [])
        self.assertIn("reply-only", rows[0]["parse_error"])

    def test_non_exact_quote_fails_closed(self):
        rows = _normalize({"item-1": self.item}, [self.raw_row(make_event("reply", "公司已经盈利"))])

        self.assertEqual(rows[0]["events"], [])
        self.assertIsNotNone(rows[0]["parse_error"])


if __name__ == "__main__":
    unittest.main()
