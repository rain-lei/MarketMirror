import unittest

from research.semantic.analyze_wuhan_fresh_holdout_errors import profile_errors


def event(event_type):
    return {"event_type": event_type}


class WuhanFreshErrorAnalysisTest(unittest.TestCase):
    def test_profile_exposes_type_collapse_without_source_text(self):
        labels = [
            {"item_id": "earnings", "status": "labeled", "events": [event("earnings")],
             "notes": "private source text must not be copied"},
            {"item_id": "negative", "status": "labeled", "events": [],
             "notes": "private source text must not be copied"},
            {"item_id": "liquidity", "status": "labeled", "events": [event("liquidity")]},
        ]
        predictions = [
            {"item_id": "earnings", "events": [event("other")]},
            {"item_id": "negative", "events": [event("other")]},
            {"item_id": "liquidity", "events": [event("liquidity")]},
        ]

        profile = profile_errors(labels, predictions)

        self.assertEqual(profile["event_detection"]["tp"], 2)
        self.assertEqual(profile["event_detection"]["fp"], 1)
        self.assertEqual(profile["category_by_type"]["earnings"]["fn"], 1)
        self.assertEqual(profile["category_by_type"]["other"]["fp"], 2)
        matrix = profile["single_event_item_confusion"]
        self.assertEqual(matrix["items_with_one_reference_and_one_prediction"], 2)
        self.assertEqual(matrix["rows_reference_columns_prediction"]["earnings"]["other"], 1)
        self.assertNotIn("private source text", repr(profile))
        self.assertTrue(any(row["error"] == "spurious_event" for row in profile["error_items"]))

    def test_profile_rejects_mismatched_item_ids(self):
        with self.assertRaisesRegex(ValueError, "item IDs must match exactly"):
            profile_errors(
                [{"item_id": "gold", "status": "labeled", "events": []}],
                [{"item_id": "other", "events": []}],
            )


if __name__ == "__main__":
    unittest.main()
