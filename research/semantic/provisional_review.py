"""Create a single-reviewer provisional semantic review without opening the gold gate.

This tool is deliberately separate from the dual-review workflow.  It is useful for
checking the model run and exercising downstream code while a real independent
review is still pending.  Its output must not be used as a gold manifest.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .signal_validation import load_pack, read_jsonl, validate_labels, validate_predictions
from ..data_pipeline.provenance import file_sha256


REVIEW_VERSION = "semantic-single-provisional-review-v1"
ANNOTATOR_ID = "codex-provisional-single-2026-09-27"


def _segments(item: dict[str, Any]) -> dict[str, str]:
    return {segment["source"]: segment["text"] for segment in item["segments"]}


def _span(item: dict[str, Any], source: str, quote: str) -> dict[str, Any]:
    text = _segments(item)[source]
    start = text.find(quote)
    if start < 0:
        raise ValueError(f"review quote not found for {item['item_id']}: {quote}")
    return {"source": source, "start": start, "end": start + len(quote), "quote": quote}


def _event(
    item: dict[str, Any],
    *,
    event_type: str,
    direction: str,
    industries: list[str],
    horizon: str,
    intensity: float,
    uncertainty: float,
    source: str,
    quote: str,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "direction": direction,
        "affected_industries": industries,
        "horizon": horizon,
        "intensity": intensity,
        "uncertainty": uncertainty,
        "evidence_spans": [_span(item, source, quote)],
    }


def _reply_only_candidates(item: dict[str, Any], prediction: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep model candidates only when they have source-grounded reply evidence."""
    events: list[dict[str, Any]] = []
    for candidate in prediction["events"]:
        reply_spans = [span for span in candidate["evidence_spans"] if span["source"] == "reply"]
        if not reply_spans:
            continue
        reviewed = copy.deepcopy(candidate)
        reviewed["evidence_spans"] = reply_spans
        events.append(reviewed)
    return events


