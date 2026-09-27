import json
import unittest

from research.semantic.diagnose_model import diagnose_rows, evidence_locations


class ModelDiagnosticsTest(unittest.TestCase):
    def test_evidence_diagnosis_distinguishes_offset_error_hallucination_and_ambiguity(self):
        items = {"1": {"segments": [{"source": "reply", "text": "收入增长aaa"}]}}
        spans = [{"source": "reply", "start": 0, "end": 4, "quote": "收入增长"},
                 {"source": "reply", "start": 1, "end": 4, "quote": "收入增长"},
                 {"source": "reply", "start": 0, "end": 2, "quote": "aa"},
                 {"source": "reply", "start": 0, "end": 4, "quote": "编造原文"},
                 {"source": "question", "start": 0, "end": 4, "quote": "收入增长"}]
        rows = [{"item_id": "1", "raw_response": json.dumps({"events": [{"evidence_spans": spans}]})}]
        original = json.dumps(rows)
        self.assertEqual(evidence_locations(items, rows), {
            "exact_offsets": 1, "unique_exact_quote_wrong_offsets": 1,
            "quote_ambiguous_wrong_offsets": 1, "quote_not_present": 1, "invalid_source_or_quote": 1})
        self.assertEqual(json.dumps(rows), original)

    def test_failures_empty_responses_and_missing_rows_are_distinct(self):
        items = {str(i): {"stage": "question" if i < 2 else "reply", "split": "test",
                          "selection_stratum": "earnings", "source_text_sha256": str(i) * 64,
                          "segments": [{"source": "question", "text": "private"}]}
                 for i in range(3)}
        rows = [{"item_id": str(i), "source_text_sha256": str(i) * 64,
                 "model_id": "model", "prompt_version": "prompt", "events": [],
                 "parse_error": None if i == 0 else "ValueError: duplicate JSON key: private_identifier"}
                for i in range(2)]
        report = diagnose_rows(items, rows)
        self.assertEqual(report["coverage"]["valid_empty_rows"], 1)
        self.assertEqual(report["coverage"]["parse_error_rows"], 1)
        self.assertEqual(report["coverage"]["missing_rows"], 1)
        self.assertEqual(report["error_categories"], {"json_syntax": 1})
        self.assertEqual(report["groups"]["stage"]["reply"]["returned_rows"], 0)
        self.assertFalse(report["accuracy_claim_allowed"])
        self.assertNotIn("private_identifier", str(report))

    def test_multievent_counts_and_invalid_evidence_rejection(self):
        items = {"1": {"stage": "reply", "split": "test", "selection_stratum": "earnings",
                       "source_text_sha256": "a" * 64,
                       "segments": [{"source": "reply", "text": "收入增长"}]}}
        event = {"event_type": "earnings", "direction": "positive", "affected_industries": [],
                 "horizon": "unknown", "intensity": 0.5, "uncertainty": 0.2,
                 "evidence_spans": [{"source": "reply", "start": 0, "end": 4, "quote": "收入增长"}]}
        rows = [{"item_id": "1", "source_text_sha256": "a" * 64, "model_id": "model",
                 "prompt_version": "prompt", "events": [event, event], "parse_error": None}]
        report = diagnose_rows(items, rows)
        self.assertEqual(report["coverage"]["validated_events"], 2)
        self.assertEqual(report["coverage"]["valid_event_rows"], 1)
        event["evidence_spans"][0]["quote"] = "fake"
        with self.assertRaisesRegex(ValueError, "exactly match"):
            diagnose_rows(items, rows)


if __name__ == "__main__":
    unittest.main()
