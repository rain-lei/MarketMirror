"""Build two source-bound, self-contained offline pages for independent human review."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .review_workflow import SLOTS, VERSION as PACKET_VERSION, blinded_task
from .signal_validation import load_pack, read_jsonl, validate_labels

VERSION = "semantic-offline-review-interface-v1"
ASSETS = Path(__file__).with_name("review_assets")


def _separate_output(output: Path, source: Path) -> bool:
    return output != source and output not in source.parents and source not in output.parents


def _ordered_tasks(items: dict[str, dict[str, Any]], slot: str) -> list[dict[str, Any]]:
    ordered = sorted(items.values(), key=lambda item: hashlib.sha256(
        f"{PACKET_VERSION}:{slot}:{item['item_id']}".encode("utf-8")).hexdigest())
    return [blinded_task(item) for item in ordered]


def _blank_labels(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"item_id": task["item_id"], "source_text_sha256": task["source_text_sha256"],
             "annotator_id": None, "status": "unlabeled", "events": [], "notes": ""}
            for task in tasks]


def _embedded_json(payload: dict[str, Any]) -> str:
    # Do not let a source quote close the application/json script element.
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            .replace("<", r"\u003c").replace("\u2028", r"\u2028").replace("\u2029", r"\u2029"))


def _render(template: str, payload: dict[str, Any], assets: dict[str, str]) -> str:
    replacements = {"/* REVIEW_STYLE */": assets["review.css"],
                    "/* REVIEW_DATA */": _embedded_json(payload),
                    "/* REVIEW_CORE */": assets["review_core.js"],
                    "/* REVIEW_APP */": assets["review.js"]}
    for marker, value in replacements.items():
        if template.count(marker) != 1:
            raise ValueError(f"review template marker is missing or repeated: {marker}")
        template = template.replace(marker, value)
    return template


def build_review_interface(pack_dir: Path, packet_dir: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, packet_dir, output_dir = (path.resolve() for path in (pack_dir, packet_dir, output_dir))
    if not _separate_output(output_dir, pack_dir) or not _separate_output(output_dir, packet_dir):
        raise ValueError("review interface output must be separate from source directories")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty review interface directory")

    items, pack_manifest = load_pack(pack_dir)
    packet_manifest_path = packet_dir / "review_packet_manifest.json"
    packet_manifest = json.loads(packet_manifest_path.read_text(encoding="utf-8"))
    if packet_manifest.get("pipeline_version") != PACKET_VERSION:
        raise ValueError("review packet version differs from interface")
    if (packet_manifest.get("annotation_experiment_id") != pack_manifest["experiment_id"]
            or packet_manifest.get("annotation_manifest_sha256") != file_sha256(pack_dir / "annotation_manifest.json")
            or packet_manifest.get("code_sha256") != file_sha256(Path(__file__).with_name("review_workflow.py"))
            or packet_manifest.get("items") != len(items)):
        raise ValueError("review packet does not match its annotation source or code")

    expected_files = {f"{slot}_{kind}.jsonl" for slot in SLOTS for kind in ("tasks", "labels")}
    if set(packet_manifest.get("artifacts", {})) != expected_files:
        raise ValueError("review packet artifact list is incomplete or unexpected")
    inputs = {pack_dir / "annotation_manifest.json": file_sha256(pack_dir / "annotation_manifest.json"),
              packet_manifest_path: file_sha256(packet_manifest_path)}
    payloads = {}
    for slot in SLOTS:
        tasks_path = packet_dir / f"{slot}_tasks.jsonl"
        labels_path = packet_dir / f"{slot}_labels.jsonl"
        for path in (tasks_path, labels_path):
            digest = file_sha256(path)
            if packet_manifest["artifacts"][path.name]["sha256"] != digest:
                raise ValueError(f"review packet artifact hash mismatch: {path.name}")
            inputs[path] = digest
        tasks, labels = read_jsonl(tasks_path), read_jsonl(labels_path)
        if tasks != _ordered_tasks(items, slot) or labels != _blank_labels(tasks):
            raise ValueError(f"{slot} does not contain the expected blinded tasks and blank labels")
        validate_labels(items, labels)
        payloads[slot] = {"schema_version": VERSION, "slot": slot,
                          "annotation_experiment_id": pack_manifest["experiment_id"],
                          "tasks": tasks, "labels": labels}

    asset_names = ("review.html", "review.css", "review_core.js", "review.js")
    assets = {name: (ASSETS / name).read_text(encoding="utf-8") for name in asset_names}
    template = assets["review.html"]
    source_hashes = {str(path): digest for path, digest in inputs.items()}
    code_hashes = {name: file_sha256(ASSETS / name) for name in asset_names}
    code_hashes["review_interface.py"] = file_sha256(Path(__file__))
    if any(file_sha256(path) != digest for path, digest in inputs.items()):
        raise RuntimeError("review input changed while building interfaces")

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        for slot in SLOTS:
            (staging / f"{slot}.html").write_text(_render(template, payloads[slot], assets), encoding="utf-8")
        manifest = {"pipeline_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "annotation_experiment_id": pack_manifest["experiment_id"],
                    "items_per_reviewer": len(items), "input_sha256": source_hashes,
                    "code_sha256": code_hashes,
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()},
                    "interpretation": "Private offline review pages with blank labels; no human review or gold standard is implied."}
        (staging / "interface_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("packet_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_review_interface(args.pack_dir, args.packet_dir, args.output_dir)
    print(json.dumps({"items_per_reviewer": result["items_per_reviewer"],
                      "pages": list(result["artifacts"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
