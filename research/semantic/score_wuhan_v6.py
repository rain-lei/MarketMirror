"""Normalize and score the one-time sixth-cohort v6 evaluation."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .compare_holdout import paired_company_bootstrap
from .keyword_baseline import predict_item
from .prepare_wuhan_v6_holdout import (
    POLICY, PROMPT, ROOT, SNAPSHOT_NAME, UNIVERSE_NAME,
    verify_existing as verify_cohort,
)
from .run_model import _is_runner_failure, _read_raw_rows
from .signal_validation import evaluate, read_jsonl
from .wuhan_model_v6 import (
    MANIFEST_NAME as RAW_MANIFEST_NAME, RAW_DIR, RAW_NAME,
    VERSION_RUN, _code_hashes as model_code_hashes, _inputs as model_inputs,
)
from .wuhan_protocol_v4 import _normalize
from .wuhan_v6_gate import evaluate_gate, load_policy
from .wuhan_v6_review import (
    MANIFEST_NAME as REVIEW_MANIFEST_NAME, REVIEW_DIR, load_review,
)
from .wuhan_v6_snapshot import (
    BASE_URL, MODEL, OUTPUT_DIR as PACK_DIR, PROMPT_VERSION,
    verify_snapshot,
)


VERSION_NORMAL = "wuhan-company-confirmed-normalization-v6"
VERSION_SCORE = "wuhan-v6-sixth-company-evaluation-v1"
MODULE = Path(__file__).resolve()
NORMAL_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v6_holdout_normalized_v1"
SCORE_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v6_holdout_scored_v1"
PREDICTIONS_NAME = "model_predictions.jsonl"
NORMAL_MANIFEST_NAME = "normalization_manifest.json"
SCORE_NAME = "v6_sixth_company_comparison.json"
SCORE_MANIFEST_NAME = "v6_sixth_company_comparison_manifest.json"


def _local_new_output(path: Path, *inputs: Path) -> Path:
    path = path.resolve()
    outputs = (ROOT / "research_outputs").resolve()
    if (path == outputs or outputs not in path.parents
            or any(path == item or path in item.parents or item in path.parents
                   for item in (input_path.resolve() for input_path in inputs))
            or path.exists() and any(path.iterdir())):
        raise ValueError("v6 evaluation output must be a new separate local directory")
    return path


def audit_raw(pack_dir: Path = PACK_DIR, raw_dir: Path = RAW_DIR,
              review_dir: Path = REVIEW_DIR) -> tuple[dict, dict, dict, list[dict]]:
    pack_dir, raw_dir, review_dir = (path.resolve() for path in
                                     (pack_dir, raw_dir, review_dir))
    items, pack = verify_snapshot(output_dir=pack_dir)
    labels, review = load_review(pack_dir, review_dir)
    if len(items) != 250 or len(labels) != len(items):
        raise ValueError("v6 sixth cohort reply or review count differs")
    raw_path, manifest_path = raw_dir / RAW_NAME, raw_dir / RAW_MANIFEST_NAME
    run = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (run.get("pipeline_version") != VERSION_RUN
            or run.get("pack_experiment_id") != pack["experiment_id"]
            or run.get("provider_base_url") != BASE_URL
            or run.get("model_id") != MODEL
            or run.get("prompt_version") != PROMPT_VERSION
            or run.get("temperature") != 0.0
            or run.get("input_sha256") != model_inputs(pack_dir, review_dir)
            or run.get("code_sha256") != model_code_hashes()
            or run.get("reviewed_items") != len(items)
            or run.get("review_before_model") is not True
            or run.get("requested_rows") != len(items)
            or run.get("model_rows") != len(items)
            or run.get("remaining_rows") != 0
            or run.get("raw_bytes") != raw_path.stat().st_size
            or run.get("artifacts", {}).get(RAW_NAME, {}).get("sha256")
            != file_sha256(raw_path)):
        raise ValueError("v6 raw run identity, coverage or hash differs")
    reviewed_at = datetime.fromisoformat(review["generated_at"])
    requested_at = datetime.fromisoformat(run["generated_at"])
    if requested_at <= reviewed_at:
        raise ValueError("v6 model outputs must follow source-first review")
    rows = _read_raw_rows(raw_path, items, MODEL, prompt_version=PROMPT_VERSION)
    if (len(rows) != len(items)
            or sum(_is_runner_failure(row) for row in rows) != run["request_failures"]):
        raise ValueError("v6 raw response rows or failure count differ")
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
              "prompt": file_sha256(PROMPT),
              "evaluation_policy": file_sha256(POLICY)}
    code = {name: file_sha256(MODULE.with_name(name)) for name in (
        "score_wuhan_v6.py", "wuhan_protocol_v4.py", "quote_grounding.py",
        "signal_validation.py", "wuhan_v6_gate.py")}
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
            "evidence_protocol": "reply-only-unique-exact-quote-v6",
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
                       "prompt": file_sha256(PROMPT),
                       "evaluation_policy": file_sha256(POLICY)}
    expected_code = {name: file_sha256(MODULE.with_name(name)) for name in (
        "score_wuhan_v6.py", "wuhan_protocol_v4.py", "quote_grounding.py",
        "signal_validation.py", "wuhan_v6_gate.py")}
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
        raise ValueError("v6 normalized predictions differ from raw reconstruction")
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
                           ROOT / "research/configs" / SNAPSHOT_NAME)
    policy = load_policy(POLICY)
    model_score = evaluate(items, labels, predictions)
    keyword = [predict_item(item) for item in items.values()]
    keyword_score = evaluate(items, labels, keyword)
    paired = paired_company_bootstrap(
        items, labels, predictions, keyword,
        replicates=policy["bootstrap"]["replicates"],
        seed=policy["bootstrap"]["seed"])
    raw_run = json.loads((raw_dir / RAW_MANIFEST_NAME).read_text(encoding="utf-8"))
    review_before_model = (review["audit"]["reviewed_items"] == len(items)
                           and review["model_outputs_previously_seen"] is False
                           and review["review_before_model_outputs"] is True
                           and datetime.fromisoformat(raw_run["generated_at"])
                           > datetime.fromisoformat(review["generated_at"]))
    selected = json.loads((ROOT / "research/configs" / UNIVERSE_NAME).read_text(
        encoding="utf-8"))
    disjoint = (cohort["companies"] == policy["sample_size"]
                and selected["selection_audit"]["excluded_company_count"] == 630
                and len(set(selected["stock_codes"])) == len(items))
    gate = evaluate_gate(model_score, keyword_score, len(items),
                         source_first_review=review_before_model,
                         zero_company_overlap=disjoint, policy=policy)
    result = {
        "pipeline_version": VERSION_SCORE,
        "sample_role": "sixth_disjoint_company_evaluation_not_externally_preregistered",
        "pack_experiment_id": pack["experiment_id"],
        "source_disjointness": {
            "selected_companies": cohort["companies"],
            "companies_with_visible_reply": len(items),
            "excluded_prior_companies": selected["selection_audit"]["excluded_company_count"],
            "selected_pairs_sha256": cohort["selected_pairs_sha256"],
            "zero_overlap": disjoint,
        },
        "reference_basis": {
            "reviewer_kind": "single_ai_assistant",
            "review_before_v6_model_outputs": review_before_model,
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
        "research_signal_gate": gate,
        "sampling_note": "Answer-visible question-topic-enriched company sample; scores do not estimate market prevalence.",
    }
    inputs = {str(path.resolve()): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        raw_dir / RAW_MANIFEST_NAME, raw_dir / RAW_NAME,
        review_dir / REVIEW_MANIFEST_NAME, review_dir / "reference_labels.jsonl",
        normal_dir / NORMAL_MANIFEST_NAME, normal_dir / PREDICTIONS_NAME,
        POLICY, PROMPT,
        ROOT / "research/configs" / UNIVERSE_NAME,
        ROOT / "research/configs" / SNAPSHOT_NAME)}
    code = {name: file_sha256(MODULE.with_name(name)) for name in (
        "score_wuhan_v6.py", "wuhan_model_v6.py", "wuhan_v6_snapshot.py",
        "wuhan_v6_review.py", "prepare_wuhan_v6_holdout.py", "wuhan_v6_gate.py",
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


def audit_score(pack_dir: Path = PACK_DIR, raw_dir: Path = RAW_DIR,
                review_dir: Path = REVIEW_DIR, normal_dir: Path = NORMAL_DIR,
                score_dir: Path = SCORE_DIR) -> dict:
    """Re-score into a fresh local directory and compare archived bytes."""
    score_dir = score_dir.resolve()
    archived_path = score_dir / SCORE_NAME
    manifest = json.loads((score_dir / SCORE_MANIFEST_NAME).read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION_SCORE
            or manifest.get("artifacts", {}).get(SCORE_NAME, {}).get("sha256")
            != file_sha256(archived_path)):
        raise ValueError("v6 archived score manifest or file hash differs")
    outputs = ROOT / "research_outputs"
    with tempfile.TemporaryDirectory(dir=outputs) as temporary:
        fresh_dir = Path(temporary) / "rescore"
        score(pack_dir, raw_dir, review_dir, normal_dir, fresh_dir)
        fresh_path = fresh_dir / SCORE_NAME
        fresh_manifest = json.loads((fresh_dir / SCORE_MANIFEST_NAME).read_text(
            encoding="utf-8"))
        if (archived_path.read_bytes() != fresh_path.read_bytes()
                or manifest.get("inputs") != fresh_manifest.get("inputs")
                or manifest.get("code_sha256") != fresh_manifest.get("code_sha256")):
            raise ValueError("v6 archived score differs from fresh source reconstruction")
    return {"score_sha256": file_sha256(archived_path),
            "byte_identical_fresh_rescore": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--normalize", action="store_true")
    group.add_argument("--audit-normal", action="store_true")
    group.add_argument("--score", action="store_true")
    group.add_argument("--audit-score", action="store_true")
    args = parser.parse_args()
    if args.normalize:
        result = normalize()
        print(json.dumps({"model_rows": result["model_rows"],
                          "parse_errors": result["parse_errors"]}, ensure_ascii=False))
    elif args.audit_normal:
        items, pack, _ = audit_normal()
        print(json.dumps({"model_rows": len(items),
                          "pack_experiment_id": pack["experiment_id"]}, ensure_ascii=False))
    elif args.score:
        result = score()
        print(json.dumps({"model_f1": result["model"]["event_detection"]["f1"],
                          "type_macro_f1": result["model"]["event_type_macro_f1_supported"],
                          "keyword_f1": result["keyword"]["event_detection"]["f1"],
                          "gate_passed": result["research_signal_gate"]["passed"]},
                         ensure_ascii=False))
    else:
        print(json.dumps(audit_score(), ensure_ascii=False))


if __name__ == "__main__":
    main()
