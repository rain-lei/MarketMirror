"""Prepare blinded independent review packets and compare human annotations."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .signal_validation import load_pack, read_jsonl, validate_labels
from ..data_pipeline.provenance import file_sha256

VERSION = "semantic-double-review-v1"
SLOTS = ("reviewer_a", "reviewer_b")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def blinded_task(item: dict[str, Any]) -> dict[str, Any]:
    """Give reviewers the visible text and source binding without cohort information."""
    return {key: item[key] for key in ("item_id", "source_text_sha256", "stage", "segments")}


def prepare_packets(pack_dir: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, output_dir = pack_dir.resolve(), output_dir.resolve()
    items, pack_manifest = load_pack(pack_dir)
    if output_dir == pack_dir or output_dir in pack_dir.parents or pack_dir in output_dir.parents:
        raise ValueError("review output must be separate from source annotation pack")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty review packet directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        for slot in SLOTS:
            ordered = sorted(items.values(), key=lambda item: hashlib.sha256(
                f"{VERSION}:{slot}:{item['item_id']}".encode("utf-8")).hexdigest())
            _write_jsonl(staging / f"{slot}_tasks.jsonl", [blinded_task(item) for item in ordered])
            _write_jsonl(staging / f"{slot}_labels.jsonl", [
                {"item_id": item["item_id"], "source_text_sha256": item["source_text_sha256"],
                 "annotator_id": None, "status": "unlabeled", "events": [], "notes": ""} for item in ordered])
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "annotation_experiment_id": pack_manifest["experiment_id"],
                    "annotation_manifest_sha256": file_sha256(pack_dir / "annotation_manifest.json"),
                    "code_sha256": file_sha256(Path(__file__)), "items": len(items),
                    "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()},
                    "interpretation": "Blank independent reviewer packets; no human labels or gold standard are produced."}
        (staging / "review_packet_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def cohens_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    a_yes = sum(a for a, _ in pairs) / n
    b_yes = sum(b for _, b in pairs) / n
    expected = a_yes * b_yes + (1 - a_yes) * (1 - b_yes)
    return (observed - expected) / (1 - expected) if expected < 1 else None


def _canonical_events(events: list[dict[str, Any]]) -> list[str]:
    return sorted(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for event in events)


def _disagreement_reasons(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> list[str]:
    if bool(a) != bool(b):
        return ["event_presence"]
    if len(a) != len(b):
        return ["event_count"]
    if sorted(e["event_type"] for e in a) != sorted(e["event_type"] for e in b):
        return ["event_type"]
    if len(a) == len(b) == 1:
        return [name for name in ("direction", "horizon", "affected_industries", "intensity", "uncertainty", "evidence_spans")
                if a[0][name] != b[0][name]]
    return ["event_details_or_alignment"]


def compare_reviewers(items: dict[str, dict[str, Any]], labels_a: list[dict[str, Any]],
                      labels_b: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    audits = {"reviewer_a": validate_labels(items, labels_a),
              "reviewer_b": validate_labels(items, labels_b)}
    identities = []
    for labels in (labels_a, labels_b):
        found = {row["annotator_id"] for row in labels if row["status"] != "unlabeled"}
        if len(found) > 1:
            raise ValueError("one reviewer file contains multiple annotator identities")
        identities.append(next(iter(found), None))
    if identities[0] is not None and identities[0] == identities[1]:
        raise ValueError("independent reviewer files must have different annotator identities")
    by_a = {row["item_id"]: row for row in labels_a}
    by_b = {row["item_id"]: row for row in labels_b}
    pairs = []
    exact = 0
    type_set_exact = 0
    conflicts, pending = [], []
    by_split: dict[str, dict[str, int]] = {}
    for item_id in sorted(items):
        a, b = by_a.get(item_id), by_b.get(item_id)
        a_status = a["status"] if a else "missing"
        b_status = b["status"] if b else "missing"
        if a_status != "labeled" or b_status != "labeled":
            pending.append({"item_id": item_id, "source_text_sha256": items[item_id]["source_text_sha256"],
                            "reviewer_a_status": a_status, "reviewer_b_status": b_status})
            continue
        split = items[item_id]["split"]
        counts = by_split.setdefault(split, {"dual_reviewed": 0, "exact_agreement": 0})
        counts["dual_reviewed"] += 1
        pairs.append((bool(a["events"]), bool(b["events"])))
        type_set_exact += int({e["event_type"] for e in a["events"]} ==
                              {e["event_type"] for e in b["events"]})
        if _canonical_events(a["events"]) == _canonical_events(b["events"]):
            exact += 1
            counts["exact_agreement"] += 1
        else:
            conflicts.append({"item_id": item_id, "source_text_sha256": items[item_id]["source_text_sha256"],
                              "split": split, "reason_codes": _disagreement_reasons(a["events"], b["events"]),
                              "reviewer_a_events": a["events"], "reviewer_b_events": b["events"],
                              "reviewer_a_notes": a["notes"], "reviewer_b_notes": b["notes"]})
    dual = len(pairs)
    status = ("no_dual_review" if dual == 0 else
              "needs_adjudication" if conflicts else
              "awaiting_remaining_reviews" if pending else "agreement_complete_requires_final_approval")
    result = {"pipeline_version": VERSION, "status": status, "pack_items": len(items),
              "reviewer_ids": dict(zip(SLOTS, identities)), "reviewer_audit": audits,
              "dual_reviewed_items": dual, "pending_items": len(pending), "conflict_items": len(conflicts),
              "conflict_item_ids": [row["item_id"] for row in conflicts],
              "exact_event_agreement": exact / dual if dual else None,
              "event_presence_agreement": sum(a == b for a, b in pairs) / dual if dual else None,
              "event_presence_kappa": cohens_kappa(pairs),
              "event_type_set_agreement": type_set_exact / dual if dual else None,
              "by_split": by_split, "gold_ready": False,
              "interpretation": "Agreement is measured only on items labeled independently by both reviewers. Exact agreement is not automatically promoted to gold; disagreements need a separate human adjudication."}
    return result, conflicts, pending


def render_report(result: dict[str, Any]) -> str:
    lines = ["# 双人独立语义标注对照", "", f"状态：`{result['status']}`。共 {result['pack_items']} 条；双人完成 {result['dual_reviewed_items']} 条；待完成 {result['pending_items']} 条；分歧 {result['conflict_items']} 条。", "",
             f"事件清单完全一致率：{result['exact_event_agreement'] if result['exact_event_agreement'] is not None else '无可评估条目'}。",
             f"事件有无一致率：{result['event_presence_agreement'] if result['event_presence_agreement'] is not None else '无可评估条目'}；Cohen κ：{result['event_presence_kappa'] if result['event_presence_kappa'] is not None else '未定义'}。", "",
             "以上指标仅覆盖两位审核者均标为 `labeled` 的条目。κ 在没有类别变异时未定义；抽样按关键词分层，这些比例不是总体事件发生率。", "",
             "分歧保存在 `disagreements.jsonl`，缺失或未完成条目保存在 `pending_items.jsonl`。完全一致的项目也不会被程序自动写成金标准；最终标签必须经单独的人审裁定和签署。", ""]
    return "\n".join(lines)


def run_comparison(pack_dir: Path, reviewer_a: Path, reviewer_b: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, reviewer_a, reviewer_b, output_dir = (p.resolve() for p in (pack_dir, reviewer_a, reviewer_b, output_dir))
    if reviewer_a == reviewer_b:
        raise ValueError("independent reviewer files must be distinct")
    if output_dir == pack_dir or pack_dir in output_dir.parents or output_dir in pack_dir.parents:
        raise ValueError("comparison output must be separate from source annotation pack")
    items, pack_manifest = load_pack(pack_dir)
    hashes = {p: file_sha256(p) for p in (pack_dir / "annotation_manifest.json",
                                          pack_dir / "annotation_items.jsonl", reviewer_a, reviewer_b)}
    result, conflicts, pending = compare_reviewers(items, read_jsonl(reviewer_a), read_jsonl(reviewer_b))
    if any(file_sha256(p) != h for p, h in hashes.items()):
        raise RuntimeError("review inputs changed during comparison")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty comparison output directory")
    if any(output_dir == p or output_dir in p.parents for p in hashes):
        raise ValueError("comparison output must not contain reviewer input files")
    result["annotation_experiment_id"] = pack_manifest["experiment_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        (staging / "comparison_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "comparison_report.md").write_text(render_report(result), encoding="utf-8")
        _write_jsonl(staging / "disagreements.jsonl", conflicts)
        _write_jsonl(staging / "pending_items.jsonl", pending)
        _write_jsonl(staging / "adjudication_template.jsonl", [
            {"item_id": item["item_id"], "source_text_sha256": item["source_text_sha256"],
             "annotator_id": None, "status": "unlabeled", "events": [], "notes": ""}
            for item in sorted(items.values(), key=lambda item: item["item_id"])])
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "inputs": {str(p): h for p, h in hashes.items()}, "code_sha256": file_sha256(Path(__file__)),
                    "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "comparison_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def validate_adjudication(items: dict[str, dict[str, Any]], comparison: dict[str, Any],
                         final_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if (comparison["pack_items"] != len(items) or comparison["dual_reviewed_items"] != len(items)
            or comparison["pending_items"] != 0):
        raise ValueError("complete independent dual review is required before finalization")
    audit = validate_labels(items, final_rows)
    if audit["label_rows"] != len(items) or audit["reviewed_items"] != len(items):
        raise ValueError("final adjudication must label every source item")
    identities = {row["annotator_id"] for row in final_rows}
    reviewers = {value for value in comparison["reviewer_ids"].values() if value is not None}
    if len(identities) != 1 or next(iter(identities)) in reviewers:
        raise ValueError("final adjudicator identity must be distinct from both independent reviewers")
    conflict_ids = set(comparison["conflict_item_ids"])
    if any(not row["notes"].strip() for row in final_rows if row["item_id"] in conflict_ids):
        raise ValueError("every disputed item requires a written adjudication reason")
    return {"items": len(items), "adjudicator_id": next(iter(identities)),
            "resolved_conflicts": len(conflict_ids), "status": "all_items_adjudicated"}


def finalize_labels(pack_dir: Path, comparison_dir: Path, final_path: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, comparison_dir, final_path, output_dir = (p.resolve() for p in
                                                         (pack_dir, comparison_dir, final_path, output_dir))
    if any(output_dir == p or p in output_dir.parents or output_dir in p.parents for p in (pack_dir, comparison_dir)):
        raise ValueError("gold output must be separate from source and comparison directories")
    items, pack_manifest = load_pack(pack_dir)
    comparison_manifest_path = comparison_dir / "comparison_manifest.json"
    comparison_manifest = json.loads(comparison_manifest_path.read_text(encoding="utf-8"))
    if comparison_manifest["pipeline_version"] != VERSION:
        raise ValueError("comparison version differs from finalizer")
    input_hashes = {Path(path): digest for path, digest in comparison_manifest["inputs"].items()}
    if any(file_sha256(path) != digest for path, digest in input_hashes.items()):
        raise ValueError("comparison reviewer inputs changed since comparison")
    for name, info in comparison_manifest["artifacts"].items():
        if file_sha256(comparison_dir / name) != info["sha256"]:
            raise ValueError("comparison artifact hash mismatch")
    comparison = json.loads((comparison_dir / "comparison_results.json").read_text(encoding="utf-8"))
    if comparison["annotation_experiment_id"] != pack_manifest["experiment_id"]:
        raise ValueError("comparison and source annotation pack differ")
    final_hash = file_sha256(final_path)
    final_rows = read_jsonl(final_path)
    result = validate_adjudication(items, comparison, final_rows)
    if file_sha256(final_path) != final_hash:
        raise RuntimeError("adjudication file changed during validation")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty gold output directory")
    if output_dir == final_path or output_dir in final_path.parents:
        raise ValueError("gold output cannot contain its final input")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        _write_jsonl(staging / "gold_labels.jsonl", sorted(final_rows, key=lambda row: row["item_id"]))
        (staging / "gold_report.md").write_text(
            f"# 人审裁定标签\n\n已验证 {result['items']} 条逐项裁定，含 {result['resolved_conflicts']} 条有书面说明的分歧。"
            "输出仅确认文件格式、双人覆盖、独立身份字段及裁定完整性；无法由程序证明标注者确为真人或判断语义正确性。\n",
            encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "annotation_experiment_id": pack_manifest["experiment_id"],
                    "comparison_manifest_sha256": file_sha256(comparison_manifest_path),
                    "final_input_sha256": final_hash, "code_sha256": file_sha256(Path(__file__)),
                    "review": result, "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "gold_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="make two blinded, empty reviewer packets")
    prepare.add_argument("pack_dir", type=Path)
    prepare.add_argument("--output-dir", type=Path, required=True)
    compare = sub.add_parser("compare", help="audit two independent reviewer label files")
    compare.add_argument("pack_dir", type=Path)
    compare.add_argument("--reviewer-a", type=Path, required=True)
    compare.add_argument("--reviewer-b", type=Path, required=True)
    compare.add_argument("--output-dir", type=Path, required=True)
    finalize = sub.add_parser("finalize", help="validate a separately signed adjudication file")
    finalize.add_argument("pack_dir", type=Path)
    finalize.add_argument("--comparison-dir", type=Path, required=True)
    finalize.add_argument("--final-labels", type=Path, required=True)
    finalize.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_packets(args.pack_dir, args.output_dir)
        print(json.dumps({"items": result["items"], "artifacts": list(result["artifacts"])}, ensure_ascii=False))
    elif args.command == "compare":
        result = run_comparison(args.pack_dir, args.reviewer_a, args.reviewer_b, args.output_dir)
        print(json.dumps({"status": result["status"], "dual_reviewed_items": result["dual_reviewed_items"],
                          "conflict_items": result["conflict_items"]}, ensure_ascii=False))
    else:
        result = finalize_labels(args.pack_dir, args.comparison_dir, args.final_labels, args.output_dir)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
