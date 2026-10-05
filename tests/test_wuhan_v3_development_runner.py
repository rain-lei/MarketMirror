import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic import run_wuhan_v3_development as runner


class WuhanV3DevelopmentRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.outputs = self.root / "research_outputs"
        self.outputs.mkdir()
        self.pack = self.outputs / "pack"
        self.pack.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_saved_key_runs_and_reports_development_score_without_signal_eligibility(self):
        output = self.outputs / "run"
        score_result = {
            "model": {"event_detection": {"f1": 0.72},
                      "event_type_macro_f1_supported": 0.64},
            "keyword": {"event_detection": {"f1": 0.33}},
            "development_targets": {"met": True, "agent_signal_eligible": False},
        }
        prompt = unittest.mock.Mock(return_value="must-not-be-used")
        with patch.object(runner, "OUTPUTS_ROOT", self.outputs), \
             patch.object(runner, "load_api_key", return_value="fixture-saved-key"), \
             patch.object(runner, "save_api_key") as save_key, \
             patch.object(runner, "verify_pack", return_value=({"item": {}}, {
                 "experiment_id": "pack", "derived_protocol": {
                     "source_pack_directory": str(runner.DEVELOPMENT_SOURCE_DIR)}})), \
             patch.object(runner, "run_model", return_value={
                 "model_rows": 1, "request_failures": 0}) as model, \
             patch.object(runner, "normalize_v3", return_value={"parse_errors": 0}), \
             patch.object(runner, "audit_v3", return_value={"reply_only_evidence": True}), \
             patch.object(runner, "score", return_value=score_result):
            output_capture = io.StringIO()
            with contextlib.redirect_stdout(output_capture):
                result = runner.run_pipeline(
                    self.pack, output, credential_path=self.root / ".env.local",
                    api_key_provider=prompt)

        prompt.assert_not_called()
        save_key.assert_not_called()
        self.assertEqual(model.call_args.kwargs["api_key"], "fixture-saved-key")
        self.assertEqual(result["model_f1"], 0.72)
        self.assertEqual(result["keyword_f1"], 0.33)
        self.assertFalse(result["agent_signal_eligible"])
        self.assertNotIn("fixture-saved-key", output_capture.getvalue())

    def test_missing_key_is_masked_saved_and_not_echoed(self):
        output = self.outputs / "run"
        score_result = {
            "model": {"event_detection": {"f1": 0.2},
                      "event_type_macro_f1_supported": 0.1},
            "keyword": {"event_detection": {"f1": 0.3}},
            "development_targets": {"met": False, "agent_signal_eligible": False},
        }
        with patch.object(runner, "OUTPUTS_ROOT", self.outputs), \
             patch.object(runner, "load_api_key", side_effect=FileNotFoundError), \
             patch.object(runner, "save_api_key") as save_key, \
             patch.object(runner, "verify_pack", return_value=({"item": {}}, {
                 "experiment_id": "pack", "derived_protocol": {
                     "source_pack_directory": str(runner.DEVELOPMENT_SOURCE_DIR)}})), \
             patch.object(runner, "run_model", return_value={
                 "model_rows": 1, "request_failures": 0}) as model, \
             patch.object(runner, "normalize_v3", return_value={"parse_errors": 0}), \
             patch.object(runner, "audit_v3", return_value={"reply_only_evidence": True}), \
             patch.object(runner, "score", return_value=score_result):
            output_capture = io.StringIO()
            with contextlib.redirect_stdout(output_capture):
                result = runner.run_pipeline(
                    self.pack, output, credential_path=self.root / ".env.local",
                    api_key_provider=lambda _: "fixture-new-key")

        save_key.assert_called_once_with("fixture-new-key", self.root / ".env.local")
        self.assertEqual(model.call_args.kwargs["api_key"], "fixture-new-key")
        self.assertFalse(result["development_targets_met"])
        self.assertNotIn("fixture-new-key", output_capture.getvalue())

    def test_output_outside_research_outputs_is_rejected_before_key_load(self):
        output = self.root / "outside"
        with patch.object(runner, "OUTPUTS_ROOT", self.outputs), \
             patch.object(runner, "load_api_key") as load_key:
            with self.assertRaisesRegex(ValueError, "research_outputs"):
                runner.run_pipeline(self.pack, output)
        load_key.assert_not_called()

    def test_existing_output_is_preserved_and_key_is_not_requested(self):
        output = self.outputs / "existing"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        prompt = unittest.mock.Mock(return_value="fixture-key")
        with patch.object(runner, "OUTPUTS_ROOT", self.outputs), \
             patch.object(runner, "load_api_key", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(ValueError, "already contains data"):
                runner.run_pipeline(self.pack, output,
                                    credential_path=self.root / ".env.local",
                                    api_key_provider=prompt)
        prompt.assert_not_called()
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_non_development_cohort_is_rejected_before_key_load(self):
        output = self.outputs / "run"
        unrelated_pack = self.outputs / "unrelated_pack"
        unrelated_pack.mkdir()
        with patch.object(runner, "OUTPUTS_ROOT", self.outputs), \
             patch.object(runner, "verify_pack", return_value=({"item": {}}, {
                 "experiment_id": "pack", "derived_protocol": {
                     "source_pack_directory": str(unrelated_pack)}})), \
             patch.object(runner, "load_api_key") as load_key:
            with self.assertRaisesRegex(ValueError, "development cohort"):
                runner.run_pipeline(self.pack, output)
        load_key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
