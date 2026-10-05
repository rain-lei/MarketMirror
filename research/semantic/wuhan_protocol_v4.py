"""Freeze and verify the v4 prompt package for the fourth Wuhan cohort."""

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
from .prepare_wuhan_v4_holdout import (
    PROMPT,
    ROOT,
    SNAPSHOT_NAME,
    UNIVERSE_NAME,
    __file__ as SELECTION_MODULE_PATH,
    verify_existing as verify_cohort,
)
from .signal_validation import load_pack


VERSION = "wuhan-company-confirmed-v4"
DERIVED_VERSION = "wuhan-derived-annotation-pack-v4"
BASE_URL = "http://aigw.dlut.edu.cn/v1"
MODEL = "DeepSeek-V4-Flash-0731-W8A8"
MODULE = Path(__file__).resolve()
SOURCE_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v4_holdout_source_v1"
PACK_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v4_holdout_protocol_v4_v2"
RAW_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v4_holdout_model_v4_v1"
NORMAL_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v4_holdout_normalized_v1"
RAW_NAME = "model_raw_outputs.jsonl"
RAW_MANIFEST = "model_run_manifest.json"
PREDICTIONS = "model_predictions.jsonl"
NORMAL_MANIFEST = "normalization_manifest.json"


def _identity(parent_id: str, parent_manifest_hash: str, parent_items_hash: str,
              prompt_hash: str, module_hash: str, selection_module_hash: str) -> str:
    return canonical_hash({"version": DERIVED_VERSION,
                           "parent_experiment_id": parent_id,
                           "parent_manifest_sha256": parent_manifest_hash,
                           "parent_items_sha256": parent_items_hash,
                           "prompt_sha256": prompt_hash,
                           "builder_sha256": module_hash,
                           "selection_builder_sha256": selection_module_hash})


