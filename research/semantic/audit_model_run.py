"""Audit completeness and provenance of a saved semantic model run."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .signal_validation import load_pack, read_jsonl
from .prompt_contract import prompt_path
from .parse_model_outputs import normalize_rows
from ..data_pipeline.provenance import file_sha256


VERSION = "semantic-model-audit-v2"


def _artifact_hash(directory: Path, manifest: dict[str, Any], name: str) -> str:
    info = manifest.get("artifacts", {}).get(name)
    path = directory / name
    if not isinstance(info, dict) or not path.exists() or info.get("sha256") != file_sha256(path):
        raise ValueError(f"artifact hash mismatch: {name}")
    return info["sha256"]


def _check_ids(rows: list[dict[str, Any]], items: dict[str, dict[str, Any]], label: str) -> None:
    ids = [row.get("item_id") for row in rows]
    if any(item_id not in items for item_id in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"{label} contains unknown or duplicate item IDs")


def audit_model_run(pack_dir: Path, raw_dir: Path, normalized_dir: Path | None = None) -> dict[str, Any]:
    pack_dir, raw_dir = pack_dir.resolve(), raw_dir.resolve()
    items, pack_manifest = load_pack(pack_dir)
    raw_manifest_path = raw_dir / "model_run_manifest.json"
    raw_path = raw_dir / "model_raw_outputs.jsonl"
    if not raw_manifest_path.exists() or not raw_path.exists():
        raise ValueError("raw model run must contain model_run_manifest.json and model_raw_outputs.jsonl")
    raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
    raw_hash = _artifact_hash(raw_dir, raw_manifest, raw_path.name)
    declared_inputs = raw_manifest.get("input_sha256", {})
    if raw_manifest.get("pack_experiment_id") != pack_manifest["experiment_id"]:
        raise ValueError("raw model run belongs to a different annotation pack")
    if (declared_inputs.get("annotation_manifest") != file_sha256(pack_dir / "annotation_manifest.json")
            or declared_inputs.get("annotation_items") != file_sha256(pack_dir / "annotation_items.jsonl")):
        raise ValueError("raw model run input hashes differ from annotation pack")
    raw_rows = read_jsonl(raw_path)
    _check_ids(raw_rows, items, "raw model output")
    version = raw_manifest.get("prompt_version")
    if declared_inputs.get("prompt") != file_sha256(prompt_path(version)):
        raise ValueError("raw model run prompt hash differs from versioned prompt")
    required_raw = {"item_id", "source_text_sha256", "model_id", "prompt_version", "raw_response"}
    for row in raw_rows:
        if (set(row) != required_raw or row["source_text_sha256"] != items[row["item_id"]]["source_text_sha256"]
                or row["model_id"] != raw_manifest.get("model_id") or row["prompt_version"] != version
                or not isinstance(row["raw_response"], str)):
            raise ValueError("raw model row provenance differs from annotation pack or run manifest")
    requested, completed, remaining, failures = (
        raw_manifest.get(field) for field in ("requested_rows", "model_rows", "remaining_rows", "request_failures"))
    if (any(type(value) is not int for value in (requested, completed, remaining, failures))
            or not 0 <= completed <= requested <= len(items) or remaining != requested - completed
            or not 0 <= failures <= completed):
        raise ValueError("raw model manifest has inconsistent scope or counts")
    if completed != len(raw_rows):
        raise ValueError("raw model manifest row count differs from JSONL")
    failed_requests = 0
    for row in raw_rows:
        try:
            response = json.loads(row["raw_response"])
        except ValueError:
            continue
        failed_requests += isinstance(response, dict) and "runner_error" in response
    if failures != failed_requests:
        raise ValueError("raw model request failure count differs from responses")
    raw_status = "complete" if completed == requested and remaining == 0 and failures == 0 else "incomplete_or_failed"
    result: dict[str, Any] = {
        "pipeline_version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pack_experiment_id": pack_manifest["experiment_id"],
        "model_id": raw_manifest.get("model_id"),
        "prompt_version": raw_manifest.get("prompt_version"),
        "provider_base_url": raw_manifest.get("provider_base_url"),
        "scope": {"pack_items": len(items), "requested_rows": requested,
                  "full_pack_requested": requested == len(items)},
        "raw": {"rows": completed, "remaining_rows": remaining,
                "request_failures": failures, "status": raw_status, "sha256": raw_hash},
        "normalized": {"status": "not_provided"},
        "scoring": {"status": "unavailable_without_adjudicated_gold",
                    "accuracy_claim_allowed": False},
    }
    if normalized_dir is not None:
        normalized_dir = normalized_dir.resolve()
        manifest_path = normalized_dir / "normalization_manifest.json"
        prediction_path = normalized_dir / "model_predictions.jsonl"
        if not manifest_path.exists() or not prediction_path.exists():
            raise ValueError("normalized output must contain normalization_manifest.json and model_predictions.jsonl")
        normalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        prediction_hash = _artifact_hash(normalized_dir, normalized_manifest, prediction_path.name)
        normalized_inputs = normalized_manifest.get("input_sha256", {})
        if (normalized_manifest.get("pack_experiment_id") != pack_manifest["experiment_id"]
                or normalized_inputs.get("raw_outputs") != raw_hash):
            raise ValueError("normalized output is not bound to this raw model run")
        prediction_rows = read_jsonl(prediction_path)
        _check_ids(prediction_rows, items, "normalized prediction")
        normalized_count = int(normalized_manifest.get("model_rows", -1))
        parse_errors = int(normalized_manifest.get("parse_errors", -1))
        if normalized_count != len(prediction_rows):
            raise ValueError("normalization manifest row count differs from JSONL")
        expected_predictions = normalize_rows(items, raw_rows)
        if prediction_rows != expected_predictions:
            raise ValueError("normalized predictions differ from deterministic normalization of raw responses")
        if parse_errors != sum(row["parse_error"] is not None for row in prediction_rows):
            raise ValueError("normalization parse error count differs from predictions")
        result["normalized"] = {"rows": normalized_count, "parse_errors": parse_errors,
                                 "missing_predictions": len(items) - normalized_count,
                                 "sha256": prediction_hash,
                                 "status": "complete" if normalized_count == requested and parse_errors == 0 else "parse_errors_or_partial"}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("--normalized-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_model_run(args.pack_dir, args.raw_dir, args.normalized_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.resolve().write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
