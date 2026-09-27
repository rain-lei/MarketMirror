import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.baselines.observed_diagnostics import (render_activity_report, render_placebo_report,
                                                     run_diagnostic)
from research.data_pipeline.provenance import file_sha256


class ObservedDiagnosticsTest(unittest.TestCase):
    def test_reports_are_event_neutral_and_do_not_pretend_ranks_are_p_values(self):
        activity = {"runs": [{"event_id": "policy-2018", "stock_code": "000001",
                              "event_date_used": "2018-05-02", "estimation_sessions": 120,
                              "event_day_volume_fold": 0.87, "event_day_amount_fold": 0.78,
                              "window_max_amount_fold": 1.78}]}
        prose = render_activity_report(activity)
        self.assertIn("2018-05-02", prose)
        self.assertIn("0.780", prose)
        self.assertNotIn("武汉", prose)
        placebo = {"diagnostic": {"candidate_start_date": "2017-09-01",
                                  "candidate_end_date": "2018-12-31", "blackout_start": "2018-04-25",
                                  "blackout_end": "2018-05-09",
                                  "excluded_or_retained_dates": {"before_actual_event": 2, "after_actual_event": 0}},
                   "comparisons": [
                       {"event_id": "policy-2018", "stock_code_or_mean": "000001",
                        "regime": regime, "actual_car": -0.09,
                        "absolute_rank_fraction": 0.5 if regime == "before_actual_event" else None,
                        "candidate_dates": 2 if regime == "before_actual_event" else 0}
                       for regime in ("before_actual_event", "after_actual_event")]}
        prose = render_placebo_report(placebo)
        self.assertIn("无可比日期", prose)
        self.assertIn("不是 p 值", prose)
        self.assertNotIn("武汉", prose)

    def test_adapter_discards_old_event_specific_prose_but_preserves_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text("{}", encoding="utf-8")
            result = {"analysis_id": "synthetic-id", "runs": [{
                "event_id": "policy-2018", "stock_code": "000001", "event_date_used": "2018-05-02",
                "estimation_sessions": 120, "event_day_volume_fold": 0.87,
                "event_day_amount_fold": 0.78, "window_max_amount_fold": 1.78}]}
            def fake_runner(_config, staging):
                (staging / "event_activity.json").write_text(json.dumps(result), encoding="utf-8")
                (staging / "event_activity.csv").write_text("trade_date,value\n2018-05-02,7\n", encoding="utf-8")
                (staging / "event_activity_report.md").write_text("旧文案：武汉", encoding="utf-8")
                manifest = {"pipeline_version": "event-activity-v1",
                            "input_sha256": {str(config.resolve()): file_sha256(config)},
                            "code_sha256": {"old_runner.py": "test"},
                            "artifacts": {path.name: {"sha256": file_sha256(path)}
                                          for path in staging.iterdir()}}
                (staging / "activity_event_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                return result
            with patch("research.baselines.observed_diagnostics.run_activity_event", fake_runner):
                manifest = run_diagnostic("activity", config, root / "output")
            self.assertEqual(manifest["numerical_run_id"], "synthetic-id")
            self.assertEqual((root / "output/event_activity.csv").read_text(encoding="utf-8"),
                             "trade_date,value\n2018-05-02,7\n")
            self.assertNotIn("武汉", (root / "output/event_activity_report.md").read_text(encoding="utf-8"))
            for name, info in manifest["artifacts"].items():
                self.assertEqual(file_sha256(root / "output" / name), info["sha256"])
            with self.assertRaisesRegex(ValueError, "new empty"):
                run_diagnostic("activity", config, root / "output")


if __name__ == "__main__":
    unittest.main()
