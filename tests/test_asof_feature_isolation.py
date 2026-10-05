import unittest

from research.data_pipeline.audit_asof_feature_isolation import (
    allowed_columns, check_rows,
)


class AsofFeatureIsolationTest(unittest.TestCase):
    def test_allowlist_has_only_asof_text_counts_and_lengths(self):
        columns = allowed_columns()
        self.assertEqual(len(columns), 37)
        self.assertNotIn("source_quarter_hint", columns)
        self.assertNotIn("snapshot_id", columns)
        self.assertNotIn("financial_snapshot", columns)
        self.assertNotIn("reply_available_at", columns)

    def test_independent_counts_reject_future_reply_and_wrong_cutoff(self):
        row = {"stock_code": "000001", "window_start": "2020-01-01", "as_of": "2020-01-23",
               "question_count": "2", "known_reply_count": "1", "no_visible_reply_count": "1",
               "known_reply_share": "0.5"}
        expected = {"000001": (2, 1)}
        self.assertEqual(check_rows([row], expected, start="2020-01-01", cutoff="2020-01-23"),
                         {"companies": 1, "questions": 2, "visible_replies": 1})
        with self.assertRaisesRegex(ValueError, "counts differ"):
            check_rows([{**row, "known_reply_count": "2"}], expected,
                       start="2020-01-01", cutoff="2020-01-23")
        with self.assertRaisesRegex(ValueError, "cutoff differs"):
            check_rows([{**row, "as_of": "2020-01-24"}], expected,
                       start="2020-01-01", cutoff="2020-01-23")


if __name__ == "__main__":
    unittest.main()
