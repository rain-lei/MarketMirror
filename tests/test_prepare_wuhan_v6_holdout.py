import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic import prepare_wuhan_v6_holdout as target


class WuhanV6HoldoutSelectionTest(unittest.TestCase):
    def test_candidate_pairs_use_visible_question_topic_without_reply_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "qa.sqlite"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE qa_record (qa_id TEXT, stock_code TEXT, "
                "question_available_at TEXT, question_text TEXT, "
                "reply_available_at TEXT, reply_eligible INTEGER, "
                "question_eligible INTEGER, source_file_hash TEXT)"
            )
            source_hash = "a" * 64
            rows = [
                ("old", "000001", "2020-01-03T00:00:00.000000+08:00", "政策进展如何",
                 "2020-01-04T00:00:00.000000+08:00", 1, 1, source_hash),
                ("new", "000001", "2020-01-05T00:00:00.000000+08:00", "政策进展如何",
                 "2020-01-06T00:00:00.000000+08:00", 1, 1, source_hash),
                ("future", "000001", "2020-01-07T00:00:00.000000+08:00", "政策进展如何",
                 "2020-01-23T00:00:00.000000+08:00", 1, 1, source_hash),
                ("finance", "000001", "2020-01-08T00:00:00.000000+08:00", "融资情况如何",
                 "2020-01-09T00:00:00.000000+08:00", 1, 1, source_hash),
                ("unanswered", "000001", "2020-01-10T00:00:00.000000+08:00", "疫情影响如何",
                 None, 0, 1, source_hash),
                ("other-company", "000002", "2020-01-08T00:00:00.000000+08:00", "政策如何",
                 "2020-01-09T00:00:00.000000+08:00", 1, 1, source_hash),
            ]
            connection.executemany("INSERT INTO qa_record VALUES (?,?,?,?,?,?,?,?)", rows)
            connection.commit()
            connection.close()
            base = {"qa_database": "qa.sqlite", "qa_source_sha256": source_hash,
                    "question_window_start": "2020-01-01",
                    "snapshot_as_of": "2020-01-22T23:59:59.999999+08:00"}
            with patch.object(target, "BASE_SNAPSHOT", root / "base.json"):
                pools = target._candidate_pairs(base, {"000001"})

        self.assertEqual(pools["policy"]["000001"]["qa_id"], "new")
        self.assertEqual(pools["liquidity"]["000001"]["qa_id"], "finance")
        self.assertFalse(pools["pandemic"])
        self.assertFalse(pools["governance"])

    def test_topic_quotas_select_unique_companies_deterministically(self):
        def pair(code):
            return {"stock_code": code, "qa_id": code,
                    "question_available_at": "2020-01-01T00:00:00+08:00",
                    "reply_available_at": "2020-01-02T00:00:00+08:00"}

        pools = {
            "pandemic": {"000001": pair("000001")},
            "policy": {"000001": pair("000001"), "000002": pair("000002")},
            "liquidity": {"000002": pair("000002"), "000003": pair("000003")},
            "governance": {"000004": pair("000004")},
            "earnings": {"000005": pair("000005")},
            "general": {"000006": pair("000006")},
        }
        quotas = {name: 1 for name in target.TOPIC_QUOTAS}
        first = target.select_pairs(pools, seed="fixed-test-seed", quotas=quotas)
        second = target.select_pairs(pools, seed="fixed-test-seed", quotas=quotas)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 6)
        self.assertEqual(len({row["stock_code"] for row in first}), 6)
        self.assertEqual([row["question_topic"] for row in first], list(quotas))

    def test_membership_cannot_be_created_before_prompt_freezes(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(target, "PROMPT", Path(directory) / "not-frozen.md"):
                with self.assertRaisesRegex(FileNotFoundError, "freeze"):
                    target.build_configs()


if __name__ == "__main__":
    unittest.main()
