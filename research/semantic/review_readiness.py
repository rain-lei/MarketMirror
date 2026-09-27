"""Audit that the frozen holdout's human-review package is ready to hand off."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .review_workflow import SLOTS, blinded_task
from .signal_validation import load_pack, read_jsonl, validate_labels

VERSION = "semantic-review-readiness-v1"
INTERFACE_VERSION = "semantic-offline-review-interface-v1"
PACKET_VERSION = "semantic-double-review-v1"
ROOT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).with_name("review_assets")
FORBIDDEN_TASK_KEYS = {"stock_code", "split", "selection_stratum", "qa_id", "model_id", "prompt_version"}


def _ordered_tasks(items: dict[str, dict[str, Any]], slot: str) -> list[dict[str, Any]]:
    ordered = sorted(items.values(), key=lambda item: hashlib.sha256(
        f"{PACKET_VERSION}:{slot}:{item['item_id']}".encode("utf-8")).hexdigest())
    return [blinded_task(item) for item in ordered]


def _blank_labels(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"item_id": task["item_id"], "source_text_sha256": task["source_text_sha256"],
             "annotator_id": None, "status": "unlabeled", "events": [], "notes": ""}
            for task in tasks]


def _assert_artifacts(directory: Path, manifest: dict[str, Any], expected: set[str]) -> None:
    if set(manifest.get("artifacts", {})) != expected:
        raise ValueError(f"artifact set differs in {directory.name}")
    for name, info in manifest["artifacts"].items():
        path = directory / name
        if not path.is_file() or file_sha256(path) != info.get("sha256"):
            raise ValueError(f"artifact hash mismatch: {path}")


def _assert_interface_page(path: Path, slot: str, tasks: list[dict[str, Any]],
                          labels: list[dict[str, Any]]) -> None:
    html = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', html, re.S)
    if match is None:
        raise ValueError(f"review data is missing from {path.name}")
    payload = json.loads(match.group(1))
    if (payload.get("schema_version") != INTERFACE_VERSION
            or payload.get("slot") != slot
            or payload.get("tasks") != tasks
            or payload.get("labels") != labels):
        raise ValueError(f"review page payload differs from blank {slot} packet")
    if any(key in payload for key in FORBIDDEN_TASK_KEYS):
        raise ValueError(f"review page exposes a hidden task field: {path.name}")
    if any(f'"{key}"' in match.group(1) for key in FORBIDDEN_TASK_KEYS):
        raise ValueError(f"review page embeds a hidden task field: {path.name}")


def audit_review_package(pack_dir: Path, packet_dir: Path, interface_dir: Path) -> dict[str, Any]:
    """Verify source binding, blank labels, independent ordering, and page payloads."""
    pack_dir, packet_dir, interface_dir = (path.resolve() for path in (pack_dir, packet_dir, interface_dir))
    items, pack_manifest = load_pack(pack_dir)
    if len(items) != 128:
        raise ValueError("the frozen H2 review package must contain 128 items")

    packet_manifest_path = packet_dir / "review_packet_manifest.json"
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    if (packet_manifest.get("pipeline_version") != PACKET_VERSION
            or packet_manifest.get("annotation_experiment_id") != pack_manifest["experiment_id"]
            or packet_manifest.get("annotation_manifest_sha256") != file_sha256(pack_dir / "annotation_manifest.json")
            or packet_manifest.get("code_sha256") != file_sha256(ROOT / "research/semantic/review_workflow.py")
            or packet_manifest.get("items") != len(items)):
        raise ValueError("review packet is not bound to the current frozen holdout")
    expected_files = {f"{slot}_{kind}.jsonl" for slot in SLOTS for kind in ("tasks", "labels")}
    _assert_artifacts(packet_dir, packet_manifest, expected_files)

    packet_payloads: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for slot in SLOTS:
        tasks = read_jsonl(packet_dir / f"{slot}_tasks.jsonl")
        labels = read_jsonl(packet_dir / f"{slot}_labels.jsonl")
        expected_tasks = _ordered_tasks(items, slot)
        expected_labels = _blank_labels(expected_tasks)
        if tasks != expected_tasks or labels != expected_labels:
            raise ValueError(f"{slot} packet is not the expected blank blinded packet")
        audit = validate_labels(items, labels)
        if audit["reviewed_items"] != 0 or audit["unlabeled_items"] != len(items):
            raise ValueError(f"{slot} packet contains human labels")
        packet_payloads[slot] = (tasks, labels)

    interface_manifest_path = interface_dir / "interface_manifest.json"
    interface_manifest = json.loads(interface_manifest_path.read_text(encoding="utf-8"))
    if (interface_manifest.get("pipeline_version") != INTERFACE_VERSION
            or interface_manifest.get("annotation_experiment_id") != pack_manifest["experiment_id"]
            or interface_manifest.get("items_per_reviewer") != len(items)):
        raise ValueError("review interface is not bound to the current frozen holdout")
    _assert_artifacts(interface_dir, interface_manifest, {f"{slot}.html" for slot in SLOTS})

    expected_inputs = {pack_dir / "annotation_manifest.json": file_sha256(pack_dir / "annotation_manifest.json"),
                       packet_manifest_path: file_sha256(packet_manifest_path)}
    for slot in SLOTS:
        for suffix in ("tasks.jsonl", "labels.jsonl"):
            path = packet_dir / f"{slot}_{suffix}"
            expected_inputs[path] = file_sha256(path)
    declared_inputs = interface_manifest.get("input_sha256", {})
    if {Path(path).resolve(): digest for path, digest in declared_inputs.items()} != expected_inputs:
        raise ValueError("review interface input hash map differs from its packet")

    expected_code = {name: file_sha256(ASSETS / name)
                     for name in ("review.html", "review.css", "review_core.js", "review.js")}
    expected_code["review_interface.py"] = file_sha256(ROOT / "research/semantic/review_interface.py")
    if interface_manifest.get("code_sha256") != expected_code:
        raise ValueError("review interface code hashes differ from current source")
    for slot in SLOTS:
        tasks, labels = packet_payloads[slot]
        _assert_interface_page(interface_dir / f"{slot}.html", slot, tasks, labels)

    return {"pipeline_version": VERSION, "status": "ready_for_human_review",
            "items": len(items), "reviewer_slots": len(SLOTS),
            "reviewed_items": 0, "blank_label_rows_per_reviewer": len(items),
            "interface_pages": len(SLOTS), "gold_ready": False,
            "interpretation": "The frozen package is ready for two independent human reviewers; no semantic accuracy or gold standard is established."}


def run_readiness(pack_dir: Path, packet_dir: Path, interface_dir: Path,
                  output_dir: Path) -> dict[str, Any]:
    pack_dir, packet_dir, interface_dir, output_dir = (path.resolve() for path in
                                                       (pack_dir, packet_dir, interface_dir, output_dir))
    sources = (pack_dir, packet_dir, interface_dir)
    if output_dir in sources or any(output_dir in p.parents or p in output_dir.parents for p in sources):
        raise ValueError("readiness output must be separate from all review inputs")
    result = audit_review_package(pack_dir, packet_dir, interface_dir)
    inputs = {}
    for directory, names in ((pack_dir, ("annotation_manifest.json",)),
                             (packet_dir, ("review_packet_manifest.json", "reviewer_a_tasks.jsonl",
                                           "reviewer_a_labels.jsonl", "reviewer_b_tasks.jsonl", "reviewer_b_labels.jsonl")),
                             (interface_dir, ("interface_manifest.json", "reviewer_a.html", "reviewer_b.html"))):
        inputs.update({directory / name: file_sha256(directory / name) for name in names})
    code = {"review_readiness.py": file_sha256(Path(__file__)),
            "review_workflow.py": file_sha256(ROOT / "research/semantic/review_workflow.py"),
            "review_interface.py": file_sha256(ROOT / "research/semantic/review_interface.py")}
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty review readiness output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        result_path = staging / "review_readiness.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = staging / "review_readiness_report.md"
        report.write_text("# 下半年留出包人工审核准备状态\n\n"
                          f"状态：`{result['status']}`。固定条目 {result['items']} 条；"
                          f"双人审核位 {result['reviewer_slots']} 个；当前已审核 {result['reviewed_items']} 条。\n\n"
                          "该检查只证明来源绑定、空白标签、双人独立排序、页面脱敏和哈希一致；不证明语义准确率，也不生成金标准。\n",
                          encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "input_sha256": {str(path): digest for path, digest in sorted(inputs.items())},
                    "code_sha256": code,
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()}}
        (staging / "review_readiness_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("packet_dir", type=Path)
    parser.add_argument("interface_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_readiness(args.pack_dir, args.packet_dir, args.interface_dir, args.output_dir)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
