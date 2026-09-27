import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import VERSION as PACK_VERSION, canonical_hash
from research.semantic.compare_holdout import compare_holdout, paired_company_bootstrap
from research.semantic.parse_model_outputs import normalize_file
from research.semantic.prompt_contract import prompt_path
from research.semantic.review_workflow import finalize_labels, run_comparison


def jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def item(number):
    segments = [{"source": "question", "text": f"公司{number}利润增长"}]
    return {"item_id": str(number), "source_text_sha256": canonical_hash(segments),
            "segments": segments, "stage": "question", "stock_code": str(number).zfill(6),
            "qa_id": f"qa-{number}", "split": "test", "selection_stratum": "earnings"}


def event(source):
    quote = "利润增长"
    start = source.index(quote)
    return {"event_type": "earnings", "direction": "positive", "affected_industries": [],
            "horizon": "unknown", "intensity": 0.5, "uncertainty": 0.2,
            "evidence_spans": [{"source": "question", "start": start, "end": start + len(quote), "quote": quote}]}


def label(sample, reviewer, events):
    return {"item_id": sample["item_id"], "source_text_sha256": sample["source_text_sha256"],
            "annotator_id": reviewer, "status": "labeled", "events": events, "notes": ""}


def prediction(sample, events, model_id, prompt_version):
    return {"item_id": sample["item_id"], "source_text_sha256": sample["source_text_sha256"],
            "model_id": model_id, "prompt_version": prompt_version,
            "events": events, "parse_error": None}


class CompareHoldoutTest(unittest.TestCase):
    def test_company_bootstrap_is_paired_and_deterministic(self):
        samples = [item(1), item(2)]
        items = {sample["item_id"]: sample for sample in samples}
        gold = [label(samples[0], "human", [event(samples[0]["segments"][0]["text"])]),
                label(samples[1], "human", [])]
        model = [prediction(sample, [], "model", "v2") for sample in samples]
        keyword = [prediction(samples[0], gold[0]["events"], "keyword", "baseline"),
                   prediction(samples[1], [], "keyword", "baseline")]
        first = paired_company_bootstrap(items, gold, model, keyword, replicates=100, seed=4)
        self.assertEqual(first, paired_company_bootstrap(items, gold, model, keyword,
                                                         replicates=100, seed=4))
        self.assertEqual(first["difference"], -1.0)
        self.assertEqual(first["companies"], 2)

    def test_full_provenance_chain_and_tamper_rejection(self):
        samples = [item(1), item(2)]
        model_id, prompt_version = "DeepSeek-V4-Flash-0731-W8A8", "semantic-prompt-v2"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack, raw_dir, keyword_dir = (root / name for name in ("pack", "raw", "keyword"))
            for path in (pack, raw_dir, keyword_dir):
                path.mkdir()
            items_path = pack / "annotation_items.jsonl"
            jsonl(items_path, samples)
            pack_manifest = {"pipeline_version": PACK_VERSION, "experiment_id": "synthetic-holdout",
                             "counts": {"items": len(samples)},
                             "config": {"frozen_model_id": model_id,
                                        "frozen_prompt_version": prompt_version,
                                        "frozen_prompt_sha256": file_sha256(prompt_path(prompt_version))},
                             "artifacts": {"annotation_items.jsonl": {"sha256": file_sha256(items_path)}}}
            (pack / "annotation_manifest.json").write_text(json.dumps(pack_manifest), encoding="utf-8")
            labels = [label(samples[0], "human-a", [event(samples[0]["segments"][0]["text"])]),
                      label(samples[1], "human-a", [])]
            a_path, b_path = root / "a.jsonl", root / "b.jsonl"
            jsonl(a_path, labels)
            jsonl(b_path, [{**row, "annotator_id": "human-b"} for row in labels])
            run_comparison(pack, a_path, b_path, root / "review_comparison")
            final_path = root / "final.jsonl"
            jsonl(final_path, [{**row, "annotator_id": "adjudicator"} for row in labels])
            finalize_labels(pack, root / "review_comparison", final_path, root / "gold")

            raw_rows = [{"item_id": sample["item_id"],
                         "source_text_sha256": sample["source_text_sha256"],
                         "model_id": model_id, "prompt_version": prompt_version,
                         "raw_response": '{"events":[]}'} for sample in samples]
            raw_path = raw_dir / "model_raw_outputs.jsonl"
            jsonl(raw_path, raw_rows)
            raw_manifest = {"pack_experiment_id": pack_manifest["experiment_id"],
                            "model_id": model_id, "prompt_version": prompt_version,
                            "provider_base_url": "http://aigw.dlut.edu.cn/v1",
                            "temperature": 0.0, "requested_rows": 2, "model_rows": 2,
                            "remaining_rows": 0, "request_failures": 0,
                            "input_sha256": {"annotation_manifest": file_sha256(pack / "annotation_manifest.json"),
                                             "annotation_items": file_sha256(items_path),
                                             "prompt": file_sha256(prompt_path(prompt_version))},
                            "artifacts": {"model_raw_outputs.jsonl": {"sha256": file_sha256(raw_path)}}}
            (raw_dir / "model_run_manifest.json").write_text(json.dumps(raw_manifest), encoding="utf-8")
            normalize_file(pack, raw_path, root / "normalized")

            keyword = [prediction(samples[0], labels[0]["events"], "keyword", "baseline"),
                       prediction(samples[1], [], "keyword", "baseline")]
            keyword_path = keyword_dir / "keyword_predictions.jsonl"
            jsonl(keyword_path, keyword)
            keyword_manifest = {"pack_experiment_id": pack_manifest["experiment_id"],
                                "input_sha256": {"annotation_manifest.json": file_sha256(pack / "annotation_manifest.json"),
                                                 "annotation_items.jsonl": file_sha256(items_path)},
                                "artifacts": {"keyword_predictions.jsonl": {"sha256": file_sha256(keyword_path)}}}
            (keyword_dir / "keyword_manifest.json").write_text(json.dumps(keyword_manifest), encoding="utf-8")
            result = compare_holdout(pack, root / "review_comparison", root / "gold",
                                     raw_dir, root / "normalized", keyword_dir, root / "scored")
            self.assertEqual(result["model"]["event_detection"]["f1"], 0.0)
            self.assertEqual(result["keyword"]["event_detection"]["f1"], 1.0)
            self.assertFalse(result["research_signal_gate"]["passed"])
            self.assertEqual(result["model_run_audit"]["raw"]["rows"], 2)
            (root / "normalized/model_predictions.jsonl").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs from its manifest"):
                compare_holdout(pack, root / "review_comparison", root / "gold",
                                raw_dir, root / "normalized", keyword_dir, root / "scored2")


if __name__ == "__main__":
    unittest.main()
