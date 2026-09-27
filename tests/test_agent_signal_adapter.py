import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.semantic.agent_signal_adapter import (
    GATE_PIPELINE,
    GATE_SCOPE,
    build_signal_rows,
    prediction_to_signal,
    run_adapter,
)
from research.semantic.annotation_pack import VERSION as PACK_VERSION, canonical_hash
from test_assistant_review import fixture
from research.semantic.assistant_review import finalize_review
from research.semantic.compare_holdout import compare_holdout
from research.semantic.parse_model_outputs import normalize_file


def jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def event(direction="positive", uncertainty=0.2, intensity=0.8):
    return {"event_type": "earnings", "direction": direction, "affected_industries": [],
            "horizon": "short", "intensity": intensity, "uncertainty": uncertainty,
            "evidence_spans": [{"source": "question", "start": 0, "end": 4, "quote": "利润增长"}]}


class AgentSignalAdapterTest(unittest.TestCase):
    def test_prediction_mapping_is_bounded_and_explicit(self):
        prediction = {"parse_error": None, "events": [event(), event("negative", 0.0, 1.0)]}
        signal, uncertainty, count = prediction_to_signal(prediction)
        self.assertAlmostEqual(signal, -0.18)
        self.assertAlmostEqual(uncertainty, 0.1)
        self.assertEqual(count, 2)
        self.assertEqual(prediction_to_signal({"parse_error": "bad", "events": []}), (0.0, 1.0, 0))
        self.assertEqual(prediction_to_signal({"parse_error": None, "events": []}), (0.0, 0.0, 0))

    def test_adapter_requires_passed_gate_and_writes_bound_stream(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_dir, comparison_dir = root / "pack", root / "comparison"
            pack_dir.mkdir(); comparison_dir.mkdir()
            segments = [{"source": "question", "text": "利润增长"}]
            items = []
            for number in (1, 2):
                items.append({"item_id": f"{number:064x}", "source_text_sha256": canonical_hash(segments),
                              "segments": segments, "stage": "question", "stock_code": f"{number:06d}",
                              "available_at": f"2020-01-0{number}T00:00:00+08:00"})
            items_path = pack_dir / "annotation_items.jsonl"
            jsonl(items_path, items)
            pack_manifest = {"pipeline_version": PACK_VERSION, "experiment_id": "adapter-pack",
                             "counts": {"items": 2}, "config": {},
                             "artifacts": {"annotation_items.jsonl": {"sha256": file_sha256(items_path)}}}
            (pack_dir / "annotation_manifest.json").write_text(json.dumps(pack_manifest), encoding="utf-8")
            predictions = root / "predictions.jsonl"
            jsonl(predictions, [{"item_id": items[0]["item_id"], "source_text_sha256": items[0]["source_text_sha256"],
                                "model_id": "model", "prompt_version": "prompt", "events": [event()], "parse_error": None},
                               {"item_id": items[1]["item_id"], "source_text_sha256": items[1]["source_text_sha256"],
                                "model_id": "model", "prompt_version": "prompt", "events": [], "parse_error": None}])
            result = {"experiment_id": "adapter-pack", "research_signal_gate": {
                "passed": True, "checks": {"complete_independent_review": True},
                "scope": "Eligibility for a controlled Agent signal ablation only; not real investor calibration, causal historical reproduction, or regulatory forecasting."}}
            result_path = comparison_dir / "holdout_comparison.json"
            result_path.write_text(json.dumps(result), encoding="utf-8")
            (comparison_dir / "comparison_manifest.json").write_text(json.dumps({
                "pipeline_version": GATE_PIPELINE,
                "code_sha256": {"compare_holdout.py": file_sha256(
                    Path(__file__).parents[1] / "research/semantic/compare_holdout.py")},
                "artifacts": {result_path.name: {"sha256": file_sha256(result_path)}}}), encoding="utf-8")
            rows, audit = build_signal_rows(pack_dir, predictions, comparison_dir)
            self.assertEqual(audit["items"], 2)
            self.assertEqual(rows[0]["text_signal"], 0.0)
            self.assertEqual(audit["suppressed_question_event_items"], 1)
            self.assertEqual(audit["suppressed_question_events"], 1)
            self.assertEqual(rows[1]["uncertainty"], 0.0)
            output = root / "output"
            run_adapter(pack_dir, predictions, comparison_dir, output)
            self.assertEqual(len((output / "agent_signal_rows.jsonl").read_text(encoding="utf-8").splitlines()), 2)
            self.assertIn("Controlled Agent signal", (output / "agent_signal_result.json").read_text(encoding="utf-8"))

    def test_adapter_rejects_failed_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / "pack"; comparison = root / "comparison"
            pack.mkdir(); comparison.mkdir()
            items_path = pack / "annotation_items.jsonl"
            items_path.write_text("", encoding="utf-8")
            (pack / "annotation_manifest.json").write_text(json.dumps({
                "pipeline_version": PACK_VERSION, "experiment_id": "x", "counts": {"items": 0},
                "config": {}, "artifacts": {"annotation_items.jsonl": {"sha256": file_sha256(items_path)}}}), encoding="utf-8")
            result_path = comparison / "holdout_comparison.json"
            result_path.write_text(json.dumps({"experiment_id": "x", "research_signal_gate": {
                "passed": False, "checks": {"complete_independent_review": False}, "scope": GATE_SCOPE}}), encoding="utf-8")
            (comparison / "comparison_manifest.json").write_text(json.dumps({"pipeline_version": GATE_PIPELINE,
                "code_sha256": {"compare_holdout.py": file_sha256(
                    Path(__file__).parents[1] / "research/semantic/compare_holdout.py")},
                "artifacts": {result_path.name: {"sha256": file_sha256(result_path)}}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "has not passed"):
                build_signal_rows(pack, root / "predictions.jsonl", comparison)

    def test_reply_event_with_only_question_evidence_is_suppressed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, decisions = fixture(root)
            raw_path = root / "raw/model_raw_outputs.jsonl"
            raw_rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
            response = json.loads(raw_rows[1]["raw_response"])
            response["events"][0]["evidence_quotes"] = [{"source": "question", "quote": "盈利是否增加？"}]
            raw_rows[1]["raw_response"] = json.dumps(response, ensure_ascii=False)
            jsonl(raw_path, raw_rows)
            manifest_path = root / "raw/model_run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][raw_path.name]["sha256"] = file_sha256(raw_path)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            normalize_file(root / "pack", raw_path, root / "question_evidence_normalized")
            finalize_review(root / "pack", decisions, root / "review")
            compare_holdout(root / "pack", None, None, root / "raw", root / "question_evidence_normalized",
                            root / "keyword", root / "scored", ai_review_dir=root / "review")
            rows, audit = build_signal_rows(root / "pack",
                                           root / "question_evidence_normalized/model_predictions.jsonl",
                                           root / "scored")
            self.assertEqual(audit["suppressed_reply_ungrounded_events"], 1)
            self.assertEqual(rows[1]["stage"], "reply")
            self.assertEqual((rows[1]["text_signal"], rows[1]["uncertainty"], rows[1]["event_count"]),
                             (0.0, 0.0, 0))


if __name__ == "__main__":
    unittest.main()
