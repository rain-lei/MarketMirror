"""Versioned company-confirmed quote protocol without changing archived v2 code.

The derived pack retains the same source items and pins a new prompt. Raw model
responses, normalized predictions and manifests live only under ignored outputs.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .annotation_pack import VERSION as PACK_VERSION, canonical_hash
from .quote_grounding import ground_event
from .run_model import _is_runner_failure
from .signal_validation import load_pack, read_jsonl, strict_json_loads, validate_predictions


VERSION = "wuhan-company-confirmed-v3"
PACK_VERSION_V3 = "wuhan-derived-annotation-pack-v3"
RUN_VERSION = "wuhan-company-confirmed-model-run-v3"
NORMAL_VERSION = "wuhan-company-confirmed-normalization-v3"
BASE_URL = "http://aigw.dlut.edu.cn/v1"
MODEL = "DeepSeek-V4-Flash-0731-W8A8"
PROMPT = Path(__file__).with_name("PROMPT_WUHAN_V3.md")
MODULE = Path(__file__).resolve()
SEMANTIC = MODULE.parent
RAW_NAME = "model_raw_outputs.jsonl"
RAW_MANIFEST = "model_run_manifest.json"
PREDICTIONS = "model_predictions.jsonl"
NORMAL_MANIFEST = "normalization_manifest.json"


def _hash_inputs(mapping: dict[str, str]) -> None:
    if not isinstance(mapping, dict) or any(file_sha256(Path(name)) != digest
                                            for name, digest in mapping.items()):
        raise ValueError("derived pack source input hash differs")


def _hash_code(mapping: dict[str, str]) -> None:
    if not isinstance(mapping, dict) or any(file_sha256(SEMANTIC / name) != digest
                                            for name, digest in mapping.items()):
        raise ValueError("derived pack source code hash differs")


def _identity(parent_id: str, parent_manifest_sha: str, parent_items_sha: str,
              prompt_sha: str, builder_sha: str) -> str:
    return canonical_hash({"version": PACK_VERSION_V3, "parent_experiment_id": parent_id,
                           "parent_manifest_sha256": parent_manifest_sha,
                           "parent_items_sha256": parent_items_sha,
                           "prompt_sha256": prompt_sha, "builder_sha256": builder_sha})


def derive_pack(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    source_dir, output_dir = source_dir.resolve(), output_dir.resolve()
    items, source = load_pack(source_dir)
    if (source.get("pipeline_version") != PACK_VERSION or source.get("snapshot_pipeline_version")
            != "policy-event-qna-snapshot-v1" or source.get("selection_rule")
            != "latest_company_confirmed_reply_per_asset_before_cutoff"
            or source.get("event_id") != "wuhan_date_only_conservative"
            or source.get("snapshot_as_of") != "2020-01-22T23:59:59.999999+08:00"
            or source.get("universe_size") != 126
            or len(items) != source.get("counts", {}).get("reply_items")
            or any(item["stage"] != "reply" or item["available_at"] > source["snapshot_as_of"]
                   for item in items.values())):
        raise ValueError("v3 protocol requires the fixed complete pre-event reply snapshot")
    _hash_inputs(source["input_sha256"])
    _hash_code(source["code_sha256"])
    parent_manifest = source_dir / "annotation_manifest.json"
    parent_items = source_dir / "annotation_items.jsonl"
    if (output_dir == source_dir or output_dir in source_dir.parents or source_dir in output_dir.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("derived pack needs a new directory separate from source")
    parent_manifest_sha, parent_items_sha = file_sha256(parent_manifest), file_sha256(parent_items)
    prompt_sha, builder_sha = file_sha256(PROMPT), file_sha256(MODULE)
    experiment_id = _identity(source["experiment_id"], parent_manifest_sha, parent_items_sha,
                              prompt_sha, builder_sha)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        shutil.copyfile(parent_items, staging / "annotation_items.jsonl")
        manifest = {**source,
                    "pipeline_version": PACK_VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "run_id": source["run_id"] + "_company_confirmed_v3",
                    "experiment_id": experiment_id,
                    "frozen_model_protocol": {"provider_base_url": BASE_URL, "model_id": MODEL,
                                              "prompt_version": VERSION, "prompt_sha256": prompt_sha,
                                              "temperature": 0.0},
                    "derived_protocol": {"version": PACK_VERSION_V3,
                                         "source_pack_directory": str(source_dir),
                                         "source_pack_experiment_id": source["experiment_id"],
                                         "source_manifest_sha256": parent_manifest_sha,
                                         "source_items_sha256": parent_items_sha,
                                         "prompt_sha256": prompt_sha,
                                         "builder_sha256": builder_sha},
                    "input_sha256": {**source["input_sha256"], str(parent_manifest): parent_manifest_sha,
                                     str(parent_items): parent_items_sha, str(PROMPT): prompt_sha},
                    "code_sha256": {**source["code_sha256"], MODULE.name: builder_sha},
                    "artifacts": {"annotation_items.jsonl": {"sha256": parent_items_sha}},
                    "limitations": [*source.get("limitations", []),
                                    "Prompt v3 was developed after viewing the previous 105 item review; this pack is not independent validation by itself."]}
        (staging / "annotation_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if file_sha256(parent_manifest) != parent_manifest_sha or file_sha256(parent_items) != parent_items_sha:
            raise RuntimeError("source pack changed during protocol derivation")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def verify_pack(pack_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    pack_dir = pack_dir.resolve()
    items, manifest = load_pack(pack_dir)
    derived = manifest.get("derived_protocol", {})
    protocol = manifest.get("frozen_model_protocol", {})
    source_dir = Path(derived.get("source_pack_directory", "")).resolve()
    source_items, source = load_pack(source_dir)
    expected_protocol = {"provider_base_url": BASE_URL, "model_id": MODEL,
                         "prompt_version": VERSION, "prompt_sha256": file_sha256(PROMPT),
                         "temperature": 0.0}
    if (derived.get("version") != PACK_VERSION_V3 or protocol != expected_protocol
            or derived.get("source_pack_experiment_id") != source["experiment_id"]
            or derived.get("source_manifest_sha256") != file_sha256(source_dir / "annotation_manifest.json")
            or derived.get("source_items_sha256") != file_sha256(source_dir / "annotation_items.jsonl")
            or derived.get("prompt_sha256") != expected_protocol["prompt_sha256"]
            or derived.get("builder_sha256") != file_sha256(MODULE)
            or manifest.get("experiment_id") != _identity(
                source["experiment_id"], derived["source_manifest_sha256"],
                derived["source_items_sha256"], derived["prompt_sha256"], derived["builder_sha256"])
            or len(items) != len(source_items) or items != source_items
            or file_sha256(pack_dir / "annotation_items.jsonl") != derived["source_items_sha256"]):
        raise ValueError("derived v3 pack differs from its frozen source or prompt")
    _hash_inputs(manifest["input_sha256"])
    _hash_code(manifest["code_sha256"])
    return items, manifest


def _verified_raw(pack_dir: Path, raw_dir: Path):
    items, pack = verify_pack(pack_dir)
    raw_dir = raw_dir.resolve()
    manifest_path, raw_path = raw_dir / RAW_MANIFEST, raw_dir / RAW_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hashes = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
              "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
              "prompt": file_sha256(PROMPT)}
    if (manifest.get("pipeline_version") != RUN_VERSION
            or manifest.get("pack_experiment_id") != pack["experiment_id"]
            or manifest.get("input_sha256") != hashes
            or manifest.get("provider_base_url") != BASE_URL
            or manifest.get("model_id") != MODEL or manifest.get("prompt_version") != VERSION
            or manifest.get("temperature") != 0.0
            or manifest.get("code_sha256") != {
                "wuhan_protocol_v3.py": file_sha256(MODULE),
                "wuhan_model_v3.py": file_sha256(MODULE.with_name("wuhan_model_v3.py")),
                "run_model.py": file_sha256(MODULE.with_name("run_model.py"))}
            or manifest.get("artifacts", {}).get(RAW_NAME, {}).get("sha256") != file_sha256(raw_path)):
        raise ValueError("v3 raw model manifest or artifact differs")
    rows = read_jsonl(raw_path)
    if (len(rows) != manifest.get("model_rows")
            or manifest.get("requested_rows") != len(items)
            or manifest.get("remaining_rows") != len(items) - len(rows)
            or manifest.get("request_failures") != sum(_is_runner_failure(row) for row in rows)
            or len({row["item_id"] for row in rows}) != len(rows)):
        raise ValueError("v3 raw model coverage differs")
    for row in rows:
        if (set(row) != {"item_id", "source_text_sha256", "model_id", "prompt_version", "raw_response"}
                or row["item_id"] not in items
                or row["source_text_sha256"] != items[row["item_id"]]["source_text_sha256"]
                or row["model_id"] != MODEL or row["prompt_version"] != VERSION
                or not isinstance(row["raw_response"], str)):
            raise ValueError("v3 raw model row differs from source")
    return items, pack, manifest, rows


def _normalize(items: dict[str, dict[str, Any]], rows: list[dict]) -> list[dict]:
    predictions = []
    for row in rows:
        base = {field: row[field] for field in ("item_id", "source_text_sha256", "model_id", "prompt_version")}
        try:
            payload = strict_json_loads(row["raw_response"])
            if not isinstance(payload, dict) or set(payload) != {"events"} or not isinstance(payload["events"], list):
                raise ValueError("response must contain only an events array")
            if any(not isinstance(event, dict) or any(
                    quote.get("source") != "reply" for quote in event.get("evidence_quotes", []))
                   for event in payload["events"]):
                raise ValueError("company-confirmed protocol requires reply-only evidence")
            events = [ground_event(event, items[row["item_id"]]) for event in payload["events"]]
            predictions.append({**base, "events": events, "parse_error": None})
        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as error:
            predictions.append({**base, "events": [], "parse_error": f"{type(error).__name__}: {error}"})
    validate_predictions(items, predictions)
    return predictions


def normalize_v3(pack_dir: Path, raw_dir: Path, output_dir: Path) -> dict:
    pack_dir, raw_dir, output_dir = pack_dir.resolve(), raw_dir.resolve(), output_dir.resolve()
    items, pack, raw_manifest, rows = _verified_raw(pack_dir, raw_dir)
    if raw_manifest["remaining_rows"] != 0:
        raise ValueError("normalization requires every frozen item")
    predictions = _normalize(items, rows)
    if (output_dir == pack_dir or output_dir == raw_dir
            or output_dir in pack_dir.parents or output_dir in raw_dir.parents
            or pack_dir in output_dir.parents or raw_dir in output_dir.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("v3 normalized output must be a new directory separate from inputs")
    inputs = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
              "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
              "raw_outputs": file_sha256(raw_dir / RAW_NAME),
              "model_run_manifest": file_sha256(raw_dir / RAW_MANIFEST)}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        with (staging / PREDICTIONS).open("w", encoding="utf-8") as handle:
            for row in predictions:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        manifest = {"pipeline_version": NORMAL_VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "pack_experiment_id": pack["experiment_id"], "input_sha256": inputs,
                    "model_rows": len(predictions),
                    "parse_errors": sum(row["parse_error"] is not None for row in predictions),
                    "model_ids": [MODEL], "prompt_versions": [VERSION],
                    "code_sha256": {"wuhan_protocol_v3.py": file_sha256(MODULE),
                                    "quote_grounding.py": file_sha256(MODULE.with_name("quote_grounding.py")),
                                    "signal_validation.py": file_sha256(MODULE.with_name("signal_validation.py"))},
                    "evidence_protocol": "reply-only-unique-exact-quote-v3",
                    "artifacts": {PREDICTIONS: {"sha256": file_sha256(staging / PREDICTIONS)}}}
        (staging / NORMAL_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def audit_v3(pack_dir: Path, raw_dir: Path, normal_dir: Path) -> dict:
    pack_dir, raw_dir, normal_dir = pack_dir.resolve(), raw_dir.resolve(), normal_dir.resolve()
    items, pack, raw_manifest, rows = _verified_raw(pack_dir, raw_dir)
    manifest = json.loads((normal_dir / NORMAL_MANIFEST).read_text(encoding="utf-8"))
    predictions_path = normal_dir / PREDICTIONS
    expected_inputs = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                       "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                       "raw_outputs": file_sha256(raw_dir / RAW_NAME),
                       "model_run_manifest": file_sha256(raw_dir / RAW_MANIFEST)}
    predictions = read_jsonl(predictions_path)
    if (manifest.get("pipeline_version") != NORMAL_VERSION
            or manifest.get("pack_experiment_id") != pack["experiment_id"]
            or manifest.get("input_sha256") != expected_inputs
            or manifest.get("code_sha256") != {
                "wuhan_protocol_v3.py": file_sha256(MODULE),
                "quote_grounding.py": file_sha256(MODULE.with_name("quote_grounding.py")),
                "signal_validation.py": file_sha256(MODULE.with_name("signal_validation.py"))}
            or manifest.get("artifacts", {}).get(PREDICTIONS, {}).get("sha256") != file_sha256(predictions_path)
            or manifest.get("model_rows") != len(items)
            or predictions != _normalize(items, rows)
            or manifest.get("parse_errors") != sum(row["parse_error"] is not None for row in predictions)):
        raise ValueError("v3 normalized predictions differ from raw response reconstruction")
    return {"pack_experiment_id": pack["experiment_id"], "items": len(items),
            "model_rows": len(rows), "request_failures": raw_manifest["request_failures"],
            "parse_errors": manifest["parse_errors"],
            "reply_only_evidence": all(span["source"] == "reply" for row in predictions
                                       for event in row["events"] for span in event["evidence_spans"]),
            "raw_sha256": expected_inputs["raw_outputs"], "predictions_sha256": file_sha256(predictions_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("derive")
    build.add_argument("source_dir", type=Path)
    build.add_argument("--output-dir", type=Path, required=True)
    check = sub.add_parser("preflight")
    check.add_argument("pack_dir", type=Path)
    normalize = sub.add_parser("normalize")
    normalize.add_argument("pack_dir", type=Path)
    normalize.add_argument("raw_dir", type=Path)
    normalize.add_argument("--output-dir", type=Path, required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("pack_dir", type=Path)
    audit.add_argument("raw_dir", type=Path)
    audit.add_argument("normal_dir", type=Path)
    args = parser.parse_args()
    if args.action == "derive":
        result = derive_pack(args.source_dir, args.output_dir)
        print(json.dumps({"experiment_id": result["experiment_id"], "items": result["counts"]["items"]},
                         ensure_ascii=False))
    elif args.action == "preflight":
        items, manifest = verify_pack(args.pack_dir)
        print(json.dumps({"experiment_id": manifest["experiment_id"], "items": len(items),
                          "protocol": manifest["frozen_model_protocol"]}, ensure_ascii=False))
    elif args.action == "normalize":
        result = normalize_v3(args.pack_dir, args.raw_dir, args.output_dir)
        print(json.dumps({"model_rows": result["model_rows"], "parse_errors": result["parse_errors"]},
                         ensure_ascii=False))
    else:
        print(json.dumps(audit_v3(args.pack_dir, args.raw_dir, args.normal_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
