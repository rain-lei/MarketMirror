"""Finalize explicit item-by-item AI review decisions under the user-selected protocol."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .signal_validation import load_pack, read_jsonl, validate_labels
from ..data_pipeline.provenance import file_sha256

VERSION = "semantic-assistant-review-v1"
POLICY_PATH = Path(__file__).resolve().parents[1] / "configs/assistant_review_policy.json"


def validate_review(items: dict[str, dict], labels: list[dict]) -> dict[str, Any]:
    audit = validate_labels(items, labels)
    if not items or audit["reviewed_items"] != len(items) or audit["missing_label_rows"]:
        raise ValueError("assistant review requires every item to be explicitly completed")
    reviewers = {row["annotator_id"] for row in labels}
    if len(reviewers) != 1 or not next(iter(reviewers)).startswith("ai:"):
        raise ValueError("reference requires one explicitly identified AI reviewer")
    if any(len(row["notes"].strip()) < 12 for row in labels) or len({row["notes"] for row in labels}) != len(labels):
        raise ValueError("every item needs its own substantive review rationale")
    for row in labels:
        item = items[row["item_id"]]
        if item["stage"] == "question" and row["events"]:
            raise ValueError("current review policy does not promote question-only claims")
        sources = {segment["source"]: segment["text"] for segment in item["segments"]}
        for event in row["events"]:
            for span in event["evidence_spans"]:
                if span["source"] != "reply" or span["end"] > len(sources[span["source"]]):
                    raise ValueError("review events require bounded reply evidence")
    return {**audit, "reviewer_id": next(iter(reviewers)), "reviewer_kind": "ai_assistant",
            "event_items": sum(bool(row["events"]) for row in labels),
            "events": sum(len(row["events"]) for row in labels),
            "event_counts": dict(Counter(e["event_type"] for row in labels for e in row["events"])),
            "stage_counts": dict(Counter(item["stage"] for item in items.values()))}


def finalize_review(pack_dir: Path, decisions: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, decisions, output_dir = [p.resolve() for p in (pack_dir, decisions, output_dir)]
    if any(output_dir == p or output_dir in p.parents or p in output_dir.parents
           for p in (pack_dir, decisions)):
        raise ValueError("review output must be separate from inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use an empty reference output directory")
    inputs = {str(p): file_sha256(p) for p in (pack_dir / "annotation_manifest.json",
              pack_dir / "annotation_items.jsonl", decisions, POLICY_PATH)}
    items, pack = load_pack(pack_dir)
    labels = read_jsonl(decisions)
    audit = validate_review(items, labels)
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    result = {"pipeline_version": VERSION, "experiment_id": pack["experiment_id"],
              "protocol": policy["protocol"], "status": "assistant_review_complete",
              "reference_ready": True, "gold_ready": False, "independent_reference": False,
              "model_outputs_previously_seen": True, "sample_role": policy["sample_role"],
              "score_interpretation": policy["score_interpretation"], "audit": audit}
    if any(file_sha256(Path(p)) != h for p, h in inputs.items()):
        raise RuntimeError("review input changed during finalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temp:
        staging = Path(temp)
        shutil.copyfile(decisions, staging / "reference_labels.jsonl")
        (staging / "assistant_review_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        report = ["# AI 逐条复核参考标签", "",
                  f"已完成 {audit['reviewed_items']}/{audit['pack_items']} 条；{audit['event_items']} 条含事件，共 {audit['events']} 个事件。",
                  "用户已取消双人审核和第三人裁定要求。每条标签有单独理由，所有事件有回复原文证据；问题阶段不确认投资者推测。",
                  "此参考来自单一 AI 复核，曾见过模型候选；后续分数表示与本次复核的一致性，不能称为独立人工准确率。",
                  "本批样本按探索性开发材料使用；金标准状态仍为 false，但不再是当前实验的阻断条件。", ""]
        (staging / "assistant_review_report.md").write_text("\n".join(report), encoding="utf-8")
        manifest = {**result, "inputs": inputs, "code_sha256": file_sha256(Path(__file__)),
                    "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "assistant_review_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for p in staging.iterdir():
            p.replace(output_dir / p.name)
    return result


def load_review(pack_dir: Path, review_dir: Path) -> tuple[list[dict], dict]:
    items, pack = load_pack(pack_dir)
    manifest = json.loads((review_dir / "assistant_review_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION or manifest.get("experiment_id") != pack["experiment_id"]
            or manifest.get("protocol") != "assistant_review_v1" or manifest.get("gold_ready") is not False
            or manifest.get("independent_reference") is not False or manifest.get("reference_ready") is not True
            or manifest.get("model_outputs_previously_seen") is not True
            or manifest.get("code_sha256") != file_sha256(Path(__file__))):
        raise ValueError("assistant reference metadata or code binding is invalid")
    required_inputs = [pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl", POLICY_PATH]
    if any(manifest.get("inputs", {}).get(str(p.resolve())) != file_sha256(p) for p in required_inputs):
        raise ValueError("assistant reference belongs to different source or policy")
    for name, digest in manifest["inputs"].items():
        if file_sha256(Path(name)) != digest:
            raise ValueError("assistant review input has changed")
    expected = {"reference_labels.jsonl", "assistant_review_result.json", "assistant_review_report.md"}
    if set(manifest.get("artifacts", {})) != expected:
        raise ValueError("assistant reference artifact set is invalid")
    for name in expected:
        if file_sha256(review_dir / name) != manifest["artifacts"][name]["sha256"]:
            raise ValueError("assistant review artifact has changed")
    labels = read_jsonl(review_dir / "reference_labels.jsonl")
    audit = validate_review(items, labels)
    if audit != manifest["audit"]:
        raise ValueError("assistant review counts differ from actual labels")
    return labels, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(finalize_review(args.pack_dir, args.decisions, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
