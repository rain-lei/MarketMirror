import json
import unittest

from research.semantic.holdout_overlap import compare_packs, normalize_text


def item(item_id, question, reply=None):
    segments = [{"source": "question", "text": question}]
    if reply is not None:
        segments.append({"source": "reply", "text": reply})
    return {"item_id": item_id, "segments": segments}


class HoldoutOverlapTest(unittest.TestCase):
    def test_normalization_and_matching_segments_find_reworded_duplicate(self):
        development = {"d1": item("d1", "ＡＢＣ公司：2020年利润增长", "感谢您的提问")}
        holdout = {"h1": item("h1", "ABC公司 2020年利润增长!", "完全不同的回复")}
        result = compare_packs(development, holdout)
        self.assertEqual(normalize_text("ＡＢＣ！"), "abc")
        self.assertEqual(result["max_trigram_jaccard"], 1.0)
        self.assertEqual(result["nearest_pairs"][0]["segment_source"], "question")
        self.assertEqual(result["holdout_items_at_or_above"]["1.0"], 1)
        self.assertNotIn("利润增长", json.dumps(result, ensure_ascii=False))

    def test_question_and_reply_are_not_compared_across_sources(self):
        development = {"d1": item("d1", "完全不相干的问题", "感谢您的关注")}
        holdout = {"h1": item("h1", "感谢您的关注")}
        result = compare_packs(development, holdout)
        self.assertLess(result["max_trigram_jaccard"], 1.0)
        self.assertEqual(result["segment_comparisons"], 1)


if __name__ == "__main__":
    unittest.main()
