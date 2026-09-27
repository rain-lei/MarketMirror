import copy
import json
import unittest

from research.semantic.parse_model_outputs import normalize_rows
from research.semantic.quote_grounding import ground_event


def quote_event(quote="收入增长", source="reply"):
    return {"event_type": "earnings", "direction": "positive", "affected_industries": [],
            "horizon": "unknown", "intensity": 0.5, "uncertainty": 0.3,
            "evidence_quotes": [{"source": source, "quote": quote}]}


class QuoteGroundingTest(unittest.TestCase):
    def setUp(self):
        self.item = {"item_id": "item", "source_text_sha256": "a" * 64,
                     "segments": [{"source": "question", "text": "是否收入增长？"},
                                  {"source": "reply", "text": "公司🙂\n收入增长。"}]}

    def test_exact_unicode_quote_gets_codepoint_offsets_without_mutating_input(self):
        event = quote_event()
        original = copy.deepcopy(event)
        result = ground_event(event, self.item)
        self.assertEqual(result["evidence_spans"],
                         [{"source": "reply", "start": 4, "end": 8, "quote": "收入增长"}])
        self.assertEqual(event, original)
        self.assertNotIn("evidence_quotes", result)

    def test_absent_invisible_repeated_overlapping_and_duplicate_quotes_fail(self):
        cases = [(quote_event("收入下降"), self.item, "absent"),
                 (quote_event(source="future"), self.item, "not visible"),
                 (quote_event("aa"), {"segments": [{"source": "reply", "text": "aaa"}]}, "ambiguous"),
                 (quote_event("收入增长"), {"segments": [{"source": "reply", "text": "收入增长，收入增长"}]}, "ambiguous")]
        repeated = quote_event()
        repeated["evidence_quotes"] *= 2
        cases.append((repeated, self.item, "duplicate"))
        for event, source, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                ground_event(event, source)

    def test_longer_context_disambiguates_and_spaces_are_never_normalized(self):
        source = {"segments": [{"source": "reply", "text": "昨日收入增长，今日收入增长。 a  b"}]}
        self.assertEqual(ground_event(quote_event("今日收入增长"), source)["evidence_spans"][0]["start"], 7)
        with self.assertRaisesRegex(ValueError, "absent"):
            ground_event(quote_event("a b"), source)

    def test_v2_rejects_offsets_and_invalid_signal_values(self):
        event = quote_event()
        event["evidence_quotes"][0]["start"] = 4
        with self.assertRaisesRegex(ValueError, "quote fields"):
            ground_event(event, self.item)
        event = quote_event()
        event["intensity"] = True
        with self.assertRaisesRegex(ValueError, "finite number"):
            ground_event(event, self.item)

    def test_v1_offset_failure_is_not_repaired_and_v2_never_accepts_v1_shape(self):
        base = {"item_id": "item", "source_text_sha256": "a" * 64, "model_id": "fixture"}
        event = ground_event(quote_event(), self.item)
        event["evidence_spans"][0]["start"] = 0
        row = {**base, "prompt_version": "semantic-prompt-v1", "raw_response": json.dumps({"events": [event]})}
        result = normalize_rows({"item": self.item}, [row])[0]
        self.assertIsNotNone(result["parse_error"])
        row["prompt_version"] = "semantic-prompt-v2"
        self.assertIn("quote schema", normalize_rows({"item": self.item}, [row])[0]["parse_error"])
        row["prompt_version"] = "unregistered-prompt"
        with self.assertRaisesRegex(ValueError, "unsupported raw model prompt"):
            normalize_rows({"item": self.item}, [row])

    def test_v2_normalization_keeps_entire_row_failed_if_one_quote_is_bad(self):
        row = {"item_id": "item", "source_text_sha256": "a" * 64, "model_id": "fixture",
               "prompt_version": "semantic-prompt-v2",
               "raw_response": json.dumps({"events": [quote_event(), quote_event("编造")]})}
        result = normalize_rows({"item": self.item}, [row])[0]
        self.assertEqual(result["events"], [])
        self.assertIn("absent", result["parse_error"])

    def test_declared_v1_end_cannot_rely_on_python_slice_clipping(self):
        event = ground_event(quote_event("收入增长。"), self.item)
        event["evidence_spans"][0]["end"] = 999
        row = {"item_id": "item", "source_text_sha256": "a" * 64, "model_id": "fixture",
               "prompt_version": "semantic-prompt-v1", "raw_response": json.dumps({"events": [event]})}
        self.assertIn("extend past", normalize_rows({"item": self.item}, [row])[0]["parse_error"])


if __name__ == "__main__":
    unittest.main()
