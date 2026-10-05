"""Normalize and score the one-time fifth-cohort v5 evaluation."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .compare_holdout import paired_company_bootstrap
from .keyword_baseline import predict_item
from .prepare_wuhan_v5_holdout import ROOT, UNIVERSE_NAME, verify_existing as verify_cohort
from .run_model import _is_runner_failure, _read_raw_rows
from .signal_validation import evaluate, read_jsonl
from .wuhan_model_v5 import (
    MANIFEST_NAME as RAW_MANIFEST_NAME, RAW_DIR, RAW_NAME,
    VERSION_RUN, _code_hashes as model_code_hashes, _inputs as model_inputs,
)
from .wuhan_protocol_v4 import _normalize
from .wuhan_protocol_v5 import BASE_URL, MODEL, PACK_DIR, PROMPT, VERSION, verify_pack
from .wuhan_v5_review import (
    MANIFEST_NAME as REVIEW_MANIFEST_NAME, POLICY, REVIEW_DIR, load_review,
)


VERSION_NORMAL = "wuhan-company-confirmed-normalization-v5"
VERSION_SCORE = "wuhan-v5-fifth-company-evaluation-v1"
MODULE = Path(__file__).resolve()
NORMAL_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v5_holdout_normalized_v1"
SCORE_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v5_holdout_scored_v1"
PREDICTIONS_NAME = "model_predictions.jsonl"
NORMAL_MANIFEST_NAME = "normalization_manifest.json"
SCORE_NAME = "v5_fifth_company_comparison.json"
SCORE_MANIFEST_NAME = "v5_fifth_company_comparison_manifest.json"


def _local_new_output(path: Path, *inputs: Path) -> Path:
    path = path.resolve()
    outputs = (ROOT / "research_outputs").resolve()
    if (path == outputs or outputs not in path.parents
            or any(path == item or path in item.parents or item in path.parents
                   for item in (input_path.resolve() for input_path in inputs))
            or path.exists() and any(path.iterdir())):
        raise ValueError("v5 evaluation output must be a new separate local directory")
    return path


def audit_raw(pack_dir: Path = PACK_DIR, raw_dir: Path = RAW_DIR,
              review_dir: Path = REVIEW_DIR) -> tuple[dict, dict, dict, list[dict]]:
    pack_dir, raw_dir, review_dir = (path.resolve() for path in
                                     (pack_dir, raw_dir, review_dir))
    items, pack = verify_pack(pack_dir)
    labels, review = load_review(pack_dir, review_dir)
    if len(items) != 106 or len(labels) != len(items):
        raise ValueError("v5 fifth cohort reply or review count differs")
    raw_path, manifest_path = raw_dir / RAW_NAME, raw_dir / RAW_MANIFEST_NAME
    run = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (run.get("pipeline_version") != VERSION_RUN
            or run.get("pack_experiment_id") != pack["experiment_id"]
            or run.get("provider_base_url") != BASE_URL
            or run.get("model_id") != MODEL
            or run.get("prompt_version") != VERSION
            or run.get("temperature") != 0.0
            or run.get("input_sha256") != model_inputs(pack_dir, review_dir)
            or run.get("code_sha256") != model_code_hashes()
            or run.get("reviewed_items") != len(items)
            or run.get("review_before_model") is not True
            or run.get("requested_rows") != len(items)
            or run.get("model_rows") != len(items)
            or run.get("remaining_rows") != 0
            or run.get("artifacts", {}).get(RAW_NAME, {}).get("sha256")
            != file_sha256(raw_path)):
        raise ValueError("v5 raw run identity, coverage or hash differs")
    reviewed_at = datetime.fromisoformat(review["generated_at"])
    requested_at = datetime.fromisoformat(run["generated_at"])
    if requested_at <= reviewed_at:
        raise ValueError("v5 model outputs must follow source-first review")
    rows = _read_raw_rows(raw_path, items, MODEL, prompt_version=VERSION)
    if (len(rows) != len(items)
            or sum(_is_runner_failure(row) for row in rows) != run["request_failures"]):
        raise ValueError("v5 raw response rows or failure count differ")
    return items, pack, run, rows


def normalize(pack_dir: Path = PACK_DIR, raw_dir: Path = RAW_DIR,
              review_dir: Path = REVIEW_DIR, output_dir: Path = NORMAL_DIR) -> dict:
    pack_dir, raw_dir, review_dir = (path.resolve() for path in
                                     (pack_dir, raw_dir, review_dir))
    output_dir = _local_new_output(output_dir, pack_dir, raw_dir, review_dir)
    items, pack, run, rows = audit_raw(pack_dir, raw_dir, review_dir)
    predictions = _normalize(items, rows)
    inputs = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
              "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
              "raw_manifest": file_sha256(raw_dir / RAW_MANIFEST_NAME),
              "raw_outputs": file_sha256(raw_dir / RAW_NAME),
              "source_first_review_manifest": file_sha256(review_dir / REVIEW_MANIFEST_NAME),
              "prompt": file_sha256(PROMPT)}
    code = {name: file_sha256(MODULE.with_name(name)) for name in (
        "score_wuhan_v5.py", "wuhan_protocol_v4.py", "quote_grounding.py",
        "signal_validation.py")}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        predictions_path = stage / PREDICTIONS_NAME
        with predictions_path.open("w", encoding="utf-8") as handle:
            for row in predictions:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        manifest = {
            "pipeline_version": VERSION_NORMAL,
            "pack_experiment_id": pack["experiment_id"],
            "input_sha256": inputs, "code_sha256": code,
            "model_rows": len(predictions),
            "request_failures": run["request_failures"],
            "parse_errors": sum(row["parse_error"] is not None for row in predictions),
            "evidence_protocol": "reply-only-unique-exact-quote-v5",
            "artifacts": {PREDICTIONS_NAME: {"sha256": file_sha256(predictions_path)}},
        }
        (stage / NORMAL_MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def audit_normal(pack_dir: Path = PACK_DIR, raw_dir: Path = RAW_DIR,
                 review_dir: Path = REVIEW_DIR, normal_dir: Path = NORMAL_DIR) -> tuple[dict, dict, list[dict]]:
    pack_dir, raw_dir, review_dir, normal_dir = (path.resolve() for path in
                                                (pack_dir, raw_dir, review_dir, normal_dir))
    items, pack, run, rows = audit_raw(pack_dir, raw_dir, review_dir)
    manifest = json.loads((normal_dir / NORMAL_MANIFEST_NAME).read_text(encoding="utf-8"))
    expected_inputs = {"annotation_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                       "annotation_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                       "raw_manifest": file_sha256(raw_dir / RAW_MANIFEST_NAME),
                       "raw_outputs": file_sha256(raw_dir / RAW_NAME),
                       "source_first_review_manifest": file_sha256(review_dir / REVIEW_MANIFEST_NAME),
                       "prompt": file_sha256(PROMPT)}
    expected_code = {name: file_sha256(MODULE.with_name(name)) for name in (
        "score_wuhan_v5.py", "wuhan_protocol_v4.py", "quote_grounding.py",
        "signal_validation.py")}
    predictions_path = normal_dir / PREDICTIONS_NAME
    predictions = read_jsonl(predictions_path)
    if (manifest.get("pipeline_version") != VERSION_NORMAL
            or manifest.get("pack_experiment_id") != pack["experiment_id"]
            or manifest.get("input_sha256") != expected_inputs
            or manifest.get("code_sha256") != expected_code
            or manifest.get("model_rows") != len(items)
            or manifest.get("request_failures") != run["request_failures"]
            or manifest.get("parse_errors") != sum(row["parse_error"] is not None
                                                   for row in predictions)
            or manifest.get("artifacts", {}).get(PREDICTIONS_NAME, {}).get("sha256")
            != file_sha256(predictions_path)
            or predictions != _normalize(items, rows)):
        raise ValueError("v5 normalized predictions differ from raw reconstruction")
    return items, pack, predictions


def score(pack_dir: Path = PACK_DIR, raw_dir: Path = RAW_DIR,
          review_dir: Path = REVIEW_DIR, normal_dir: Path = NORMAL_DIR,
          output_dir: Path = SCORE_DIR) -> dict:
    pack_dir, raw_dir, review_dir, normal_dir = (path.resolve() for path in
                                                (pack_dir, raw_dir, review_dir, normal_dir))
    output_dir = _local_new_output(output_dir, pack_dir, raw_dir, review_dir, normal_dir)
    items, pack, predictions = audit_normal(pack_dir, raw_dir, review_dir, normal_dir)
    labels, review = load_review(pack_dir, review_dir)
    cohort = verify_cohort(ROOT / "research/configs" / UNIVERSE_NAME,
                           ROOT / "research/configs/wuhan_pre_event_fresh_v5_holdout_snapshot_2020.json")
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    model_score = evaluate(items, labels, predictions)
    keyword = [predict_item(item) for item in items.values()]
    keyword_score = evaluate(items, labels, keyword)
    paired = paired_company_bootstrap(items, labels, predictions, keyword)
    thresholds = policy["thresholds"]
    detection = model_score["event_detection"]["f1"]
    macro = model_score["event_type_macro_f1_supported"]
    raw_run = json.loads((raw_dir / RAW_MANIFEST_NAME).read_text(encoding="utf-8"))
    checks = {
        "review_complete_before_model": (review["audit"]["reviewed_items"] == len(items)
                                         and review["model_outputs_previously_seen"] is False
                                         and datetime.fromisoformat(raw_run["generated_at"])
                                         > datetime.fromisoformat(review["generated_at"])),
        "company_sample_disjoint": cohort["excluded_companies"] == 504,
        "all_items_scored": model_score["prediction_audit"]["prediction_rows"] == len(items),
        "parse_error_at_most_5_percent": (model_score["parse_error_items"] / len(items)
                                          <= thresholds["max_parse_error_rate"]),
        "event_detection_f1_at_least_0_70": detection >= thresholds["min_event_detection_f1"],
        "event_detection_f1_not_below_keyword": detection >= keyword_score["event_detection"]["f1"],
        "supported_type_macro_f1_at_least_0_60": (
            macro is not None and macro >= thresholds["min_supported_type_macro_f1"]),
    }
    result = {
        "pipeline_version": VERSION_SCORE,
        "sample_role": "fifth_disjoint_company_evaluation_not_externally_preregistered",
        "pack_experiment_id": pack["experiment_id"],
        "source_disjointness": {
            "selected_companies": cohort["companies"],
            "companies_with_visible_reply": len(items),
            "companies_without_visible_reply": cohort["companies"] - len(items),
            "excluded_prior_companies": cohort["excluded_companies"],
            "selected_stock_codes_sha256": cohort["selected_codes_sha256"],
            "zero_overlap": True,
        },
        "reference_basis": {
            "reviewer_kind": "single_ai_assistant",
            "review_before_v5_model_outputs": True,
            "model_outputs_previously_seen": False,
            "human_gold_ready": False,
            "score_interpretation": policy["score_interpretation"],
        },
        "model_run_audit": {
            "model_rows": len(predictions),
            "request_failures": raw_run["request_failures"],
            "parse_errors": model_score["parse_error_items"],
        },
        "model": model_score, "keyword": keyword_score,
        "paired_company_bootstrap": paired,
        "research_signal_gate": {
            "checks": checks, "passed": all(checks.values()),
            "thresholds": thresholds,
            "scope": policy["score_interpretation"],
        },
    }
    inputs = {str(path.resolve()): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        raw_dir / RAW_MANIFEST_NAME, raw_dir / RAW_NAME,
        review_dir / REVIEW_MANIFEST_NAME, review_dir / "reference_labels.jsonl",
        normal_dir / NORMAL_MANIFEST_NAME, normal_dir / PREDICTIONS_NAME,
        POLICY, PROMPT,
        ROOT / "research/configs" / UNIVERSE_NAME)}
    code = {name: file_sha256(MODULE.with_name(name)) for name in (
        "score_wuhan_v5.py", "wuhan_model_v5.py", "wuhan_protocol_v5.py",
        "wuhan_v5_review.py", "prepare_wuhan_v5_holdout.py",
        "signal_validation.py", "compare_holdout.py", "keyword_baseline.py")}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        report = stage / SCORE_NAME
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
                          + "\n", encoding="utf-8")
        manifest = {"pipeline_version": VERSION_SCORE,
                    "inputs": inputs, "code_sha256": code,
                    "artifacts": {SCORE_NAME: {"sha256": file_sha256(report)}}}
        (stage / SCORE_MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--normalize", action="store_true")
    group.add_argument("--audit-normal", action="store_true")
    group.add_argument("--score", action="store_true")
    args = parser.parse_args()
    if args.normalize:
        result = normalize()
        print(json.dumps({"model_rows": result["model_rows"],
                          "parse_errors": result["parse_errors"]}, ensure_ascii=False))
    elif args.audit_normal:
        items, pack, _ = audit_normal()
        print(json.dumps({"model_rows": len(items),
                          "pack_experiment_id": pack["experiment_id"]}, ensure_ascii=False))
    else:
        result = score()
        print(json.dumps({"model_f1": result["model"]["event_detection"]["f1"],
                          "type_macro_f1": result["model"]["event_type_macro_f1_supported"],
                          "keyword_f1": result["keyword"]["event_detection"]["f1"],
                          "gate_passed": result["research_signal_gate"]["passed"]},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
