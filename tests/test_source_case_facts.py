import copy
import json
import unittest

from research.semantic.source_case_facts import (
    asof_facts, build_cases, digest, canonical, evidence_span, normalize_facts, validate_fact_record)


def item():
    row = {"case_id": "qa_test", "source_kind": "company_reply_workbook",
        "available_at": "2020-07-24T23:59:59.999999+08:00",
        "segments": [{"source": "question", "text": "是否已经通过审批？"},
                     {"source": "reply", "text": "能否通过审批具有不确定性。请关注公司公告。"}]}
    row["case_text_sha256"] = digest(canonical(row["segments"]))
    return row


def response():
    return json.dumps({"facts": [{"kind": "approval_uncertainty", "status": "uncertain",
        "claim": "是否获批尚不确定。", "evidence": [{"source": "reply", "quote": "能否通过审批具有不确定性。"}]}]},
        ensure_ascii=False)


class SourceCaseFactsTest(unittest.TestCase):
    def test_full_registered_cases_reconstruct_from_original_archives(self):
        first = build_cases()
        self.assertEqual(first, build_cases())
        self.assertEqual(len(first), 6)
        self.assertTrue(all(c["development_case_not_holdout"] for c in first))
        self.assertTrue(first[0]["title"].endswith("答记者问"))
        self.assertNotIn("责任编辑", first[1]["segments"][0]["text"])
        self.assertIn("2月3日（星期一）正常开市", first[2]["segments"][0]["text"])
        self.assertTrue(all(c["available_at"].endswith("23:59:59.999999+08:00") for c in first[3:]))

    def test_source_quotes_and_offsets_validate_without_fuzzy_matching(self):
        case = item()
        record = normalize_facts(case, response())
        self.assertEqual(validate_fact_record(case, record), record)
        span = record["facts"][0]["evidence"][0]
        self.assertEqual(case["segments"][1]["text"][span["start"]:span["end"]], span["quote"])
        wrong = copy.deepcopy(record); wrong["facts"][0]["evidence"][0]["start"] += 1
        with self.assertRaises(ValueError):
            validate_fact_record(case, wrong)
        with self.assertRaises(ValueError):
            evidence_span(case, "reply", "审批已经通过")

    def test_ambiguous_and_question_only_quotes_are_rejected(self):
        case = item(); case["segments"][1]["text"] = "请关注公告。请关注公告。"
        with self.assertRaises(ValueError):
            evidence_span(case, "reply", "请关注公告。")
        case = item()
        raw = json.loads(response()); raw["facts"][0]["evidence"] = [{"source":"question","quote":"是否已经通过审批？"}]
        with self.assertRaisesRegex(ValueError, "question"):
            normalize_facts(case, json.dumps(raw,ensure_ascii=False))

    def test_future_reply_never_enters_earlier_or_same_day_cutoff(self):
        case = item(); record = normalize_facts(case,response())
        before = asof_facts(case, record, "2020-07-24T12:00:00+08:00")
        self.assertFalse(before["available"]); self.assertEqual(before["facts"],[])
        after = asof_facts(case, record, "2020-07-25T00:00:00+08:00")
        self.assertTrue(after["available"]); self.assertEqual(after["facts"],record["facts"])
        with self.assertRaises(ValueError):
            asof_facts(case, record, "2020-07-25T00:00:00")

    def test_invalid_json_and_added_price_direction_are_rejected(self):
        case = item()
        for raw in ('{"facts":[],"facts":[]}', '{"facts":[],"price_signal":0.5}', '{"facts":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_facts(case,raw)
        wrong=json.loads(response());wrong["facts"][0]["return_direction"]="positive"
        with self.assertRaises(ValueError):
            normalize_facts(case,json.dumps(wrong))


if __name__ == "__main__":
    unittest.main()
