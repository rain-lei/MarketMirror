import json
import tempfile
import unittest
from pathlib import Path

from research.baselines.prediction_panel import Question, text_window
from research.baselines.visibility_lag import delayed_questions, load_sensitivity_config


class VisibilityLagTest(unittest.TestCase):
    def test_extra_delay_excludes_future_question_and_reply_independently(self):
        question = Question("2020-01-03T14:00:00.000000+08:00", "政策风险",
                            "2020-01-04T00:00:00.000000+08:00", "利润", True)
        source = {"000001": [question]}
        zero = delayed_questions(source, 0)["000001"]
        one = delayed_questions(source, 1)["000001"]
        start = "2020-01-01T00:00:00.000000+08:00"
        day_three = "2020-01-03T15:00:00.000000+08:00"
        day_four = "2020-01-04T15:00:00.000000+08:00"
        self.assertEqual(zero, [question])
        self.assertEqual(text_window(zero, start, day_three, "000001")["question_count"], 1)
        self.assertEqual(text_window(one, start, day_three, "000001")["question_count"], 0)
        self.assertEqual(text_window(zero, start, day_four, "000001")["known_reply_count"], 1)
        self.assertEqual(text_window(one, start, day_four, "000001")["question_count"], 1)
        self.assertEqual(text_window(one, start, day_four, "000001")["known_reply_count"], 0)
        self.assertEqual(source["000001"], [question])

    def test_delay_grid_is_fixed_before_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            source = {"run_id": "text_publication_delay_sensitivity_2020",
                      "source_run_id": "text_prediction", "lag_days": [0, 1, 3, 7],
                      "lag_unit": "calendar_days", "interpretation": "Illustrative delay"}
            path.write_text(json.dumps(source), encoding="utf-8")
            self.assertEqual(load_sensitivity_config(path)["lag_days"], [0, 1, 3, 7])
            source["lag_days"] = [0, 1, 2, 7]
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fixed"):
                load_sensitivity_config(path)


if __name__ == "__main__":
    unittest.main()
