import unittest
from datetime import date

from research.data_pipeline.audit_financial_quarter_assignment import (
    inferred_reply_bucket, summarize,
)


class FinancialQuarterAssignmentTest(unittest.TestCase):
    def test_observed_boundaries_are_not_calendar_quarter_ends(self):
        self.assertEqual(inferred_reply_bucket(date(2020, 3, 30)), "1")
        self.assertEqual(inferred_reply_bucket(date(2020, 3, 31)), "2")
        self.assertEqual(inferred_reply_bucket(date(2020, 6, 30)), "3")
        self.assertEqual(inferred_reply_bucket(date(2020, 9, 30)), "4")
        self.assertEqual(inferred_reply_bucket(date(2021, 3, 2)), "4")

    def test_summary_separates_question_reply_and_empirical_bucket(self):
        rows = [
            ("2", "2020-03-01 10:00:00", "2020-03-31"),
            ("4", "2020-01-06 08:59:45", "2021-03-02"),
            ("1", "2020-03-30 12:00:00", "2020-03-30"),
        ]
        result = summarize(rows)
        self.assertEqual(result["source_rows"], 3)
        self.assertEqual(result["source_matches_inferred_reply_bucket"], 3)
        self.assertEqual(result["source_matches_question_calendar_quarter"], 1)
        self.assertEqual(result["source_matches_2020_reply_calendar_quarter"], 1)
        self.assertEqual(result["reply_year_counts"], {"2020": 2, "2021": 1})

    def test_source_mismatch_remains_visible_and_invalid_dates_fail(self):
        result = summarize([("1", "2020-03-01", "2020-03-31")])
        self.assertEqual(result["inferred_rule_mismatches"], 1)
        with self.assertRaisesRegex(ValueError, "later than reply"):
            summarize([("1", "2020-04-01", "2020-03-30")])
        with self.assertRaises(ValueError):
            summarize([("5", "2020-03-01", "2020-03-30")])


if __name__ == "__main__":
    unittest.main()
