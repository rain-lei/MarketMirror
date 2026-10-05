"""Try a v5 candidate on the already seen Wuhan development replies only.

This is a development diagnostic. Its single-assistant reference has seen older
model outputs, and this command never creates an Agent signal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .annotation_pack import canonical_hash
from .assistant_review import load_review
from .compare_holdout import paired_company_bootstrap
from .keyword_baseline import predict_item
from .local_credential import load_api_key
from .run_model import _is_runner_failure, _raw_row, _read_raw_rows, request_completion
from .signal_validation import evaluate, load_pack, read_jsonl
from .wuhan_protocol_v3 import verify_pack as verify_v3_pack
from .wuhan_protocol_v4 import _normalize as normalize_quote_rows


VERSION = "wuhan-v5-seen-development-v1"
PROMPT_VERSION = "wuhan-company-confirmed-v5-development"
MODEL = "DeepSeek-V4-Flash-0731-W8A8"
BASE_URL = "http://aigw.dlut.edu.cn/v1"
ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ROOT / "research_outputs"
SOURCE = OUTPUTS / "wuhan_pre_event_pit_snapshot_2020_v3"
V3_PACK = OUTPUTS / "wuhan_pre_event_dev_protocol_v3_v2"
REVIEW = OUTPUTS / "wuhan_pre_event_assistant_review_v3"
PROMPT = Path(__file__).with_name("PROMPT_WUHAN_V5_CANDIDATE.md")
DEFAULT_OUTPUT = OUTPUTS / "wuhan_v5_development_candidate_v1"
RAW_NAME = "model_raw_outputs.jsonl"
RAW_MANIFEST = "model_run_manifest.json"
PREDICTIONS = "model_predictions.jsonl"
NORMAL_MANIFEST = "normalization_manifest.json"
SCORE_NAME = "development_comparison.json"


def _code_hashes() -> dict[str, str]:
    names = ("wuhan_v5_development.py", "wuhan_protocol_v3.py",
             "wuhan_protocol_v4.py", "run_model.py", "assistant_review.py",
             "quote_grounding.py", "signal_validation.py", "keyword_baseline.py",
             "compare_holdout.py")
    return {name: file_sha256(Path(__file__).with_name(name)) for name in names}


def preflight() -> tuple[dict, list[dict], dict]:
    items, source = load_pack(SOURCE)
    old_items, old_pack = verify_v3_pack(V3_PACK)
    labels, review = load_review(SOURCE, REVIEW)
    if (items != old_items or len(items) != 105
            or len({item["stock_code"] for item in items.values()}) != len(items)
            or any(item["stage"] != "reply" for item in items.values())
            or old_pack.get("derived_protocol", {}).get("source_pack_experiment_id")
            != source["experiment_id"]
            or len(labels) != len(items)
            or review.get("audit", {}).get("reviewed_items") != len(items)
            or review.get("model_outputs_previously_seen") is not True):
        raise ValueError("v5 development input differs from the reviewed, already seen cohort")
    inputs = {name: file_sha256(path) for name, path in (
        ("source_manifest", SOURCE / "annotation_manifest.json"),
        ("source_items", SOURCE / "annotation_items.jsonl"),
        ("v3_pack_manifest", V3_PACK / "annotation_manifest.json"),
        ("review_manifest", REVIEW / "assistant_review_manifest.json"),
        ("reference_labels", REVIEW / "reference_labels.jsonl"),
        ("v5_candidate_prompt", PROMPT))}
    code = _code_hashes()
    experiment_id = canonical_hash({"version": VERSION,
                                    "source_experiment_id": source["experiment_id"],
                                    "v3_pack_experiment_id": old_pack["experiment_id"],
                                    "inputs": inputs, "code": code})
    return items, labels, {"experiment_id": experiment_id, "inputs": inputs,
                           "code": code, "source_experiment_id": source["experiment_id"]}


def _output_root(path: Path, *, resume: bool) -> Path:
    path = path.resolve()
    root = OUTPUTS.resolve()
    inputs = (SOURCE.resolve(), V3_PACK.resolve(), REVIEW.resolve())
    if (path == root or root not in path.parents
            or any(path == item or path in item.parents or item in path.parents for item in inputs)):
        raise ValueError("v5 development output must be a separate child of research_outputs")
    if path.exists() and any(path.iterdir()):
        if not resume:
            raise ValueError("v5 development output must be new or explicitly resumed")
        if {child.name for child in path.iterdir()} != {"raw"}:
            raise ValueError("resume accepts an incomplete raw checkpoint only")
    return path


def _checkpoint(raw_dir: Path, rows: list[dict], items: dict, identity: dict) -> dict:
    raw_path = raw_dir / RAW_NAME
    manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                "sample_role": "seen_development_only",
                "experiment_id": identity["experiment_id"],
                "source_experiment_id": identity["source_experiment_id"],
                "model_id": MODEL, "prompt_version": PROMPT_VERSION,
                "provider_base_url": BASE_URL, "temperature": 0.0,
                "input_sha256": identity["inputs"], "code_sha256": identity["code"],
                "requested_rows": len(items), "model_rows": len(rows),
                "remaining_rows": len(items) - len(rows),
                "request_failures": sum(_is_runner_failure(row) for row in rows),
                "raw_bytes": raw_path.stat().st_size,
                "artifacts": {RAW_NAME: {"sha256": file_sha256(raw_path)}}}
    temporary = raw_dir / "model_run_manifest.json.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(raw_dir / RAW_MANIFEST)
    return manifest


def run_raw(output_root: Path, *, api_key: str, resume: bool = False,
            timeout: float = 90.0, retries: int = 2) -> dict:
    items, _, identity = preflight()
    output_root = _output_root(output_root, resume=resume)
    if not api_key.strip() or timeout <= 0 or retries < 0:
        raise ValueError("v5 development model request settings are invalid")
    raw_dir = output_root / "raw"
    raw_path, manifest_path = raw_dir / RAW_NAME, raw_dir / RAW_MANIFEST
    rows: list[dict] = []
    if resume:
        if not raw_dir.is_dir() or {p.name for p in raw_dir.iterdir()} - {
                RAW_NAME, RAW_MANIFEST, "model_run_manifest.json.tmp"}:
            raise ValueError("resume requires only the v5 development raw checkpoint")
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (prior.get("experiment_id") != identity["experiment_id"]
                or prior.get("input_sha256") != identity["inputs"]
                or prior.get("code_sha256") != identity["code"]
                or prior.get("model_id") != MODEL
                or prior.get("prompt_version") != PROMPT_VERSION):
            raise ValueError("v5 development resume identity differs")
        data = raw_path.read_bytes()
        committed = prior.get("raw_bytes")
        if (type(committed) is not int or not 0 <= committed <= len(data)
                or hashlib.sha256(data[:committed]).hexdigest()
                != prior.get("artifacts", {}).get(RAW_NAME, {}).get("sha256")):
            raise ValueError("v5 development raw checkpoint hash differs")
        rows = _read_raw_rows(raw_path, items, MODEL, data[:committed].decode("utf-8"),
                              PROMPT_VERSION)
        if len(rows) != prior.get("model_rows"):
            raise ValueError("v5 development checkpoint row count differs")
        rows = [row for row in rows if not _is_runner_failure(row)]
    raw_dir.mkdir(parents=True, exist_ok=True)
    with raw_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = _checkpoint(raw_dir, rows, items, identity)
    completed = {row["item_id"] for row in rows}
    pending = [item for item in items.values() if item["item_id"] not in completed]
    prompt = PROMPT.read_text(encoding="utf-8")
    with raw_path.open("a", encoding="utf-8") as handle:
        for index, item in enumerate(pending, start=len(rows) + 1):
            user_message = json.dumps({"segments": item["segments"]}, ensure_ascii=False,
                                      separators=(",", ":"))
            try:
                response = request_completion(
                    BASE_URL + "/chat/completions", api_key, MODEL,
                    [{"role": "system", "content": prompt},
                     {"role": "user", "content": user_message}], timeout, retries, 0.0)
            except RuntimeError as error:
                response = json.dumps({"runner_error": str(error)}, ensure_ascii=False)
            row = _raw_row(item, MODEL, PROMPT_VERSION, response)
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            manifest = _checkpoint(raw_dir, rows, items, identity)
            print(f"Processed {index}/{len(items)}", flush=True)
    if preflight()[2] != identity:
        raise RuntimeError("v5 development source, prompt or code changed during requests")
    return manifest


def normalize_and_score(output_root: Path) -> dict:
    items, labels, identity = preflight()
    output_root = output_root.resolve()
    raw_dir = output_root / "raw"
    raw_path, manifest_path = raw_dir / RAW_NAME, raw_dir / RAW_MANIFEST
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (raw_manifest.get("pipeline_version") != VERSION
            or raw_manifest.get("experiment_id") != identity["experiment_id"]
            or raw_manifest.get("input_sha256") != identity["inputs"]
            or raw_manifest.get("code_sha256") != identity["code"]
            or raw_manifest.get("model_id") != MODEL
            or raw_manifest.get("prompt_version") != PROMPT_VERSION
            or raw_manifest.get("model_rows") != len(items)
            or raw_manifest.get("remaining_rows") != 0
            or raw_manifest.get("artifacts", {}).get(RAW_NAME, {}).get("sha256")
            != file_sha256(raw_path)):
        raise ValueError("v5 development raw manifest or coverage differs")
    raw_rows = _read_raw_rows(raw_path, items, MODEL, prompt_version=PROMPT_VERSION)
    if len(raw_rows) != len(items) or sum(_is_runner_failure(row) for row in raw_rows) != raw_manifest["request_failures"]:
        raise ValueError("v5 development raw rows differ from checkpoint")
    predictions = normalize_quote_rows(items, raw_rows)
    normal_dir, score_dir = output_root / "normalized", output_root / "score"
    if any(path.exists() and any(path.iterdir()) for path in (normal_dir, score_dir)):
        raise ValueError("v5 development normalization and score outputs must be new")
    normal_dir.mkdir(parents=True, exist_ok=True)
    with (normal_dir / PREDICTIONS).open("w", encoding="utf-8") as handle:
        for row in predictions:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    normal_manifest = {"pipeline_version": VERSION, "experiment_id": identity["experiment_id"],
                       "raw_manifest_sha256": file_sha256(manifest_path),
                       "raw_sha256": file_sha256(raw_path),
                       "predictions_sha256": file_sha256(normal_dir / PREDICTIONS),
                       "model_rows": len(predictions),
                       "parse_errors": sum(row["parse_error"] is not None for row in predictions),
                       "code_sha256": identity["code"]}
    (normal_dir / NORMAL_MANIFEST).write_text(
        json.dumps(normal_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if read_jsonl(normal_dir / PREDICTIONS) != normalize_quote_rows(items, raw_rows):
        raise ValueError("v5 normalized file differs from raw reconstruction")

    keyword = [predict_item(item) for item in items.values()]
    model_score = evaluate(items, labels, predictions)
    keyword_score = evaluate(items, labels, keyword)
    paired = paired_company_bootstrap(items, labels, predictions, keyword)
    checks = {"all_items_scored": model_score["prediction_audit"]["prediction_rows"] == len(items),
              "parse_error_at_most_5_percent": model_score["parse_error_items"] / len(items) <= 0.05,
              "event_detection_f1_at_least_0_70": model_score["event_detection"]["f1"] >= 0.70,
              "event_detection_f1_not_below_keyword": (model_score["event_detection"]["f1"]
                                                       >= keyword_score["event_detection"]["f1"]),
              "supported_type_macro_f1_at_least_0_60": (
                  model_score["event_type_macro_f1_supported"] is not None
                  and model_score["event_type_macro_f1_supported"] >= 0.60)}
    result = {"pipeline_version": VERSION, "sample_role": "seen_development_only",
              "experiment_id": identity["experiment_id"],
              "review_basis": {"reviewer_kind": "single_ai_assistant",
                               "model_outputs_previously_seen": True,
                               "human_gold_ready": False},
              "model_run": {"rows": len(raw_rows),
                            "request_failures": raw_manifest["request_failures"],
                            "parse_errors": normal_manifest["parse_errors"]},
              "model": model_score, "keyword": keyword_score,
              "paired_company_bootstrap": paired,
              "development_targets": {"checks": checks, "met": all(checks.values()),
                                      "agent_signal_eligible": False}}
    score_dir.mkdir(parents=True, exist_ok=True)
    score_path = score_dir / SCORE_NAME
    score_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    score_manifest = {"pipeline_version": VERSION,
                      "experiment_id": identity["experiment_id"],
                      "input_sha256": {**identity["inputs"],
                                       "raw_manifest": file_sha256(manifest_path),
                                       "raw_outputs": file_sha256(raw_path),
                                       "normal_manifest": file_sha256(normal_dir / NORMAL_MANIFEST),
                                       "predictions": file_sha256(normal_dir / PREDICTIONS)},
                      "code_sha256": identity["code"],
                      "artifacts": {SCORE_NAME: {"sha256": file_sha256(score_path)}}}
    (score_dir / "development_comparison_manifest.json").write_text(
        json.dumps(score_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preflight", action="store_true", help="verify local inputs without model requests")
    args = parser.parse_args()
    items, _, identity = preflight()
    if args.preflight:
        print(json.dumps({"items": len(items), "experiment_id": identity["experiment_id"],
                          "prompt_sha256": identity["inputs"]["v5_candidate_prompt"],
                          "agent_signal_eligible": False}, ensure_ascii=False))
        return
    key = load_api_key()
    try:
        run_raw(args.output_root, api_key=key, resume=args.resume)
    finally:
        key = None
    result = normalize_and_score(args.output_root)
    print(json.dumps({"items": len(items),
                      "model_f1": result["model"]["event_detection"]["f1"],
                      "type_macro_f1": result["model"]["event_type_macro_f1_supported"],
                      "keyword_f1": result["keyword"]["event_detection"]["f1"],
                      "development_targets_met": result["development_targets"]["met"],
                      "agent_signal_eligible": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
