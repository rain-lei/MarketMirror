import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.data_pipeline.provenance import file_sha256
from research.semantic.compare_model_runs import compare_rows, compare_runs
from research.semantic.parse_model_outputs import normalize_file
from research.semantic.run_model import run_model


class ProtocolComparisonTest(unittest.TestCase):
    def test_complete_source_bound_comparison_and_rewritten_normalized_output_rejection(self):
        items = {str(index): {"item_id": str(index), "source_text_sha256": str(index) * 64,
                             "segments": [{"source": "question", "text": "公司收入增长"}],
                             "stage": "question", "split": "train", "selection_stratum": "earnings"}
                 for index in (1, 2)}
        event = {"event_type": "earnings", "direction": "positive", "affected_industries": [],
                 "horizon": "unknown", "intensity": 0.5, "uncertainty": 0.5}
        old = {**event, "evidence_spans": [{"source": "question", "start": 0, "end": 4, "quote": "收入增长"}]}
        new = {**event, "evidence_quotes": [{"source": "question", "quote": "收入增长"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            (pack / "annotation_manifest.json").write_text("{}", encoding="utf-8")
            (pack / "annotation_items.jsonl").write_text("{}\n", encoding="utf-8")
            fixture = (items, {"experiment_id": "fixture"})
            with patch("research.semantic.run_model.load_pack", return_value=fixture), \
                 patch("research.semantic.parse_model_outputs.load_pack", return_value=fixture), \
                 patch("research.semantic.audit_model_run.load_pack", return_value=fixture), \
                 patch("research.semantic.compare_model_runs.load_pack", return_value=fixture):
                for label, version, candidate_event in (("old", "semantic-prompt-v1", old),
                                                         ("new", "semantic-prompt-v2", new)):
                    with patch("research.semantic.run_model.request_completion", side_effect=[
                            '{"events":[]}', json.dumps({"events": [candidate_event]})]):
                        run_model(pack, root / label, "secret", prompt_version=version)
                    normalize_file(pack, root / label / "model_raw_outputs.jsonl", root / f"{label}_norm")
                result = compare_runs(pack, root / "old", root / "old_norm", root / "new",
                                      root / "new_norm", root / "comparison")
                self.assertEqual(result["transitions"], {"empty_to_empty": 1, "failed_to_event": 1})
                self.assertEqual(result["candidate"]["coverage"]["valid_event_rows"], 1)
                self.assertFalse(result["accuracy_claim_allowed"])
                self.assertNotIn("公司收入增长", (root / "comparison/comparison.json").read_text(encoding="utf-8"))
                # Rewriting both output and its declared hash must not fool an audit.
                prediction_path = root / "new_norm/model_predictions.jsonl"
                rows = [json.loads(line) for line in prediction_path.read_text(encoding="utf-8").splitlines()]
                rows[1]["events"] = []
                prediction_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
                manifest_path = root / "new_norm/normalization_manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["artifacts"][prediction_path.name]["sha256"] = file_sha256(prediction_path)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "deterministic normalization"):
                    compare_runs(pack, root / "old", root / "old_norm", root / "new",
                                 root / "new_norm", root / "tampered_comparison")

    def test_missing_failed_and_empty_are_separate_transition_states(self):
        items = {"1": {"source_text_sha256": "a" * 64}, "2": {"source_text_sha256": "b" * 64}}
        row = {"item_id": "1", "source_text_sha256": "a" * 64, "model_id": "fixture",
               "prompt_version": "prompt", "parse_error": None, "events": []}
        failed = {**row, "parse_error": "invalid"}
        self.assertEqual(compare_rows(items, [failed], [row]), {"failed_to_empty": 1, "missing_to_missing": 1})


if __name__ == "__main__":
    unittest.main()
