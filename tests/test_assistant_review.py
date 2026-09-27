import copy
import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import VERSION as PACK_VERSION, canonical_hash
from research.semantic.assistant_review import finalize_review, load_review, validate_review
from research.semantic.compare_holdout import compare_holdout
from research.semantic.agent_signal_adapter import run_adapter
from research.semantic.parse_model_outputs import normalize_file
from research.semantic.prompt_contract import prompt_path
from research.simulation.semantic_signal_join import load_verified_signal_stream


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def fixture(root):
    pack, raw, keyword = [root / name for name in ("pack", "raw", "keyword")]
    for directory in (pack, raw, keyword):
        directory.mkdir()
    segments = [{"source": "question", "text": "盈利是否增加？"}, {"source": "reply", "text": "利润增长。"}]
    samples = [{"item_id": f"{i:064x}", "source_text_sha256": canonical_hash(segments[:i]),
                "segments": segments[:i], "stage": "question" if i == 1 else "reply",
                "stock_code": f"{i:06d}", "available_at": "2020-07-01T10:00:00+08:00"}
               for i in (1, 2)]
    event = {"event_type": "earnings", "direction": "positive", "affected_industries": [],
             "horizon": "unknown", "intensity": 0.5, "uncertainty": 0.2,
             "evidence_spans": [{"source": "reply", "start": 0, "end": 4, "quote": "利润增长"}]}
    labels = [{"item_id": sample["item_id"], "source_text_sha256": sample["source_text_sha256"],
               "annotator_id": "ai:test-reviewer", "status": "labeled", "events": [] if i == 0 else [event],
               "notes": "尚未收到公司回复，不能把提问当作确认的事实。" if i == 0 else "回复明确确认利润增长，标经营正向，未来影响期限未知。"}
              for i, sample in enumerate(samples)]
    decisions = root / "decisions.jsonl"
    jsonl(decisions, labels)
    jsonl(pack / "annotation_items.jsonl", samples)
    model_id, prompt_version = "DeepSeek-V4-Flash-0731-W8A8", "semantic-prompt-v2"
    dump(pack / "annotation_manifest.json", {"pipeline_version": PACK_VERSION, "experiment_id": "ai-test",
         "counts": {"items": 2}, "config": {"frozen_model_id": model_id, "frozen_prompt_version": prompt_version,
         "frozen_prompt_sha256": file_sha256(prompt_path(prompt_version))},
         "artifacts": {"annotation_items.jsonl": {"sha256": file_sha256(pack / "annotation_items.jsonl")}}})
    prompt_event = {key: val for key, val in event.items() if key != "evidence_spans"}
    prompt_event["evidence_quotes"] = [{"source": "reply", "quote": "利润增长"}]
    raw_rows = [{"item_id": sample["item_id"], "source_text_sha256": sample["source_text_sha256"],
                 "model_id": model_id, "prompt_version": prompt_version,
                 "raw_response": json.dumps({"events": [] if i == 0 else [prompt_event]}, ensure_ascii=False)}
                for i, sample in enumerate(samples)]
    jsonl(raw / "model_raw_outputs.jsonl", raw_rows)
    inputs = {"annotation_manifest": file_sha256(pack / "annotation_manifest.json"),
              "annotation_items": file_sha256(pack / "annotation_items.jsonl")}
    dump(raw / "model_run_manifest.json", {"pack_experiment_id": "ai-test", "model_id": model_id,
         "prompt_version": prompt_version, "provider_base_url": "http://aigw.dlut.edu.cn/v1", "temperature": 0,
         "requested_rows": 2, "model_rows": 2, "remaining_rows": 0, "request_failures": 0,
         "input_sha256": {**inputs, "prompt": file_sha256(prompt_path(prompt_version))},
         "artifacts": {"model_raw_outputs.jsonl": {"sha256": file_sha256(raw / "model_raw_outputs.jsonl")}}})
    normalize_file(pack, raw / "model_raw_outputs.jsonl", root / "normalized")
    jsonl(keyword / "keyword_predictions.jsonl", [{"item_id": row["item_id"], "source_text_sha256": row["source_text_sha256"],
          "model_id": "keyword", "prompt_version": "baseline", "events": [], "parse_error": None} for row in labels])
    dump(keyword / "keyword_manifest.json", {"pack_experiment_id": "ai-test", "input_sha256": {
         "annotation_manifest.json": inputs["annotation_manifest"], "annotation_items.jsonl": inputs["annotation_items"]},
         "artifacts": {"keyword_predictions.jsonl": {"sha256": file_sha256(keyword / "keyword_predictions.jsonl")}}})
    return samples, labels, decisions


class AssistantReviewTest(unittest.TestCase):
    def test_all_items_rationales_and_ai_identity_are_required(self):
        with tempfile.TemporaryDirectory() as temp:
            samples, labels, _ = fixture(Path(temp))
            items = {x["item_id"]: x for x in samples}
            for bad in (labels[:1], [{**x, "notes": "auto accept"} for x in labels],
                        [{**x, "annotator_id": "human"} for x in labels]):
                with self.assertRaises(ValueError):
                    validate_review(items, bad)
            bad = copy.deepcopy(labels)
            bad[1]["events"][0]["evidence_spans"][0].update(end=400, quote="利润增长。")
            with self.assertRaisesRegex(ValueError, "bounded reply"):
                validate_review(items, bad)

    def test_ai_reference_to_scoring_to_signal_without_dual_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, labels, decisions = fixture(root)
            review = finalize_review(root / "pack", decisions, root / "review")
            self.assertFalse(review["independent_reference"])
            self.assertFalse(review["gold_ready"])
            self.assertEqual(load_review(root / "pack", root / "review")[0], labels)
            score = compare_holdout(root / "pack", None, None, root / "raw", root / "normalized",
                                    root / "keyword", root / "scored", ai_review_dir=root / "review")
            self.assertEqual(score["model"]["event_detection"]["f1"], 1.0)
            self.assertTrue(score["research_signal_gate"]["passed"])
            self.assertIn("complete_assistant_review", score["research_signal_gate"]["checks"])
            self.assertNotIn("complete_independent_review", score["research_signal_gate"]["checks"])
            predictions = root / "normalized/model_predictions.jsonl"
            result = run_adapter(root / "pack", predictions, root / "scored", root / "signals")
            self.assertEqual(result["gate"]["review_basis"]["protocol"], "assistant_review_v1")
            self.assertEqual(len(load_verified_signal_stream(root / "signals")[0]), 2)
            altered = root / "different_predictions.jsonl"
            altered.write_text(predictions.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differ from the scored"):
                run_adapter(root / "pack", altered, root / "scored", root / "bad_signals")
            reference = root / "review/reference_labels.jsonl"
            reference.write_text(reference.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact has changed"):
                load_review(root / "pack", root / "review")


if __name__ == "__main__":
    unittest.main()
