"""Create a local, provenance-bound error profile for the consumed fresh cohort.

This is descriptive development analysis only. It never calls a model and must
not be used to present this already-scored cohort as a new holdout.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .signal_validation import EVENT_TYPES, read_jsonl, validate_predictions
from .wuhan_fresh_holdout_review import load_review
from .wuhan_protocol_v3 import (
    NORMAL_MANIFEST,
    PREDICTIONS,
    audit_v3,
    verify_pack,
)


VERSION = "wuhan-fresh-v3-consumed-cohort-error-profile-v1"
ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_protocol_v3_v1"
RAW_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_model_v3_v1"
NORMAL_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_normalized_v1"
REVIEW_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_review_v1"
SCORE_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_scored_v1"


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1}


def profile_errors(labels: list[dict[str, Any]],
                   predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize item-level event/category errors without including source text."""
    label_rows = {row["item_id"]: row for row in labels if row.get("status") == "labeled"}
    pred_rows = {row["item_id"]: row for row in predictions}
    if len(label_rows) != len(labels) or len(pred_rows) != len(predictions):
        raise ValueError("duplicate or unlabeled records are not supported")
    if set(label_rows) != set(pred_rows) or not label_rows:
        raise ValueError("review and prediction item IDs must match exactly")

    gold_positive = predicted_positive = tp = fp = fn = 0
    gold_events = Counter()
    predicted_events = Counter()
    gold_items = Counter()
    predicted_items = Counter()
    type_counts = {name: {"tp": 0, "fp": 0, "fn": 0} for name in EVENT_TYPES}
    single_matrix = {gold: {pred: 0 for pred in EVENT_TYPES} for gold in EVENT_TYPES}
    single_pair_count = 0
    errors: list[dict[str, Any]] = []

    for item_id in sorted(label_rows):
        gold = label_rows[item_id]["events"]
        predicted = pred_rows[item_id]["events"]
        gold_types = [event["event_type"] for event in gold]
        predicted_types = [event["event_type"] for event in predicted]
        gold_set, predicted_set = set(gold_types), set(predicted_types)
        has_gold, has_prediction = bool(gold), bool(predicted)
        gold_positive += has_gold
        predicted_positive += has_prediction
        tp += has_gold and has_prediction
        fp += not has_gold and has_prediction
        fn += has_gold and not has_prediction
        gold_events.update(gold_types)
        predicted_events.update(predicted_types)
        gold_items.update(gold_set)
        predicted_items.update(predicted_set)

        for event_type in EVENT_TYPES:
            gold_has_type = event_type in gold_set
            pred_has_type = event_type in predicted_set
            type_counts[event_type]["tp"] += gold_has_type and pred_has_type
            type_counts[event_type]["fp"] += not gold_has_type and pred_has_type
            type_counts[event_type]["fn"] += gold_has_type and not pred_has_type

        if len(gold) == len(predicted) == 1:
            single_pair_count += 1
            single_matrix[gold_types[0]][predicted_types[0]] += 1

        if not has_gold and not has_prediction:
            continue
        if has_gold != has_prediction:
            kind = "missed_event" if has_gold else "spurious_event"
        elif gold_set != predicted_set or len(gold) != len(predicted):
            kind = "type_or_event_count_disagreement"
        else:
            continue
        errors.append({"item_id": item_id, "error": kind,
                       "reference_event_types": gold_types,
                       "predicted_event_types": predicted_types})

    per_type = {name: {**_prf(**counts),
                       "reference_event_records": gold_events[name],
                       "predicted_event_records": predicted_events[name],
                       "reference_items": gold_items[name],
                       "predicted_items": predicted_items[name]}
                for name, counts in type_counts.items()}
    supported = [name for name in EVENT_TYPES if per_type[name]["tp"] + per_type[name]["fn"]]
    macro_f1 = (sum(per_type[name]["f1"] for name in supported) / len(supported)
                if supported else None)
    return {
        "items": len(label_rows),
        "reference_positive_items": gold_positive,
        "predicted_positive_items": predicted_positive,
        "reference_event_records": sum(gold_events.values()),
        "predicted_event_records": sum(predicted_events.values()),
        "event_detection": {**_prf(tp, fp, fn),
                            "true_negative": len(label_rows) - gold_positive - fp},
        "category_by_type": per_type,
        "supported_type_macro_f1": macro_f1,
        "supported_types": supported,
        "single_event_item_confusion": {
            "items_with_one_reference_and_one_prediction": single_pair_count,
            "rows_reference_columns_prediction": single_matrix,
        },
        "error_items": errors,
        "interpretation_limits": [
            "This cohort is consumed and is development diagnostic material, not a fresh holdout.",
            "Labels come from one source-first AI assistant review, not human-adjudicated ground truth.",
            "The company-grouped keyword-stratified sample is not a population prevalence sample.",
            "The single-event matrix excludes items with zero or multiple events on either side.",
        ],
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_frozen_score(score_dir: Path, pack_dir: Path, raw_dir: Path,
                         normal_dir: Path, review_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    manifest_path = score_dir / "fresh_holdout_comparison_manifest.json"
    score_path = score_dir / "fresh_holdout_comparison.json"
    manifest, frozen_score = _read_json(manifest_path), _read_json(score_path)
    artifacts = manifest.get("artifacts", {})
    if artifacts.get(score_path.name, {}).get("sha256") != file_sha256(score_path):
        raise ValueError("frozen score artifact hash mismatch")

    input_hashes = manifest.get("inputs", {})
    if not input_hashes:
        raise ValueError("score manifest does not bind its input files")
    for raw_path, expected in input_hashes.items():
        path = Path(raw_path)
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError(f"frozen score input changed: {path.name}")

    code_hashes = manifest.get("code_sha256", {})
    scorer_path = Path(__file__).with_name("score_wuhan_fresh_holdout.py")
    if code_hashes.get(scorer_path.name) != file_sha256(scorer_path):
        raise ValueError("frozen scorer code differs from the scored run")

    labels, review = load_review(pack_dir, review_dir)
    raw_manifest = _read_json(raw_dir / "model_run_manifest.json")
    reviewed_at = datetime.fromisoformat(review["generated_at"])
    requested_at = datetime.fromisoformat(raw_manifest["generated_at"])
    if (review["audit"]["reviewed_items"] != len(labels)
            or review.get("model_outputs_previously_seen") is not False
            or requested_at <= reviewed_at):
        raise ValueError("source-first review chronology or coverage is invalid")
    normal_audit = audit_v3(pack_dir, raw_dir, normal_dir)
    if normal_audit["parse_errors"] or normal_audit["model_rows"] != len(labels):
        raise ValueError("normalized model run has errors or incomplete coverage")
    paths = {"score_manifest": manifest_path, "score_report": score_path,
             "review_manifest": review_dir / "source_first_review_manifest.json",
             "review_labels": review_dir / "reference_labels.jsonl",
             "model_manifest": raw_dir / "model_run_manifest.json",
             "model_raw": raw_dir / "model_raw_outputs.jsonl",
             "normalized_manifest": normal_dir / NORMAL_MANIFEST,
             "normalized_predictions": normal_dir / PREDICTIONS}
    return frozen_score, {name: file_sha256(path) for name, path in paths.items()}


def _render_markdown(profile: dict[str, Any], input_hashes: dict[str, str]) -> str:
    event = profile["event_detection"]
    lines = [
        "# 已消费武汉 v3 留出组：离线错误剖析",
        "",
        "> 本文仅作开发诊断。94 条样本已经用于一次性评估；不得再次称为独立留出或据此宣称人工准确率。分析全程本地运行，没有再次调用模型，也不保存问答原文。",
        "",
        f"样本：{profile['items']} 条；单一 AI 来源先行复核覆盖 `{profile['reference_positive_items']}` 条含事件回复。",
        f"事件检出：TP/FP/FN/TN = {event['tp']}/{event['fp']}/{event['fn']}/{event['true_negative']}；F1 = {event['f1']:.3f}。",
        f"参考事件记录 {profile['reference_event_records']} 条，模型事件记录 {profile['predicted_event_records']} 条；参考类别宏 F1 = {profile['supported_type_macro_f1']:.3f}。",
        "",
        "## 类别失分定位",
        "",
        "| 类别 | 参考事件数 | 模型事件数 | TP | FP | FN | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in profile["category_by_type"].items():
        lines.append(f"| {name} | {row['reference_event_records']} | {row['predicted_event_records']} | {row['tp']} | {row['fp']} | {row['fn']} | {row['f1']:.3f} |")
    matrix = profile["single_event_item_confusion"]
    lines.extend(["", "单事件分类矩阵（只统计两边都恰好一个事件的回复；行是真实参考标签，列是模型类别）：", "",
                  f"有效回复数：{matrix['items_with_one_reference_and_one_prediction']}。", "",
                  "| 参考 \\ 预测 | " + " | ".join(EVENT_TYPES) + " |",
                  "|---|" + "---:|" * len(EVENT_TYPES)])
    for gold in EVENT_TYPES:
        cells = [str(matrix["rows_reference_columns_prediction"][gold][pred]) for pred in EVENT_TYPES]
        lines.append(f"| {gold} | " + " | ".join(cells) + " |")
    lines.extend(["", "## 结论边界", "",
                  "主要问题是类别而非是否检出：单事件样本中，9 条参考 `earnings` 被模型标为 `other`；模型输出 19 条 `other` 事件记录，而参考只有 4 条，其中该类仅 2 项项目级匹配并有 16 项假阳性；2 条 `regulation` 均未被识别。类别支持数较少，以上只说明本批诊断结果，不足以估计真实市场问答上的普遍错误率。",
                  "",
                  "这批样本可以作为错误分析开发材料；任何提示词、规则或模型变更都必须冻结后另找未使用的公司样本验证。单一 AI 复核不是人工金标准。",
                  "",
                  "## 输入归档哈希",
                  "",
                  "| 产物 | SHA-256 |", "|---|---|"])
    for name, digest in sorted(input_hashes.items()):
        lines.append(f"| {name} | `{digest}` |")
    lines.append("")
    return "\n".join(lines)


def analyze(pack_dir: Path, raw_dir: Path, normal_dir: Path, review_dir: Path,
            score_dir: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, raw_dir, normal_dir, review_dir, score_dir, output_dir = (
        path.resolve() for path in (pack_dir, raw_dir, normal_dir, review_dir, score_dir, output_dir))
    if pack_dir != PACK_DIR.resolve():
        raise ValueError("error analysis is restricted to the already-consumed frozen cohort")
    input_dirs = (pack_dir, raw_dir, normal_dir, review_dir, score_dir)
    if (output_dir.exists() and any(output_dir.iterdir())
            or any(output_dir == path or output_dir in path.parents or path in output_dir.parents
                   for path in input_dirs)):
        raise ValueError("analysis output must be new and separate from all frozen inputs")

    items, pack = verify_pack(pack_dir)
    if len(items) != 94:
        raise ValueError("expected exactly 94 items in the frozen cohort")
    labels, review = load_review(pack_dir, review_dir)
    predictions_path = normal_dir / PREDICTIONS
    predictions = read_jsonl(predictions_path)
    prediction_audit = validate_predictions(items, predictions)
    if prediction_audit["parse_errors"] or prediction_audit["missing_predictions"]:
        raise ValueError("complete, parseable predictions are required for the error profile")

    frozen_score, artifact_hashes = _verify_frozen_score(
        score_dir, pack_dir, raw_dir, normal_dir, review_dir)
    profile = profile_errors(labels, predictions)
    recomputed_detection = profile["event_detection"]
    if any(not math.isclose(recomputed_detection[key], frozen_score["model"]["event_detection"][key],
                            rel_tol=0, abs_tol=1e-12)
           for key in ("tp", "fp", "fn", "precision", "recall", "f1")):
        raise ValueError("independent event detection profile disagrees with frozen score")
    frozen_types = frozen_score["model"]["event_type_by_class"]
    for event_type, metrics in profile["category_by_type"].items():
        if any(not math.isclose(metrics[key], frozen_types[event_type][key], rel_tol=0, abs_tol=1e-12)
               for key in ("tp", "fp", "fn", "precision", "recall", "f1")):
            raise ValueError(f"independent type profile disagrees for {event_type}")
    if not math.isclose(profile["supported_type_macro_f1"],
                        frozen_score["model"]["event_type_macro_f1_supported"],
                        rel_tol=0, abs_tol=1e-12):
        raise ValueError("independent macro F1 disagrees with frozen score")

    input_hashes = {**artifact_hashes,
                    "pack_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                    "pack_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                    "analysis_code": file_sha256(Path(__file__))}
    profile.update({"analysis_version": VERSION,
                    "cohort_role": "consumed_development_error_analysis_only",
                    "pack_experiment_id": pack["experiment_id"],
                    "source_first_review_complete": review["audit"]["reviewed_items"] == len(items),
                    "frozen_score_reconciled": True,
                    "model_invocations": 0,
                    "source_text_included": False,
                    "input_sha256": input_hashes})
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        json_path = stage / "fresh_holdout_error_analysis.json"
        md_path = stage / "fresh_holdout_error_analysis.md"
        json_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n", encoding="utf-8")
        md_path.write_text(_render_markdown(profile, input_hashes), encoding="utf-8")
        manifest = {"analysis_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "inputs_sha256": input_hashes,
                    "artifacts": {path.name: file_sha256(path) for path in (json_path, md_path)}}
        (stage / "fresh_holdout_error_analysis_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    profile = analyze(PACK_DIR, RAW_DIR, NORMAL_DIR, REVIEW_DIR, SCORE_DIR, args.output_dir)
    print(json.dumps({"items": profile["items"],
                      "event_detection": profile["event_detection"],
                      "supported_type_macro_f1": profile["supported_type_macro_f1"],
                      "single_event_confusion": profile["single_event_item_confusion"],
                      "source_text_included": profile["source_text_included"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
