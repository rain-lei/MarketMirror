"""Normalize saved model replies to source-bound signals, retaining every parse failure."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .signal_validation import load_pack, read_jsonl, strict_json_loads, validate_event, validate_predictions
from ..data_pipeline.provenance import file_sha256

VERSION = "semantic-model-normalization-v1"


def normalize_rows(items: dict[str, dict[str, Any]], raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records, seen = [], set()
    required = {"item_id", "source_text_sha256", "model_id", "prompt_version", "raw_response"}
    for row in raw_rows:
        if set(row) != required or row["item_id"] not in items or row["item_id"] in seen:
            raise ValueError("raw model output fields, item identity or uniqueness are invalid")
        seen.add(row["item_id"])
        item = items[row["item_id"]]
        if row["source_text_sha256"] != item["source_text_sha256"]:
            raise ValueError("model output is bound to a different source text")
        for field in ("model_id", "prompt_version"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"raw model output requires {field}")
        if not isinstance(row["raw_response"], str):
            raise ValueError("raw_response must be an exact string")
        base = {k: row[k] for k in ("item_id", "source_text_sha256", "model_id", "prompt_version")}
        try:
            payload = strict_json_loads(row["raw_response"])
            if not isinstance(payload, dict) or set(payload) != {"events"} or not isinstance(payload["events"], list):
                raise ValueError("response must be a JSON object with only an events array")
            for event in payload["events"]:
                validate_event(event, item)
            records.append({**base, "events": payload["events"], "parse_error": None})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            records.append({**base, "events": [], "parse_error": f"{type(error).__name__}: {error}"})
    validate_predictions(items, records)
    return records


def normalize_file(pack_dir: Path, raw_path: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, raw_path, output_dir = pack_dir.resolve(), raw_path.resolve(), output_dir.resolve()
    items, pack_manifest = load_pack(pack_dir)
    input_hashes = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                    "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                    "raw_outputs": file_sha256(raw_path)}
    normalized = normalize_rows(items, read_jsonl(raw_path))
    if output_dir == pack_dir or pack_dir in output_dir.parents or output_dir == raw_path.parent:
        raise ValueError("normalization output must be separate from input locations")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty normalization output directory")
    if (input_hashes["raw_outputs"] != file_sha256(raw_path)
            or input_hashes["annotation_manifest"] != file_sha256(pack_dir / "annotation_manifest.json")
            or input_hashes["annotation_items"] != file_sha256(pack_dir / "annotation_items.jsonl")):
        raise RuntimeError("model or annotation input changed during normalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        with (staging / "model_predictions.jsonl").open("w", encoding="utf-8") as handle:
            for row in normalized:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "pack_experiment_id": pack_manifest["experiment_id"], "input_sha256": input_hashes,
                    "model_rows": len(normalized), "parse_errors": sum(r["parse_error"] is not None for r in normalized),
                    "model_ids": sorted({r["model_id"] for r in normalized}),
                    "prompt_versions": sorted({r["prompt_version"] for r in normalized}),
                    "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in
                                    ("parse_model_outputs.py", "signal_validation.py")},
                    "artifacts": {"model_predictions.jsonl": {"sha256": file_sha256(staging / "model_predictions.jsonl")}}}
        (staging / "normalization_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = normalize_file(args.pack_dir, args.raw, args.output_dir)
    print(json.dumps({key: result[key] for key in ("model_rows", "parse_errors", "model_ids", "prompt_versions")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
