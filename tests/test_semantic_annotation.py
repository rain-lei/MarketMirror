import copy
import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from research.data_pipeline.aggregate_qa_features import KEYWORDS
from research.data_pipeline.build_dataset import build_dataset
from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import (build_annotation_pack, canonical_hash, load_config, sample_candidates,
                                               split_for_company, stratum)
from research.semantic.signal_validation import (evaluate, evaluate_files, load_pack, read_jsonl, validate_event,
                                                 validate_labels, validate_predictions)
from research.semantic.keyword_baseline import predict_item, run_baseline
from research.semantic.parse_model_outputs import normalize_rows, normalize_file


def base_config():
    return {"run_id": "synthetic_semantic_fixture", "qa_database": "dataset/dataset.sqlite",
            "qa_source_sha256": "a" * 64,
            "visibility_window": {"start_date": "2020-01-01", "end_date": "2020-06-30"},
            "seed": "semantic-unit-test", "company_split_percent": {"train": 60, "validation": 20, "test": 20},
            "per_stage_stratum_quota": {"train": 1, "validation": 1, "test": 1},
            "selection_basis": "Synthetic complete cell fixture, not population prevalence"}


def synthetic_rows(config):
    codes = {name: [] for name in ("train", "validation", "test")}
    for number in range(1, 10000):
        code = str(number).zfill(6)
        split = split_for_company(code, config["seed"], config["company_split_percent"])
        if len(codes[split]) < 8:
            codes[split].append(code)
        if all(len(value) == 8 for value in codes.values()):
            break
    rows = []
    for split, selected in codes.items():
        for category, code in zip((*KEYWORDS, "none"), selected):
            term = KEYWORDS[category][0] if category != "none" else "日常经营"
            qa_id = f"{split}|{code}|{category}"
            rows.append((qa_id, code, "2020-01-10T10:00:00.000000+08:00", f"请说明{term}事项{code}",
                         "2020-01-11T00:00:00.000000+08:00", f"公司说明{term}事项{code}", 1))
    return rows


def item(stage="question"):
    segments = [{"source": "question", "text": "公司称利润增长"}]
    if stage == "reply":
        segments.append({"source": "reply", "text": "该说法不属实"})
    return {"item_id": "a" * 64, "source_text_sha256": canonical_hash(segments), "segments": segments,
            "stage": stage, "stock_code": "000001", "split": "test"}


def event(source="question", text="公司称利润增长", quote="利润增长"):
    start = text.index(quote)
    return {"event_type": "earnings", "direction": "positive", "affected_industries": [], "horizon": "unknown",
            "intensity": 0.5, "uncertainty": 0.2,
            "evidence_spans": [{"source": source, "start": start, "end": start + len(quote), "quote": quote}]}


