import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic import prepare_wuhan_v5_holdout as target


class WuhanV5HoldoutSelectionTest(unittest.TestCase):
    def test_fifth_membership_uses_only_remaining_question_active_codes(self):
        candidates = {f"{index:06d}" for index in range(1, 15)}
        groups = [{"000001"}, {"000002"}, {"000003"}, {"000004"}]
        topics = {code: set() for code in candidates - set().union(*groups)}
        for code, name in zip(("000005", "000006", "000007", "000008", "000009"),
                              target.QUESTION_TERMS):
            topics[code].add(name)
        quotas = {name: 1 for name in (*target.QUESTION_TERMS, "general")}
        base = {"qa_source_sha256": "source-hash",
                "question_window_start": "2020-01-01",
                "snapshot_as_of": "2020-01-22T23:59:59.999999+08:00"}
        hashes = {"source_database_sha256": "db-hash",
                  "source_file_sha256": "source-hash",
                  "source_manifest_sha256": "manifest-hash"}
        audit = [{"name": f"prior-{index}"} for index in range(4)]
        with tempfile.TemporaryDirectory() as directory:
            prompt = Path(directory) / "PROMPT_WUHAN_V5.md"
            prompt.write_text("frozen prompt", encoding="utf-8")
            with (patch.object(target, "ROOT", Path(directory)),
                  patch.object(target, "PROMPT", prompt),
                  patch.object(target, "SAMPLE_SIZE", 6),
                  patch.object(target, "TOPIC_QUOTAS", quotas),
                  patch.object(target, "_base_and_frame",
                               return_value=(base, candidates, hashes)),
                  patch.object(target, "_prior_groups",
                               return_value=(groups, audit)),
                  patch.object(target, "_question_topics", return_value=topics)):
                universe, snapshot = target.build_configs()
        expected, by_topic = target.select_topic_codes(topics, quotas=quotas)
        self.assertEqual(universe["stock_codes"], expected)
        self.assertEqual({name: len(codes) for name, codes in by_topic.items()}, quotas)
        self.assertEqual(set(universe["stock_codes"]) & set().union(*groups), set())
        self.assertEqual(universe["selection_audit"]["excluded_company_count"], 4)
        self.assertEqual(universe["selection_audit"]["remaining_candidate_companies"], 10)
        self.assertEqual(snapshot["universe_config"], target.UNIVERSE_NAME)

    def test_prompt_must_exist_before_fifth_membership_is_selected(self):
        with patch.object(target, "PROMPT", Path("missing-v5-frozen-prompt.md")):
            with self.assertRaises(FileNotFoundError):
                target.build_configs()


if __name__ == "__main__":
    unittest.main()