def _apply_manual_review(item: dict[str, Any], prediction: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply the semantic decisions made during this provisional pass."""
    if item["stage"] == "question":
        return [], ["question-only sample: no company reply available; claims and speculation were not promoted to events"]

    events = _reply_only_candidates(item, prediction)
    notes: list[str] = []

    # Remove a vague aspiration from the regulatory answer; it is not an independent event.
    if item["item_id"] == "00b5be747b822d0b0df194d5d81b60aee1599392c6b2157ce4ecda22a1a3c5ea":
        events = events[:1]
        notes.append("removed vague product-line aspiration; retained the explicit approval uncertainty")

    # The company confirms poor operating performance, which is an earnings signal.
    if item["item_id"] == "8362141be7278a7bf86013b0411169198be8d22743f07ae0f05a22604bde069":
        if events:
            events[0]["event_type"] = "earnings"
            events[0]["direction"] = "negative"
            events[0]["affected_industries"] = ["氨基酸", "糖类"]
        notes.append("reclassified explicit poor subsidiary performance as negative earnings")

    # A liquidity concern stated by the question is not evidence; the reply says capacity is normal.
    if item["item_id"] == "6b708a0893a4c6e97fd3bb85336c7dd92ef6a2f64bacc7ada005cb365bc395c4":
        if events:
            events[0]["direction"] = "neutral"
            events[0]["intensity"] = 0.3
            events[0]["uncertainty"] = 0.3
        notes.append("removed unconfirmed investor concern; retained the company's normal-capacity statement")

    # Ownership/disclosure answers are governance facts, not generic other events.
    if item["item_id"] == "df7652fd46e0f94cd9ac6a9f90b5a2ddb50a760547d845c7858d20e5bbe8fdf6":
        if events:
            events[0]["event_type"] = "governance"
        notes.append("classified shareholder-disclosure answer as governance")
    if item["item_id"] == "9d88472df66f4d6e0aefff10758ea9d57c5120aa7261efbf12e9d86ebfdff2c2":
        if events:
            events[0]["event_type"] = "governance"
            events[0]["direction"] = "neutral"
        notes.append("classified disclosure-compliance answer as neutral governance")

    # Add explicit operational/regulatory facts that the candidate run missed.
    if item["item_id"] == "b5d52b5823e807e2f3dcfbe496184b2a9f0432151eda0ed6d36f8f257dff6737":
        events.append(_event(
            item,
            event_type="other",
            direction="negative",
            industries=[],
            horizon="unknown",
            intensity=0.4,
            uncertainty=0.3,
            source="reply",
            quote="公司的量子应用安全服务平台技术尚未产业化",
        ))
        notes.append("added explicit not-yet-commercialized platform status")
    if item["item_id"] == "c8a10af9c5616082dcb8771385b279f8064c8632165af4df293ebdf71aeb7b69":
        events.append(_event(
            item,
            event_type="regulation",
            direction="positive",
            industries=["医药"],
            horizon="unknown",
            intensity=0.5,
            uncertainty=0.3,
            source="reply",
            quote="目前，公司非酒精性脂肪肝病及肝纤维化可逆转新药GST-HG151已获批临床",
        ))
        notes.append("added explicit clinical-approval status")
    if item["item_id"] == "a61a79d20acd720923fd2091f22ce7b27e20cfff826af1b48e9f97b6a06ea32c":
        events.append(_event(
            item,
            event_type="other",
            direction="neutral",
            industries=[],
            horizon="unknown",
            intensity=0.3,
            uncertainty=0.5,
            source="reply",
            quote="公司有尝试大宗业务，但体量较小，如果仅以传统的供应商身份进入工程业务，从长远价值来看并没有那么大。公司会在深耕零售的基础上，保持工程业务的接触，希望可以找到一些创新的工程业务合作方式。",
        ))
        notes.append("added explicit small-scale engineering-business status and plan")

    if not notes:
        notes.append("checked event meaning, direction, horizon and exact reply evidence")
    return events, notes


def build_review(pack_dir: Path, predictions_path: Path, output_dir: Path) -> dict[str, Any]:
    items, pack_manifest = load_pack(pack_dir)
    predictions = read_jsonl(predictions_path)
    validate_predictions(items, predictions)
    prediction_by_id = {row["item_id"]: row for row in predictions}
    labels: list[dict[str, Any]] = []
    correction_counts: Counter[str] = Counter()

    for item_id, item in items.items():
        prediction = prediction_by_id[item_id]
        events, notes = _apply_manual_review(item, prediction)
        if item["stage"] == "question":
            correction_counts["question_events_suppressed"] += len(prediction["events"])
        else:
            candidate_count = len(prediction["events"])
            if candidate_count > len(events):
                correction_counts["candidate_events_removed_or_deduped"] += candidate_count - len(events)
        if any("added " in note for note in notes):
            correction_counts["explicit_events_added"] += sum("added " in note for note in notes)
        labels.append({
            "item_id": item_id,
            "source_text_sha256": item["source_text_sha256"],
            "annotator_id": ANNOTATOR_ID,
            "status": "labeled",
            "events": events,
            "notes": "single-agent provisional review; " + "; ".join(notes),
        })

    audit = validate_labels(items, labels)
    output_dir.mkdir(parents=True, exist_ok=False)
    labels_path = output_dir / "provisional_labels.jsonl"
    labels_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in labels) + "\n", encoding="utf-8")
    report = {
        "review_version": REVIEW_VERSION,
        "protocol": "single_agent_provisional",
        "gold_ready": False,
        "annotator_id": ANNOTATOR_ID,
        "pack_experiment_id": pack_manifest["experiment_id"],
        "review_scope": "all 128 H2 2020 items; question-only claims suppressed; reply evidence checked",
        "audit": audit,
        "event_counts": dict(Counter(event["event_type"] for row in labels for event in row["events"])),
        "direction_counts": dict(Counter(event["direction"] for row in labels for event in row["events"])),
        "correction_counts": dict(correction_counts),
        "independent_reviewers_required": 2,
        "adjudicator_required": True,
        "limitations": [
            "This pass has one reviewer and cannot establish inter-annotator agreement.",
            "Model candidates were visible during this engineering check, so these labels are not an independent gold reference.",
            "The formal research signal gate remains blocked until two independent reviews and adjudication are completed.",
        ],
        "input_sha256": {
            "annotation_manifest.json": file_sha256(pack_dir / "annotation_manifest.json"),
            "annotation_items.jsonl": file_sha256(pack_dir / "annotation_items.jsonl"),
            "model_predictions.jsonl": file_sha256(predictions_path),
            "reviewer_code": file_sha256(Path(__file__)),
        },
    }
    (output_dir / "provisional_review_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = [
        "# H2 2020 单人临时语义复核",
        "",
        "本目录是工程检查结果，不是正式金标准。复核者为单一 Codex 会话，模型候选在复核过程中可见，因此不能用于声称模型质量或替代双人盲审。",
        "",
        f"- 条目：{audit['label_rows']}；已标注：{audit['reviewed_items']}；问题阶段：64；回复阶段：64。",
        f"- 事件：{sum(len(row['events']) for row in labels)}；按类型：{json.dumps(report['event_counts'], ensure_ascii=False)}。",
        f"- 主要清理：压制问题阶段模型事件 {correction_counts['question_events_suppressed']} 个；去除无回复证据候选 {correction_counts['candidate_events_removed_or_deduped']} 个；补充明确回复事实 {correction_counts['explicit_events_added']} 个。",
        "- 正式状态：`gold_ready=false`，双人独立审核与第三人裁定仍未完成，Agent 语义门控仍保持阻断。",
        "",
        "输出：`provisional_labels.jsonl`、`provisional_review_manifest.json`。",
    ]
    (output_dir / "provisional_review_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_review(args.pack_dir, args.predictions, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