class SemanticAnnotationTest(unittest.TestCase):
    def test_company_split_stratum_and_availability_gated_sampling(self):
        config = base_config()
        rows = synthetic_rows(config)
        first, counts = sample_candidates(rows, config)
        second, _ = sample_candidates(reversed(rows), config)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 48)
        self.assertEqual(len({x["source_text_sha256"] for x in first}), 48)
        for sample in first:
            self.assertEqual(sample["split"], split_for_company(sample["stock_code"], config["seed"], config["company_split_percent"]))
            self.assertEqual([v["source"] for v in sample["segments"]],
                             ["question"] if sample["stage"] == "question" else ["question", "reply"])
            self.assertEqual(sample["source_text_sha256"], canonical_hash(sample["segments"]))
        self.assertEqual(stratum("日常经营"), "none")
        future = ("future-reply", "000001", "2020-02-01T10:00:00.000000+08:00", "政策询问",
                  "2020-07-01T00:00:00.000000+08:00", "利润增长", 1)
        unconfirmed = ("unconfirmed", "000001", "2020-02-01T10:00:00.000000+08:00", "政策询问",
                       "2020-02-02T00:00:00.000000+08:00", "利润增长", 0)
        _, changed_counts = sample_candidates(rows + [future, unconfirmed], config)
        self.assertEqual(sum(v for k, v in counts.items() if k.startswith("eligible_") and "|reply|" in k),
                         sum(v for k, v in changed_counts.items() if k.startswith("eligible_") and "|reply|" in k))

    def test_exact_visible_span_validation_rejects_future_reply_and_invalid_numbers(self):
        question = item()
        validate_event(event(), question)
        with self.assertRaisesRegex(ValueError, "source or offsets"):
            validate_event(event("reply"), question)
        wrong = event()
        wrong["evidence_spans"][0]["quote"] = "利润下降"
        with self.assertRaisesRegex(ValueError, "exactly match"):
            validate_event(wrong, question)
        wrong = event()
        wrong["intensity"] = True
        with self.assertRaisesRegex(ValueError, "finite number"):
            validate_event(wrong, question)
        validate_event(event("reply", "该说法不属实", "不属实"), item("reply"))

    def test_label_and_model_source_binding_parse_failure_and_scoring(self):
        source = item()
        items = {source["item_id"]: source}
        label = {"item_id": source["item_id"], "source_text_sha256": source["source_text_sha256"],
                 "annotator_id": "independent-reviewer-1", "status": "labeled", "events": [event()], "notes": ""}
        prediction = {"item_id": source["item_id"], "source_text_sha256": source["source_text_sha256"],
                      "model_id": "synthetic-test-model", "prompt_version": "v1", "events": [event()], "parse_error": None}
        self.assertEqual(evaluate(items, [label], [prediction])["event_detection"]["f1"], 1)
        self.assertEqual(evaluate(items, [label], [prediction])["direction_accuracy_on_matched_single"], 1)
        self.assertEqual(evaluate(items, [label], [prediction])["evidence_exact_span_recall_on_matched_single"], 1)
        invalid = copy.deepcopy(prediction)
        invalid["source_text_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "source text hash"):
            validate_predictions(items, [invalid])
        invalid = copy.deepcopy(label)
        invalid["status"] = "unlabeled"
        with self.assertRaisesRegex(ValueError, "unlabeled item"):
            validate_labels(items, [invalid])
        parse_failed = copy.deepcopy(prediction)
        parse_failed["events"] = []
        parse_failed["parse_error"] = "invalid JSON"
        score = evaluate(items, [label], [parse_failed])
        self.assertEqual(score["parse_error_items"], 1)
        self.assertEqual(score["event_detection"]["fn"], 1)
        empty = copy.deepcopy(label)
        empty["status"], empty["annotator_id"], empty["events"] = "unlabeled", None, []
        self.assertEqual(evaluate(items, [empty], [prediction])["status"], "no_reviewed_predictions")

    def test_strict_config_and_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = base_config()
            config["company_split_percent"]["train"] = 70
            path = root / "config.json"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "sum to 100"):
                load_config(path)
            records = root / "records.jsonl"
            records.write_text('{"item_id":"a","item_id":"b"}\n')
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                read_jsonl(records)

    def test_literal_baseline_is_grounded_but_does_not_assert_direction(self):
        question = item()
        prediction = predict_item(question)
        self.assertEqual(prediction["events"][0]["event_type"], "earnings")
        self.assertEqual(prediction["events"][0]["direction"], "unknown")
        validate_predictions({question["item_id"]: question}, [prediction])
        reply = item("reply")
        prediction = predict_item(reply)
        self.assertEqual(prediction["events"][0]["evidence_spans"][0]["source"], "question")
        reply["segments"][1]["text"] = "利润并未增长"
        reply["source_text_sha256"] = canonical_hash(reply["segments"])
        prediction = predict_item(reply)
        self.assertEqual(prediction["events"][0]["evidence_spans"][0]["source"], "reply")
        self.assertEqual(prediction["events"][0]["direction"], "unknown")

    def test_raw_model_output_parsing_records_hallucinated_evidence(self):
        source = item()
        base = {"item_id": source["item_id"], "source_text_sha256": source["source_text_sha256"],
                "model_id": "fixture-model", "prompt_version": "v1"}
        good = {**base, "raw_response": json.dumps({"events": [event()]}, ensure_ascii=False)}
        self.assertIsNone(normalize_rows({source["item_id"]: source}, [good])[0]["parse_error"])
        bad = {**base, "raw_response": "not JSON"}
        self.assertIn("JSONDecodeError", normalize_rows({source["item_id"]: source}, [bad])[0]["parse_error"])
        duplicate = {**base, "raw_response": '{"events":[],"events":[]}'}
        self.assertIn("duplicate JSON key", normalize_rows({source["item_id"]: source}, [duplicate])[0]["parse_error"])
        hallucinated = event()
        hallucinated["evidence_spans"][0]["quote"] = "未见于原文"
        bad = {**base, "raw_response": json.dumps({"events": [hallucinated]}, ensure_ascii=False)}
        self.assertIn("exactly match", normalize_rows({source["item_id"]: source}, [bad])[0]["parse_error"])
        wrong_source = {**good, "source_text_sha256": "b" * 64}
        with self.assertRaisesRegex(ValueError, "different source text"):
            normalize_rows({source["item_id"]: source}, [wrong_source])
        with self.assertRaisesRegex(ValueError, "uniqueness"):
            normalize_rows({source["item_id"]: source}, [good, good])

    def test_pack_end_to_end_source_and_artifact_hashes(self):
        config = base_config()
        rows = synthetic_rows(config)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "问答"
            sheet.append(["股票代码", "提问时间", "提问内容", "上市公司是否回复", "回复时间", "回复内容"])
            for _, code, _, q, _, r, _ in rows:
                sheet.append([code, "2020-01-10 10:00:00", q, "已回复", "2020-01-11 10:00:00", r])
            source = root / "qa.xlsx"
            workbook.save(source)
            workbook.close()
            build_dataset([source], root / "dataset")
            config["qa_source_sha256"] = file_sha256(source)
            path = root / "config.json"
            path.write_text(json.dumps(config))
            manifest = build_annotation_pack(path, root / "pack")
            self.assertEqual(manifest["counts"]["items"], 48)
            loaded, saved = load_pack(root / "pack")
            self.assertEqual(len(loaded), 48)
            self.assertEqual(saved["experiment_id"], manifest["experiment_id"])
            template = read_jsonl(root / "pack/annotation_template.jsonl")
            self.assertEqual(validate_labels(loaded, template)["reviewed_items"], 0)
            baseline = run_baseline(root / "pack", root / "keyword_baseline")
            self.assertEqual(baseline["validation"]["prediction_rows"], 48)
            self.assertEqual(baseline["validation"]["parse_errors"], 0)
            first_item = next(iter(loaded.values()))
            raw_path = root / "raw_model.jsonl"
            raw_path.write_text(json.dumps({"item_id": first_item["item_id"],
                                            "source_text_sha256": first_item["source_text_sha256"],
                                            "model_id": "fixture-model", "prompt_version": "v1",
                                            "raw_response": "{\"events\":[]}"}) + "\n")
            (root / "model_run_manifest.json").write_text(json.dumps({
                "pack_experiment_id": manifest["experiment_id"],
                "input_sha256": {"annotation_manifest": file_sha256(root / "pack/annotation_manifest.json"),
                                 "annotation_items": file_sha256(root / "pack/annotation_items.jsonl")},
                "artifacts": {"raw_model.jsonl": {"sha256": file_sha256(raw_path)}}
            }), encoding="utf-8")
            normalized = normalize_file(root / "pack", raw_path, root / "normalized")
            self.assertEqual(normalized["model_rows"], 1)
            self.assertEqual(normalized["parse_errors"], 0)
            self.assertIn("model_run_manifest", normalized["input_sha256"])
            with self.assertRaisesRegex(ValueError, "new empty"):
                build_annotation_pack(path, root / "pack")
            predictions = root / "predictions.jsonl"
            with predictions.open("w", encoding="utf-8") as handle:
                for item_id, sample in loaded.items():
                    handle.write(json.dumps({"item_id": item_id, "source_text_sha256": sample["source_text_sha256"],
                                             "model_id": "fixture-only", "prompt_version": "v1",
                                             "events": [], "parse_error": None}) + "\n")
            report = evaluate_files(root / "pack", root / "pack/annotation_template.jsonl", predictions, root / "evaluation.json")
            self.assertEqual(report["status"], "no_reviewed_predictions")
            original = root / "pack/annotation_items.jsonl"
            original.write_text(original.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact hash mismatch"):
                load_pack(root / "pack")


if __name__ == "__main__":
    unittest.main()
