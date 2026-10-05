import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic import run_wuhan_v3_validation as runner


class WuhanV3ValidationRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.outputs = self.root / "research_outputs"
        self.outputs.mkdir()
        self.pack = self.outputs / "pack"
        self.pack.mkdir()
        self.baseline = self.outputs / "baseline"
        self.baseline.mkdir()
        self.config = self.root / "config.json"
        self.config.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_gate_failure_saves_score_and_never_creates_signals_or_portfolio(self):
        output = self.outputs / "run"
        score_result = {"model": {"event_detection": {"f1": 0.2}},
                        "keyword": {"event_detection": {"f1": 0.3}},
                        "research_signal_gate": {"passed": False}}
        with patch.object(runner, "OUTPUTS_ROOT", self.outputs), \
             patch.object(runner, "BASELINE_DIR", self.baseline), \
             patch.object(runner, "CONFIG_PATH", self.config), \
             patch.object(runner, "load_api_key", side_effect=FileNotFoundError), \
             patch.object(runner, "save_api_key") as save_key, \
             patch.object(runner, "verify_pack", return_value=({"item": {}}, {"experiment_id": "pack"})), \
             patch.object(runner, "run_model", return_value={"model_rows": 1, "request_failures": 0}) as model, \
             patch.object(runner, "normalize_v3", return_value={"parse_errors": 0}), \
             patch.object(runner, "audit_v3", return_value={"reply_only_evidence": True}), \
             patch.object(runner, "score", return_value=score_result), \
             patch.object(runner, "run_adapter") as adapter, \
             patch.object(runner, "run_ablation") as ablation:
            output_capture = io.StringIO()
            with contextlib.redirect_stdout(output_capture):
                result = runner.run_pipeline(self.pack, output,
                                             credential_path=self.root / "key.dpapi",
                                             api_key_provider=lambda _: "fixture-secret")

        self.assertFalse(result["gate_passed"])
        self.assertFalse(result["signals_created"])
        self.assertFalse(result["portfolio_created"])
        self.assertEqual(model.call_args.kwargs["api_key"], "fixture-secret")
        save_key.assert_called_once_with("fixture-secret", self.root / "key.dpapi")
        adapter.assert_not_called()
        ablation.assert_not_called()
        self.assertNotIn("fixture-secret", output_capture.getvalue())

    def test_passed_gate_runs_signal_adapter_and_paired_portfolio(self):
        output = self.outputs / "run"
        score_result = {"model": {"event_detection": {"f1": 0.8}},
                        "keyword": {"event_detection": {"f1": 0.3}},
                        "research_signal_gate": {"passed": True}}
        with patch.object(runner, "OUTPUTS_ROOT", self.outputs), \
             patch.object(runner, "BASELINE_DIR", self.baseline), \
             patch.object(runner, "CONFIG_PATH", self.config), \
             patch.object(runner, "load_api_key", side_effect=FileNotFoundError), \
             patch.object(runner, "save_api_key"), \
             patch.object(runner, "verify_pack", return_value=({"item": {}}, {"experiment_id": "pack"})), \
             patch.object(runner, "run_model", return_value={"model_rows": 1, "request_failures": 0}), \
             patch.object(runner, "normalize_v3", return_value={"parse_errors": 0}), \
             patch.object(runner, "audit_v3", return_value={"reply_only_evidence": True}), \
             patch.object(runner, "score", return_value=score_result), \
             patch.object(runner, "run_adapter", return_value={"items": 102}) as adapter, \
             patch.object(runner, "run_ablation", return_value={
                 "paired_paths": 84, "audited_portfolio_days": 5880}) as ablation:
            with contextlib.redirect_stdout(io.StringIO()):
                result = runner.run_pipeline(self.pack, output,
                                             credential_path=self.root / "key.dpapi",
                                             api_key_provider=lambda _: "fixture-secret")

        self.assertTrue(result["gate_passed"])
        self.assertTrue(result["portfolio_created"])
        self.assertEqual(result["signals_created"], 102)
        self.assertEqual(result["paired_paths"], 84)
        adapter.assert_called_once()
        ablation.assert_called_once()

    def test_rejects_existing_output_before_prompting_for_key(self):
        output = self.outputs / "existing"
        output.mkdir()
        (output / "keep.txt").write_text("existing", encoding="utf-8")
        prompt = unittest.mock.Mock(return_value="fixture-secret")
        with patch.object(runner, "OUTPUTS_ROOT", self.outputs), \
             patch.object(runner, "BASELINE_DIR", self.baseline), \
             patch.object(runner, "CONFIG_PATH", self.config), \
             patch.object(runner, "load_api_key", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(ValueError, "already contains data"):
                runner.run_pipeline(self.pack, output, api_key_provider=prompt)
        prompt.assert_not_called()

    def test_saved_key_skips_prompt_and_is_not_rewritten(self):
        output = self.outputs / "run"
        score_result = {"model": {"event_detection": {"f1": 0.2}},
                        "keyword": {"event_detection": {"f1": 0.3}},
                        "research_signal_gate": {"passed": False}}
        prompt = unittest.mock.Mock(return_value="must-not-be-used")
        with patch.object(runner, "OUTPUTS_ROOT", self.outputs), \
             patch.object(runner, "BASELINE_DIR", self.baseline), \
             patch.object(runner, "CONFIG_PATH", self.config), \
             patch.object(runner, "load_api_key", return_value="saved-fixture-key"), \
             patch.object(runner, "save_api_key") as save_key, \
             patch.object(runner, "verify_pack", return_value=({"item": {}}, {"experiment_id": "pack"})), \
             patch.object(runner, "run_model", return_value={"model_rows": 1, "request_failures": 0}) as model, \
             patch.object(runner, "normalize_v3", return_value={"parse_errors": 0}), \
             patch.object(runner, "audit_v3", return_value={"reply_only_evidence": True}), \
             patch.object(runner, "score", return_value=score_result):
            with contextlib.redirect_stdout(io.StringIO()):
                runner.run_pipeline(self.pack, output,
                                    credential_path=self.root / "key.dpapi",
                                    api_key_provider=prompt)
        prompt.assert_not_called()
        save_key.assert_not_called()
        self.assertEqual(model.call_args.kwargs["api_key"], "saved-fixture-key")


if __name__ == "__main__":
    unittest.main()
