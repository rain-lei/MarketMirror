"""Score the frozen v3 model against the source-first fresh-cohort review."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .compare_holdout import paired_company_bootstrap
from .keyword_baseline import predict_item
from .signal_validation import evaluate, read_jsonl
from .wuhan_fresh_holdout_review import (
    POLICY as REVIEW_POLICY,
    load_review,
)
from .wuhan_protocol_v3 import NORMAL_MANIFEST, PREDICTIONS, audit_v3, verify_pack


VERSION = "wuhan-fresh-v3-source-first-comparison-v1"
ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_protocol_v3_v1"
RAW_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_model_v3_v1"
NORMAL_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_normalized_v1"
KEYWORD_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_keywords_v1"
REVIEW_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_review_v1"
SCORING_POLICY = ROOT / "research/configs/wuhan_validation_review_policy_v3.json"


def score(pack_dir: Path, raw_dir: Path, normal_dir: Path, keyword_dir: Path,
          review_dir: Path, output_dir: Path) -> dict:
    pack_dir, raw_dir, normal_dir, keyword_dir, review_dir, output_dir = (
        path.resolve() for path in (pack_dir, raw_dir, normal_dir, keyword_dir, review_dir, output_dir))
    if pack_dir != PACK_DIR.resolve():
        raise ValueError("fresh score is restricted to the frozen 94-reply cohort")
    inputs_dirs = (pack_dir, raw_dir, normal_dir, keyword_dir, review_dir)
    if (output_dir.exists() and any(output_dir.iterdir())
            or any(output_dir == path or output_dir in path.parents or path in output_dir.parents
                   for path in inputs_dirs)):
        raise ValueError("score output must be new and separate from every input")

    items, pack = verify_pack(pack_dir)
    derived = pack.get("derived_protocol", {})
    if (len(items) != 94 or Path(derived.get("source_pack_directory", "")).resolve()
            != (ROOT / "research_outputs/wuhan_pre_event_fresh_v3_holdout_source_v1").resolve()):
        raise ValueError("score pack is not derived from the frozen fresh cohort")
    labels, review = load_review(pack_dir, review_dir)
    raw_audit = audit_v3(pack_dir, raw_dir, normal_dir)
    raw_manifest = json.loads((raw_dir / "model_run_manifest.json").read_text(encoding="utf-8"))
    reviewed_at = datetime.fromisoformat(review["generated_at"])
    requested_at = datetime.fromisoformat(raw_manifest["generated_at"])
    if requested_at <= reviewed_at:
        raise ValueError("the model run must follow the source-first review")

    keyword_manifest_path = keyword_dir / "keyword_manifest.json"
    keyword_path = keyword_dir / "keyword_predictions.jsonl"
    keyword_manifest = json.loads(keyword_manifest_path.read_text(encoding="utf-8"))
    keyword = read_jsonl(keyword_path)
    if (keyword_manifest.get("pack_experiment_id") != pack["experiment_id"]
            or keyword_manifest.get("artifacts", {}).get(keyword_path.name, {}).get("sha256")
            != file_sha256(keyword_path)
            or keyword != [predict_item(item) for item in items.values()]):
        raise ValueError("literal keyword comparator differs from the frozen pack")

    predictions = read_jsonl(normal_dir / PREDICTIONS)
    model_score = evaluate(items, labels, predictions)
    keyword_score = evaluate(items, labels, keyword)
    paired = paired_company_bootstrap(items, labels, predictions, keyword)
    policy = json.loads(SCORING_POLICY.read_text(encoding="utf-8"))
    thresholds = policy["thresholds"]
    detection = model_score["event_detection"]["f1"]
    macro = model_score["event_type_macro_f1_supported"]
    parse_rate = model_score["parse_error_items"] / len(items)
    checks = {
        "source_first_review_complete_before_model": (
            review["audit"]["reviewed_items"] == len(items)
            and review.get("model_outputs_previously_seen") is False
            and requested_at > reviewed_at),
        "all_items_scored": (model_score["prediction_audit"]["prediction_rows"] == len(items)
                              and raw_audit["model_rows"] == len(items)),
        "parse_error_at_most_5_percent": parse_rate <= thresholds["max_parse_error_rate"],
        "event_detection_f1_at_least_0_70": detection >= thresholds["min_event_detection_f1"],
        "event_detection_f1_not_below_keyword": detection >= keyword_score["event_detection"]["f1"],
        "supported_type_macro_f1_at_least_0_60": (
            macro is not None and macro >= thresholds["min_supported_type_macro_f1"]),
    }
    result = {
        "pipeline_version": VERSION,
        "sample_role": "fresh_disjoint_company_cohort_not_externally_preregistered",
        "pack_experiment_id": pack["experiment_id"],
        "reference_basis": {
            "reviewer_kind": "single_ai_assistant",
            "review_before_model": True,
            "model_outputs_previously_seen": False,
            "independent_company_sample": True,
            "external_preregistration": False,
            "human_gold_ready": False,
            "score_interpretation": policy["score_interpretation"],
        },
        "source_first_review": review["audit"],
        "model_run_audit": raw_audit,
        "model": model_score,
        "keyword": keyword_score,
        "paired_company_bootstrap": paired,
        "research_signal_gate": {
            "checks": checks,
            "passed": all(checks.values()),
            "thresholds": thresholds,
            "scope": "Agreement with one AI source-first review on a fresh disjoint company cohort; not human-ground-truth accuracy or market predictive validity.",
        },
    }
    paths = (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        raw_dir / "model_run_manifest.json", raw_dir / "model_raw_outputs.jsonl",
        normal_dir / NORMAL_MANIFEST, normal_dir / PREDICTIONS,
        review_dir / "source_first_review_manifest.json", review_dir / "reference_labels.jsonl",
        keyword_manifest_path, keyword_path, REVIEW_POLICY, SCORING_POLICY,
    )
    input_hashes = {str(path): file_sha256(path) for path in paths}
    code_hashes = {name: file_sha256(Path(__file__).with_name(name)) for name in (
        "score_wuhan_fresh_holdout.py", "wuhan_protocol_v3.py",
        "wuhan_fresh_holdout_review.py", "signal_validation.py",
        "compare_holdout.py", "keyword_baseline.py")}
    if any(file_sha256(Path(name)) != digest for name, digest in input_hashes.items()):
        raise RuntimeError("fresh score input changed during evaluation")

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        report = stage / "fresh_holdout_comparison.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
        manifest = {"pipeline_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "inputs": input_hashes, "code_sha256": code_hashes,
                    "artifacts": {report.name: {"sha256": file_sha256(report)}}}
        (stage / "fresh_holdout_comparison_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path, nargs="?", default=PACK_DIR)
    parser.add_argument("raw_dir", type=Path, nargs="?", default=RAW_DIR)
    parser.add_argument("normal_dir", type=Path, nargs="?", default=NORMAL_DIR)
    parser.add_argument("keyword_dir", type=Path, nargs="?", default=KEYWORD_DIR)
    parser.add_argument("review_dir", type=Path, nargs="?", default=REVIEW_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.pack_dir, args.raw_dir, args.normal_dir, args.keyword_dir,
                   args.review_dir, args.output_dir)
    print(json.dumps({"model_f1": result["model"]["event_detection"]["f1"],
                      "keyword_f1": result["keyword"]["event_detection"]["f1"],
                      "gate_passed": result["research_signal_gate"]["passed"],
                      "checks": result["research_signal_gate"]["checks"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