def _verify_source(source_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if source_dir.resolve() != SOURCE_DIR.resolve():
        raise ValueError("v4 protocol is restricted to the frozen fourth cohort source snapshot")
    verify_cohort(ROOT / "research/configs" / UNIVERSE_NAME,
                  ROOT / "research/configs" / SNAPSHOT_NAME)
    items, source = load_pack(source_dir)
    config = source.get("config", {})
    universe = source.get("universe_size")
    if (source.get("pipeline_version") != PACK_VERSION
            or source.get("snapshot_pipeline_version") != "policy-event-qna-snapshot-v1"
            or source.get("selection_rule") != "latest_company_confirmed_reply_per_asset_before_cutoff"
            or config.get("run_id") != "wuhan_pre_event_fresh_v4_holdout_2020_v1"
            or config.get("universe_config") != UNIVERSE_NAME
            or universe != 126 or len(items) != source.get("counts", {}).get("reply_items")
            or not items
            or any(item.get("stage") != "reply"
                   or item.get("available_at", "") > source.get("snapshot_as_of", "")
                   for item in items.values())):
        raise ValueError("v4 source pack is incomplete or differs from its frozen pre-event cohort")
    for path_text, expected in source.get("input_sha256", {}).items():
        path = Path(path_text)
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError(f"v4 source input hash differs: {path.name}")
    for name, expected in source.get("code_sha256", {}).items():
        path = MODULE.with_name(name)
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError(f"v4 source builder hash differs: {name}")
    selected_codes = {item["stock_code"] for item in items.values()}
    if len(selected_codes) != len(items):
        raise ValueError("v4 snapshot must contain at most one reply for each company")
    return items, source


def derive_pack(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    source_dir, output_dir = source_dir.resolve(), output_dir.resolve()
    items, source = _verify_source(source_dir)
    outputs_root = (ROOT / "research_outputs").resolve()
    if (output_dir == source_dir or output_dir in source_dir.parents
            or source_dir in output_dir.parents
            or output_dir == outputs_root or outputs_root not in output_dir.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("v4 derived pack must be a new directory separate from source")
    source_manifest = source_dir / "annotation_manifest.json"
    source_items = source_dir / "annotation_items.jsonl"
    parent_manifest_hash = file_sha256(source_manifest)
    parent_items_hash = file_sha256(source_items)
    prompt_hash = file_sha256(PROMPT)
    module_hash = file_sha256(MODULE)
    selection_module_hash = file_sha256(Path(SELECTION_MODULE_PATH))
    experiment_id = _identity(source["experiment_id"], parent_manifest_hash,
                              parent_items_hash, prompt_hash, module_hash, selection_module_hash)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        shutil.copyfile(source_items, stage / "annotation_items.jsonl")
        manifest = {
            **source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": source["run_id"] + "_company_confirmed_v4",
            "experiment_id": experiment_id,
            "frozen_model_protocol": {"provider_base_url": BASE_URL,
                                      "model_id": MODEL,
                                      "prompt_version": VERSION,
                                      "prompt_sha256": prompt_hash,
                                      "temperature": 0.0},
            "derived_protocol": {"version": DERIVED_VERSION,
                                 "source_pack_directory": str(source_dir),
                                 "source_pack_experiment_id": source["experiment_id"],
                                 "source_manifest_sha256": parent_manifest_hash,
                                 "source_items_sha256": parent_items_hash,
                                 "prompt_sha256": prompt_hash,
                                 "builder_sha256": module_hash,
                                 "selection_builder_sha256": selection_module_hash},
            "input_sha256": {**source["input_sha256"],
                             str(source_manifest): parent_manifest_hash,
                             str(source_items): parent_items_hash,
                             str(PROMPT): prompt_hash,
                             str(Path(SELECTION_MODULE_PATH)): selection_module_hash},
            "code_sha256": {**source["code_sha256"], MODULE.name: module_hash},
            "artifacts": {"annotation_items.jsonl": {"sha256": parent_items_hash}},
            "limitations": [*source.get("limitations", []),
                            "v4 prompt was developed from the consumed v3 cohort; evaluation uses a separate fourth company sample.",
                            "Reference review is a single AI assistant review, not human-adjudicated ground truth."]}
        (stage / "annotation_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if (file_sha256(source_manifest) != parent_manifest_hash
                or file_sha256(source_items) != parent_items_hash
                or file_sha256(PROMPT) != prompt_hash
                or file_sha256(MODULE) != module_hash):
            raise RuntimeError("v4 source or prompt changed while deriving the frozen pack")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def verify_pack(pack_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    pack_dir = pack_dir.resolve()
    items, pack = load_pack(pack_dir)
    derived = pack.get("derived_protocol", {})
    source_dir = Path(derived.get("source_pack_directory", "")).resolve()
    source_items, source = _verify_source(source_dir)
    source_manifest_path, source_items_path = source_dir / "annotation_manifest.json", source_dir / "annotation_items.jsonl"
    prompt_hash, module_hash = file_sha256(PROMPT), file_sha256(MODULE)
    selection_module_hash = file_sha256(Path(SELECTION_MODULE_PATH))
    expected_protocol = {"provider_base_url": BASE_URL, "model_id": MODEL,
                         "prompt_version": VERSION, "prompt_sha256": prompt_hash,
                         "temperature": 0.0}
    if (derived.get("version") != DERIVED_VERSION
            or pack.get("frozen_model_protocol") != expected_protocol
            or derived.get("source_pack_experiment_id") != source["experiment_id"]
            or derived.get("source_manifest_sha256") != file_sha256(source_manifest_path)
            or derived.get("source_items_sha256") != file_sha256(source_items_path)
            or derived.get("prompt_sha256") != prompt_hash
            or derived.get("builder_sha256") != module_hash
            or derived.get("selection_builder_sha256") != selection_module_hash
            or pack.get("input_sha256", {}).get(str(Path(SELECTION_MODULE_PATH))) != selection_module_hash
            or pack.get("experiment_id") != _identity(
                source["experiment_id"], derived["source_manifest_sha256"],
                derived["source_items_sha256"], prompt_hash, module_hash,
                selection_module_hash)
            or len(items) != len(source_items) or items != source_items
            or file_sha256(pack_dir / "annotation_items.jsonl") != derived.get("source_items_sha256")):
        raise ValueError("v4 derived pack differs from the frozen source snapshot or prompt")
    for path_text, expected in pack.get("input_sha256", {}).items():
        path = Path(path_text)
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError(f"v4 pack input hash differs: {path.name}")
    for name, expected in pack.get("code_sha256", {}).items():
        path = MODULE.with_name(name)
        if not path.is_file() or file_sha256(path) != expected:
            raise ValueError(f"v4 pack source code hash differs: {name}")
    return items, pack


def _verified_raw(pack_dir: Path, raw_dir: Path, review_dir: Path):
    from .wuhan_v4_review import load_review

    items, pack = verify_pack(pack_dir)
    labels, review = load_review(pack_dir, review_dir)
    if review["audit"]["reviewed_items"] != len(items) or len(labels) != len(items):
        raise ValueError("v4 model run requires a complete source-first review")
    manifest_path, raw_path = raw_dir / RAW_MANIFEST, raw_dir / RAW_NAME
    run = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_inputs = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                       "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                       "prompt": file_sha256(PROMPT),
                       "source_first_review_manifest": file_sha256(
                           review_dir / "source_first_review_manifest.json")}
    from .wuhan_model_v4 import VERSION_RUN, _code_hashes

    if (run.get("pipeline_version") != VERSION_RUN
            or run.get("pack_experiment_id") != pack["experiment_id"]
            or run.get("provider_base_url") != BASE_URL
            or run.get("model_id") != MODEL
            or run.get("prompt_version") != VERSION
            or run.get("temperature") != 0.0
            or run.get("input_sha256") != expected_inputs
            or run.get("code_sha256") != _code_hashes()
            or run.get("reviewed_items") != len(items)
            or run.get("review_before_model") is not True
            or run.get("artifacts", {}).get(RAW_NAME, {}).get("sha256") != file_sha256(raw_path)):
        raise ValueError("v4 raw run manifest or checkpoint differs from frozen protocol")
    from .signal_validation import read_jsonl
    from .run_model import _is_runner_failure
    rows = read_jsonl(raw_path)
    if (len(rows) != run.get("model_rows") or run.get("requested_rows") != len(items)
            or run.get("remaining_rows") != len(items) - len(rows)
            or run.get("request_failures") != sum(_is_runner_failure(row) for row in rows)
            or len({row.get("item_id") for row in rows}) != len(rows)):
        raise ValueError("v4 raw coverage, uniqueness or request-failure count differs")
    for row in rows:
        if (set(row) != {"item_id", "source_text_sha256", "model_id", "prompt_version", "raw_response"}
                or row.get("item_id") not in items
                or row.get("source_text_sha256") != items[row["item_id"]]["source_text_sha256"]
                or row.get("model_id") != MODEL or row.get("prompt_version") != VERSION
                or not isinstance(row.get("raw_response"), str)):
            raise ValueError("v4 raw row provenance differs from the frozen source")
    return items, pack, run, rows


def _normalize(items: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .quote_grounding import ground_event
    from .signal_validation import strict_json_loads, validate_predictions

    predictions = []
    for row in rows:
        base = {field: row[field] for field in
                ("item_id", "source_text_sha256", "model_id", "prompt_version")}
        try:
            payload = strict_json_loads(row["raw_response"])
            if not isinstance(payload, dict) or set(payload) != {"events"} or not isinstance(payload["events"], list):
                raise ValueError("v4 response must contain only an events array")
            events = []
            for event in payload["events"]:
                if (not isinstance(event, dict)
                        or any(not isinstance(quote, dict) or quote.get("source") != "reply"
                               for quote in event.get("evidence_quotes", []))):
                    raise ValueError("v4 events require reply-only evidence quotes")
                events.append(ground_event(event, items[row["item_id"]]))
            predictions.append({**base, "events": events, "parse_error": None})
        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as error:
            predictions.append({**base, "events": [],
                                "parse_error": f"{type(error).__name__}: {error}"})
    validate_predictions(items, predictions)
    return predictions


def normalize_v4(pack_dir: Path, raw_dir: Path, output_dir: Path, *,
                 review_dir: Path | None = None) -> dict[str, Any]:
    from .wuhan_v4_review import REVIEW_DIR

    pack_dir, raw_dir, output_dir = (path.resolve() for path in (pack_dir, raw_dir, output_dir))
    review_dir = (review_dir or REVIEW_DIR).resolve()
    items, pack, raw_manifest, rows = _verified_raw(pack_dir, raw_dir, review_dir)
    if raw_manifest["remaining_rows"] != 0:
        raise ValueError("v4 normalization requires complete raw model coverage")
    predictions = _normalize(items, rows)
    outputs_root = (ROOT / "research_outputs").resolve()
    if (output_dir == outputs_root or outputs_root not in output_dir.parents
            or output_dir in pack_dir.parents or pack_dir in output_dir.parents
            or output_dir in raw_dir.parents or raw_dir in output_dir.parents
            or output_dir in review_dir.parents or review_dir in output_dir.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("v4 normalized output must be new and separate from all inputs")
    inputs = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
              "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
              "raw_outputs": file_sha256(raw_dir / RAW_NAME),
              "model_run_manifest": file_sha256(raw_dir / RAW_MANIFEST),
              "source_first_review_manifest": file_sha256(
                  review_dir / "source_first_review_manifest.json")}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        predictions_path = stage / PREDICTIONS
        with predictions_path.open("w", encoding="utf-8") as handle:
            for row in predictions:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        code = {"wuhan_protocol_v4.py": file_sha256(MODULE),
                "quote_grounding.py": file_sha256(MODULE.with_name("quote_grounding.py")),
                "signal_validation.py": file_sha256(MODULE.with_name("signal_validation.py"))}
        manifest = {"pipeline_version": "wuhan-company-confirmed-normalization-v4",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "pack_experiment_id": pack["experiment_id"],
                    "input_sha256": inputs,
                    "model_rows": len(predictions),
                    "parse_errors": sum(row["parse_error"] is not None for row in predictions),
                    "model_ids": [MODEL], "prompt_versions": [VERSION],
                    "evidence_protocol": "reply-only-unique-exact-quote-v4",
                    "code_sha256": code,
                    "artifacts": {PREDICTIONS: {"sha256": file_sha256(predictions_path)}}}
        (stage / NORMAL_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def audit_v4(pack_dir: Path, raw_dir: Path, normal_dir: Path, *,
             review_dir: Path | None = None) -> dict[str, Any]:
    from .wuhan_v4_review import REVIEW_DIR
    from .signal_validation import read_jsonl

    pack_dir, raw_dir, normal_dir = (path.resolve() for path in (pack_dir, raw_dir, normal_dir))
    review_dir = (review_dir or REVIEW_DIR).resolve()
    items, pack, raw_manifest, raw_rows = _verified_raw(pack_dir, raw_dir, review_dir)
    manifest = json.loads((normal_dir / NORMAL_MANIFEST).read_text(encoding="utf-8"))
    predictions_path = normal_dir / PREDICTIONS
    predictions = read_jsonl(predictions_path)
    expected_inputs = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                       "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                       "raw_outputs": file_sha256(raw_dir / RAW_NAME),
                       "model_run_manifest": file_sha256(raw_dir / RAW_MANIFEST),
                       "source_first_review_manifest": file_sha256(
                           review_dir / "source_first_review_manifest.json")}
    expected_code = {"wuhan_protocol_v4.py": file_sha256(MODULE),
                     "quote_grounding.py": file_sha256(MODULE.with_name("quote_grounding.py")),
                     "signal_validation.py": file_sha256(MODULE.with_name("signal_validation.py"))}
    if (manifest.get("pipeline_version") != "wuhan-company-confirmed-normalization-v4"
            or manifest.get("pack_experiment_id") != pack["experiment_id"]
            or manifest.get("input_sha256") != expected_inputs
            or manifest.get("code_sha256") != expected_code
            or manifest.get("artifacts", {}).get(PREDICTIONS, {}).get("sha256") != file_sha256(predictions_path)
            or manifest.get("model_rows") != len(items)
            or manifest.get("parse_errors") != sum(row["parse_error"] is not None for row in predictions)
            or predictions != _normalize(items, raw_rows)):
        raise ValueError("v4 normalized predictions differ from the frozen raw response reconstruction")
    return {"pack_experiment_id": pack["experiment_id"], "items": len(items),
            "reviewed_items": len(items), "model_rows": len(raw_rows),
            "request_failures": raw_manifest["request_failures"],
            "parse_errors": manifest["parse_errors"],
            "reply_only_evidence": all(span["source"] == "reply" for row in predictions
                                       for event in row["events"] for span in event["evidence_spans"]),
            "raw_sha256": file_sha256(raw_dir / RAW_NAME),
            "predictions_sha256": file_sha256(predictions_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    derive = actions.add_parser("derive")
    derive.add_argument("source_dir", type=Path, nargs="?", default=SOURCE_DIR)
    derive.add_argument("--output-dir", type=Path, default=PACK_DIR)
    check = actions.add_parser("preflight")
    check.add_argument("pack_dir", type=Path, nargs="?", default=PACK_DIR)
    normalize = actions.add_parser("normalize")
    normalize.add_argument("pack_dir", type=Path, nargs="?", default=PACK_DIR)
    normalize.add_argument("raw_dir", type=Path, nargs="?", default=RAW_DIR)
    normalize.add_argument("--review-dir", type=Path)
    normalize.add_argument("--output-dir", type=Path, default=NORMAL_DIR)
    audit = actions.add_parser("audit")
    audit.add_argument("pack_dir", type=Path, nargs="?", default=PACK_DIR)
    audit.add_argument("raw_dir", type=Path, nargs="?", default=RAW_DIR)
    audit.add_argument("normal_dir", type=Path, nargs="?", default=NORMAL_DIR)
    audit.add_argument("--review-dir", type=Path)
    args = parser.parse_args()
    if args.action == "derive":
        result = derive_pack(args.source_dir, args.output_dir)
        print(json.dumps({"experiment_id": result["experiment_id"],
                          "items": result["counts"]["items"],
                          "prompt_sha256": result["frozen_model_protocol"]["prompt_sha256"]},
                         ensure_ascii=False))
    elif args.action == "preflight":
        items, pack = verify_pack(args.pack_dir)
        print(json.dumps({"experiment_id": pack["experiment_id"], "items": len(items),
                          "protocol": pack["frozen_model_protocol"]}, ensure_ascii=False))
    elif args.action == "normalize":
        result = normalize_v4(args.pack_dir, args.raw_dir, args.output_dir, review_dir=args.review_dir)
        print(json.dumps({"model_rows": result["model_rows"], "parse_errors": result["parse_errors"]},
                         ensure_ascii=False))
    else:
        print(json.dumps(audit_v4(args.pack_dir, args.raw_dir, args.normal_dir,
                                  review_dir=args.review_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
