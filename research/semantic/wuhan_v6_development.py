"""Try a v6 candidate on the consumed fifth Wuhan cohort only.

This is a development diagnostic. The fifth cohort was an independent one-time
v5 evaluation, but it is now seen for v6 and can never validate v6 independently.
This command never creates an Agent signal.
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
from .compare_holdout import paired_company_bootstrap
from .keyword_baseline import predict_item
from .local_credential import load_api_key
from .run_model import _is_runner_failure, _raw_row, _read_raw_rows, request_completion
from .signal_validation import evaluate, read_jsonl
from .wuhan_protocol_v4 import _normalize as normalize_quote_rows
from .wuhan_protocol_v5 import PACK_DIR, ROOT, verify_pack
from .wuhan_v5_review import REVIEW_DIR, load_review


VERSION = "wuhan-v6-fifth-seen-development-v1"
PROMPT_VERSION = "wuhan-company-confirmed-v6-development-candidate"
MODEL = "DeepSeek-V4-Flash-0731-W8A8"
BASE_URL = "http://aigw.dlut.edu.cn/v1"
OUTPUTS = ROOT / "research_outputs"
PROMPT = Path(__file__).with_name("PROMPT_WUHAN_V6_CANDIDATE.md")
DEFAULT_OUTPUT = OUTPUTS / "wuhan_v6_fifth_seen_development_candidate_v1"
RAW_NAME = "model_raw_outputs.jsonl"
RAW_MANIFEST = "model_run_manifest.json"
PREDICTIONS = "model_predictions.jsonl"
NORMAL_MANIFEST = "normalization_manifest.json"
SCORE_NAME = "development_comparison.json"


def _code_hashes() -> dict[str, str]:
    names = ("wuhan_v6_development.py", "wuhan_protocol_v5.py",
             "wuhan_v5_review.py", "wuhan_protocol_v4.py", "run_model.py",
             "annotation_pack.py", "local_credential.py",
             "quote_grounding.py", "signal_validation.py", "keyword_baseline.py",
             "compare_holdout.py")
    return {name: file_sha256(Path(__file__).with_name(name)) for name in names}


def preflight() -> tuple[dict, list[dict], dict]:
    items, pack = verify_pack(PACK_DIR)
    labels, review = load_review(PACK_DIR, REVIEW_DIR)
    if (len(items) != 106
            or len({item["stock_code"] for item in items.values()}) != len(items)
            or any(item["stage"] != "reply" for item in items.values())
            or len(labels) != len(items)
            or review.get("audit", {}).get("reviewed_items") != len(items)
            or review.get("model_outputs_previously_seen") is not False):
        raise ValueError("v6 development input differs from the consumed fifth cohort")
    inputs = {name: file_sha256(path) for name, path in (
        ("fifth_pack_manifest", PACK_DIR / "annotation_manifest.json"),
        ("fifth_pack_items", PACK_DIR / "annotation_items.jsonl"),
        ("source_first_review_manifest", REVIEW_DIR / "source_first_review_manifest.json"),
        ("reference_labels", REVIEW_DIR / "reference_labels.jsonl"),
        ("v6_candidate_prompt", PROMPT))}
    code = _code_hashes()
    experiment_id = canonical_hash({"version": VERSION,
                                    "fifth_pack_experiment_id": pack["experiment_id"],
                                    "inputs": inputs, "code": code})
    return items, labels, {"experiment_id": experiment_id, "inputs": inputs,
                           "code": code, "fifth_pack_experiment_id": pack["experiment_id"]}


def _output_root(path: Path, *, resume: bool) -> Path:
    path = path.resolve()
    root = OUTPUTS.resolve()
    inputs = (PACK_DIR.resolve(), REVIEW_DIR.resolve())
    if (path == root or root not in path.parents
            or any(path == item or path in item.parents or item in path.parents for item in inputs)):
        raise ValueError("v6 development output must be a separate child of research_outputs")
    if path.exists() and any(path.iterdir()):
        if not resume:
            raise ValueError("v6 development output must be new or explicitly resumed")
        if {child.name for child in path.iterdir()} != {"raw"}:
            raise ValueError("resume accepts an incomplete raw checkpoint only")
    return path


def _checkpoint(raw_dir: Path, rows: list[dict], items: dict, identity: dict) -> dict:
    raw_path = raw_dir / RAW_NAME
    manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                "sample_role": "seen_development_only",
                "experiment_id": identity["experiment_id"],
                "fifth_pack_experiment_id": identity["fifth_pack_experiment_id"],
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
        raise ValueError("v6 development model request settings are invalid")
    raw_dir = output_root / "raw"
    raw_path, manifest_path = raw_dir / RAW_NAME, raw_dir / RAW_MANIFEST
    rows: list[dict] = []
    if resume:
        if not raw_dir.is_dir() or {p.name for p in raw_dir.iterdir()} - {
                RAW_NAME, RAW_MANIFEST, "model_run_manifest.json.tmp"}:
            raise ValueError("resume requires only the v6 development raw checkpoint")
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (prior.get("experiment_id") != identity["experiment_id"]
                or prior.get("input_sha256") != identity["inputs"]
                or prior.get("code_sha256") != identity["code"]
                or prior.get("model_id") != MODEL
                or prior.get("prompt_version") != PROMPT_VERSION):
            raise ValueError("v6 development resume identity differs")
        data = raw_path.read_bytes()
        committed = prior.get("raw_bytes")
        if (type(committed) is not int or not 0 <= committed <= len(data)
                or hashlib.sha256(data[:committed]).hexdigest()
                != prior.get("artifacts", {}).get(RAW_NAME, {}).get("sha256")):
            raise ValueError("v6 development raw checkpoint hash differs")
        rows = _read_raw_rows(raw_path, items, MODEL, data[:committed].decode("utf-8"),
                              PROMPT_VERSION)
        if len(rows) != prior.get("model_rows"):
            raise ValueError("v6 development checkpoint row count differs")
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
        raise RuntimeError("v6 development source, prompt or code changed during requests")
    return manifest


def normalize_and_score(output_root: Path) -> dict:
    items, labels, identity = preflight()
    output_root = output_root.resolve()
    raw_dir = output_root / "raw"
    raw_path, manifest_path = raw_dir / RAW_NAME, raw_dir / RAW_MANIFEST
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (raw_manifest.get("pipeline_version") != VERSION
            or raw_manifest.get("sample_role") != "seen_development_only"
            or raw_manifest.get("experiment_id") != identity["experiment_id"]
            or raw_manifest.get("fifth_pack_experiment_id")
            != identity["fifth_pack_experiment_id"]
            or raw_manifest.get("input_sha256") != identity["inputs"]
            or raw_manifest.get("code_sha256") != identity["code"]
            or raw_manifest.get("provider_base_url") != BASE_URL
            or raw_manifest.get("model_id") != MODEL
            or raw_manifest.get("prompt_version") != PROMPT_VERSION
            or raw_manifest.get("temperature") != 0.0
            or raw_manifest.get("requested_rows") != len(items)
            or raw_manifest.get("model_rows") != len(items)
            or raw_manifest.get("remaining_rows") != 0
            or raw_manifest.get("raw_bytes") != raw_path.stat().st_size
            or raw_manifest.get("artifacts", {}).get(RAW_NAME, {}).get("sha256")
            != file_sha256(raw_path)):
        raise ValueError("v6 development raw manifest or coverage differs")
    raw_rows = _read_raw_rows(raw_path, items, MODEL, prompt_version=PROMPT_VERSION)
    if len(raw_rows) != len(items) or sum(_is_runner_failure(row) for row in raw_rows) != raw_manifest["request_failures"]:
        raise ValueError("v6 development raw rows differ from checkpoint")
    predictions = normalize_quote_rows(items, raw_rows)
    normal_dir, score_dir = output_root / "normalized", output_root / "score"
    if any(path.exists() and any(path.iterdir()) for path in (normal_dir, score_dir)):
        raise ValueError("v6 development normalization and score outputs must be new")
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
        raise ValueError("v6 normalized file differs from raw reconstruction")

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
                               "reason": "the fifth cohort was already scored with v5",
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
                          "prompt_sha256": identity["inputs"]["v6_candidate_prompt"],
                          "request_user_fields": ["segments"],
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
