"""Validate source-grounded human labels and model outputs; score only reviewed items."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .annotation_pack import VERSION as PACK_VERSION, canonical_hash
from ..data_pipeline.provenance import file_sha256

EVENT_TYPES = ("regulation", "liquidity", "earnings", "governance", "other")
DIRECTIONS = ("positive", "negative", "neutral", "unknown")
HORIZONS = ("short", "medium", "long", "unknown")


def strict_json_loads(value: str) -> Any:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(value, object_pairs_hook=unique_pairs)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        records = [strict_json_loads(line) for line in handle if line.strip()]
    if not all(isinstance(row, dict) for row in records):
        raise ValueError("every JSONL line must be an object")
    return records


def load_pack(directory: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    directory = directory.resolve()
    manifest_path = directory / "annotation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("pipeline_version") != PACK_VERSION:
        raise ValueError("unsupported annotation pack version")
    for filename, expected in manifest["artifacts"].items():
        if file_sha256(directory / filename) != expected["sha256"]:
            raise ValueError(f"annotation artifact hash mismatch: {filename}")
    items = read_jsonl(directory / "annotation_items.jsonl")
    if len(items) != manifest["counts"]["items"] or len({i["item_id"] for i in items}) != len(items):
        raise ValueError("annotation item count or uniqueness differs from manifest")
    for item in items:
        if item["source_text_sha256"] != canonical_hash(item["segments"]):
            raise ValueError("visible source text hash mismatch")
        if item["stage"] == "question" and [s["source"] for s in item["segments"]] != ["question"]:
            raise ValueError("question stage contains future reply")
        if item["stage"] == "reply" and [s["source"] for s in item["segments"]] != ["question", "reply"]:
            raise ValueError("reply stage source segments are incomplete")
    return {item["item_id"]: item for item in items}, manifest


def validate_event(event: dict[str, Any], item: dict[str, Any]) -> None:
    required = {"event_type", "direction", "affected_industries", "horizon", "intensity", "uncertainty", "evidence_spans"}
    if not isinstance(event, dict) or set(event) != required:
        raise ValueError("event fields differ from semantic signal schema")
    if event["event_type"] not in EVENT_TYPES or event["direction"] not in DIRECTIONS or event["horizon"] not in HORIZONS:
        raise ValueError("event enums are invalid")
    for key in ("intensity", "uncertainty"):
        value = event[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{key} must be a finite number in [0,1]")
    industries = event["affected_industries"]
    if not isinstance(industries, list) or any(not isinstance(v, str) or not v.strip() for v in industries) or len(industries) != len(set(industries)):
        raise ValueError("affected_industries must be unique nonempty strings")
    spans = event["evidence_spans"]
    if not isinstance(spans, list) or not spans:
        raise ValueError("every event needs source-grounded evidence")
    sources = {segment["source"]: segment["text"] for segment in item["segments"]}
    for span in spans:
        if not isinstance(span, dict) or set(span) != {"source", "start", "end", "quote"}:
            raise ValueError("evidence span fields are invalid")
        source, start, end, quote = span["source"], span["start"], span["end"], span["quote"]
        if source not in sources or type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise ValueError("evidence source or offsets are invalid")
        if not isinstance(quote, str) or not quote or sources[source][start:end] != quote:
            raise ValueError("evidence quote must exactly match visible source text")


def validate_labels(items: dict[str, dict[str, Any]], labels: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {"item_id", "source_text_sha256", "annotator_id", "status", "events", "notes"}
    seen = set()
    labeled = []
    for row in labels:
        if set(row) != expected or row["item_id"] not in items or row["item_id"] in seen:
            raise ValueError("label fields, item identity or uniqueness are invalid")
        seen.add(row["item_id"])
        item = items[row["item_id"]]
        if row["source_text_sha256"] != item["source_text_sha256"]:
            raise ValueError("label source text hash differs from annotation item")
        if row["status"] not in {"unlabeled", "labeled", "needs_review"} or not isinstance(row["notes"], str):
            raise ValueError("invalid label status or notes")
        if not isinstance(row["events"], list):
            raise ValueError("label events must be a list")
        if row["status"] == "unlabeled":
            if row["annotator_id"] is not None or row["events"]:
                raise ValueError("unlabeled item must have no annotator or events")
        elif not isinstance(row["annotator_id"], str) or not row["annotator_id"].strip():
            raise ValueError("reviewed label requires annotator_id")
        for event in row["events"]:
            validate_event(event, item)
        if row["status"] == "labeled":
            labeled.append(row)
    return {"pack_items": len(items), "label_rows": len(labels), "reviewed_items": len(labeled),
            "unlabeled_items": sum(r["status"] == "unlabeled" for r in labels),
            "needs_review_items": sum(r["status"] == "needs_review" for r in labels),
            "missing_label_rows": len(items) - len(labels)}


def validate_predictions(items: dict[str, dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {"item_id", "source_text_sha256", "model_id", "prompt_version", "events", "parse_error"}
    seen = set()
    for row in predictions:
        if set(row) != expected or row["item_id"] not in items or row["item_id"] in seen:
            raise ValueError("prediction fields, item identity or uniqueness are invalid")
        seen.add(row["item_id"])
        if row["source_text_sha256"] != items[row["item_id"]]["source_text_sha256"]:
            raise ValueError("prediction source text hash differs from annotation item")
        if any(not isinstance(row[name], str) or not row[name].strip() for name in ("model_id", "prompt_version")):
            raise ValueError("model_id and prompt_version are required")
        if row["parse_error"] is not None and (not isinstance(row["parse_error"], str) or not row["parse_error"].strip() or row["events"]):
            raise ValueError("parse error requires empty events and nonempty error text")
        if not isinstance(row["events"], list):
            raise ValueError("prediction events must be a list")
        for event in row["events"]:
            validate_event(event, items[row["item_id"]])
    return {"prediction_rows": len(predictions), "parse_errors": sum(r["parse_error"] is not None for r in predictions),
            "missing_predictions": len(items) - len(predictions)}


def binary_counts(gold: list[bool], predicted: list[bool]) -> dict[str, Any]:
    tp = sum(g and p for g, p in zip(gold, predicted))
    fp = sum(not g and p for g, p in zip(gold, predicted))
    fn = sum(g and not p for g, p in zip(gold, predicted))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def evaluate(items: dict[str, dict[str, Any]], labels: list[dict[str, Any]],
             predictions: list[dict[str, Any]]) -> dict[str, Any]:
    label_audit = validate_labels(items, labels)
    prediction_audit = validate_predictions(items, predictions)
    gold = {row["item_id"]: row for row in labels if row["status"] == "labeled"}
    pred = {row["item_id"]: row for row in predictions}
    common = sorted(set(gold) & set(pred))
    result = {"label_audit": label_audit, "prediction_audit": prediction_audit,
              "scored_items": len(common), "unscored_reviewed_items": len(gold) - len(common),
              "sampling_note": "Keyword-stratified company-grouped sample; scores are not population prevalence estimates."}
    if not common:
        result["status"] = "no_reviewed_predictions"
        return result
    valid = [item_id for item_id in common if pred[item_id]["parse_error"] is None]
    result["status"] = "scored"
    result["valid_prediction_items"] = len(valid)
    result["parse_error_items"] = len(common) - len(valid)
    # Parse errors are evaluated as empty predictions, not silently omitted.
    result["event_detection"] = binary_counts([bool(gold[k]["events"]) for k in common],
                                               [bool(pred[k]["events"]) for k in common])
    per_type = {}
    for event_type in EVENT_TYPES:
        per_type[event_type] = binary_counts(
            [event_type in {e["event_type"] for e in gold[k]["events"]} for k in common],
            [event_type in {e["event_type"] for e in pred[k]["events"]} for k in common])
    result["event_type_by_class"] = per_type
    supported = [name for name in EVENT_TYPES if per_type[name]["tp"] + per_type[name]["fn"] > 0]
    result["event_type_macro_f1_supported"] = (sum(per_type[name]["f1"] for name in supported) / len(supported)
                                               if supported else None)
    result["supported_event_types"] = supported
    single = [(gold[k]["events"][0], pred[k]["events"][0]) for k in common
              if len(gold[k]["events"]) == len(pred[k]["events"]) == 1
              and gold[k]["events"][0]["event_type"] == pred[k]["events"][0]["event_type"]]
    result["single_event_type_matched_items"] = len(single)
    result["direction_accuracy_on_matched_single"] = (sum(g["direction"] == p["direction"] for g, p in single) / len(single)
                                                     if single else None)
    gold_spans = [set((s["source"], s["start"], s["end"]) for s in g["evidence_spans"]) for g, _ in single]
    pred_spans = [set((s["source"], s["start"], s["end"]) for s in p["evidence_spans"]) for _, p in single]
    result["evidence_exact_span_recall_on_matched_single"] = (
        sum(len(g & p) for g, p in zip(gold_spans, pred_spans)) / sum(len(g) for g in gold_spans)
        if single else None)
    result["metric_limitations"] = ["Direction and span metrics are conditional on exact single-event type matches.",
                                    "Multiple-event alignment, intensity calibration and industry scoring are not covered yet.",
                                    "Human reference labels require adjudication and inter-annotator agreement before claiming model quality."]
    return result


def evaluate_files(pack_dir: Path, gold_path: Path, prediction_path: Path, output_path: Path) -> dict[str, Any]:
    items, manifest = load_pack(pack_dir)
    gold_path, prediction_path, output_path = gold_path.resolve(), prediction_path.resolve(), output_path.resolve()
    inputs = {gold_path: file_sha256(gold_path), prediction_path: file_sha256(prediction_path),
              (pack_dir / "annotation_manifest.json").resolve(): file_sha256(pack_dir / "annotation_manifest.json")}
    if output_path in inputs or output_path in {p.resolve() for p in pack_dir.iterdir()}:
        raise ValueError("evaluation output must not replace inputs")
    report = evaluate(items, read_jsonl(gold_path), read_jsonl(prediction_path))
    if any(file_sha256(p) != h for p, h in inputs.items()):
        raise RuntimeError("evaluation input changed during scoring")
    report.update({"pack_experiment_id": manifest["experiment_id"], "input_sha256": {str(p): h for p, h in inputs.items()},
                   "evaluator_sha256": file_sha256(Path(__file__))})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise ValueError("use a fresh evaluation output path")
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate_files(args.pack_dir, args.gold, args.predictions, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
