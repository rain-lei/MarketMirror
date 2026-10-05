"""Score the frozen v4 prompt on its disjoint fourth company cohort."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .assistant_review import POLICY_PATH as ORIGINAL_POLICY
from .compare_holdout import paired_company_bootstrap
from .keyword_baseline import predict_item
from .prepare_wuhan_v4_holdout import ROOT, PRIOR_UNIVERSES, UNIVERSE_NAME, verify_existing as verify_cohort
from .signal_validation import evaluate, read_jsonl
from .wuhan_protocol_v4 import (
    NORMAL_MANIFEST,
    NORMAL_DIR,
    PACK_DIR,
    PREDICTIONS,
    RAW_DIR,
    RAW_MANIFEST,
    RAW_NAME,
    audit_v4,
    verify_pack,
)
from .wuhan_v4_review import MANIFEST_NAME as REVIEW_MANIFEST, POLICY, REVIEW_DIR, load_review


VERSION = "wuhan-v4-fourth-company-evaluation-v1"
OUTPUT_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v4_holdout_scored_v1"


def _disjoint_audit(pack_dir: Path) -> dict[str, Any]:
    config_report = verify_cohort(
        ROOT / "research/configs" / UNIVERSE_NAME,
        ROOT / "research/configs/wuhan_pre_event_fresh_v4_holdout_snapshot_2020.json")
    current = json.loads((ROOT / "research/configs" / UNIVERSE_NAME).read_text(encoding="utf-8"))
    codes = set(current["stock_codes"])
    exclusions = {}
    for name, path in PRIOR_UNIVERSES:
        prior = json.loads(path.read_text(encoding="utf-8"))
        overlap = sorted(codes.intersection(prior["stock_codes"]))
        if overlap:
            raise ValueError(f"v4 cohort overlaps prior {name} company sample")
        exclusions[name] = {"companies": len(prior["stock_codes"]),
                            "config_sha256": file_sha256(path), "overlap": 0}
    items, pack = verify_pack(pack_dir)
    reply_companies = {item["stock_code"] for item in items.values()}
    if (len(codes) != 126 or len(reply_companies) != 108
            or not reply_companies <= codes
            or pack.get("universe_size") != 126):
        raise ValueError("v4 frozen membership or reply coverage differs")
    return {"selected_companies": len(codes), "companies_with_visible_reply": len(reply_companies),
            "companies_without_visible_reply": len(codes - reply_companies),
            "selected_stock_codes_sha256": config_report["selected_codes_sha256"],
            "excluded_cohorts": exclusions, "zero_overlap": True}


def score(pack_dir: Path, raw_dir: Path, normal_dir: Path, review_dir: Path,
          output_dir: Path) -> dict[str, Any]:
    pack_dir, raw_dir, normal_dir, review_dir, output_dir = (
        path.resolve() for path in (pack_dir, raw_dir, normal_dir, review_dir, output_dir))
    if pack_dir != PACK_DIR.resolve():
        raise ValueError("v4 scoring is restricted to the frozen fourth company cohort")
    if (output_dir.exists() and any(output_dir.iterdir())
            or any(output_dir == path or output_dir in path.parents or path in output_dir.parents
                   for path in (pack_dir, raw_dir, normal_dir, review_dir))):
        raise ValueError("v4 score output must be new and separate from every frozen input")
    outputs_root = (ROOT / "research_outputs").resolve()
    if output_dir == outputs_root or outputs_root not in output_dir.parents:
        raise ValueError("v4 score output must stay under the ignored research_outputs folder")
    items, pack = verify_pack(pack_dir)
    labels, review = load_review(pack_dir, review_dir)
    source_audit = _disjoint_audit(pack_dir)
    model_audit = audit_v4(pack_dir, raw_dir, normal_dir, review_dir=review_dir)
    raw_manifest = json.loads((raw_dir / RAW_MANIFEST).read_text(encoding="utf-8"))
    reviewed_at = datetime.fromisoformat(review["generated_at"])
    requested_at = datetime.fromisoformat(raw_manifest["generated_at"])
    if requested_at <= reviewed_at:
        raise ValueError("v4 model run must follow the source-first review")

    predictions = read_jsonl(normal_dir / PREDICTIONS)
    keyword = [predict_item(item) for item in items.values()]
    model_score = evaluate(items, labels, predictions)
    keyword_score = evaluate(items, labels, keyword)
    paired = paired_company_bootstrap(items, labels, predictions, keyword)
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    original_policy = json.loads(ORIGINAL_POLICY.read_text(encoding="utf-8"))
    thresholds = policy["thresholds"]
    if thresholds != original_policy["thresholds"]:
        raise ValueError("v4 frozen quality thresholds differ from the existing gate")
    detection = model_score["event_detection"]["f1"]
    macro = model_score["event_type_macro_f1_supported"]
    parse_rate = model_score["parse_error_items"] / len(items)
    checks = {
        "review_complete_before_model": (review["audit"]["reviewed_items"] == len(items)
                                          and review.get("model_outputs_previously_seen") is False
                                          and requested_at > reviewed_at),
        "company_sample_disjoint": source_audit["zero_overlap"],
        "all_items_scored": (model_score["prediction_audit"]["prediction_rows"] == len(items)
                              and model_audit["model_rows"] == len(items)),
        "parse_error_at_most_5_percent": parse_rate <= thresholds["max_parse_error_rate"],
        "event_detection_f1_at_least_0_70": detection >= thresholds["min_event_detection_f1"],
        "event_detection_f1_not_below_keyword": detection >= keyword_score["event_detection"]["f1"],
        "supported_type_macro_f1_at_least_0_60": (
            macro is not None and macro >= thresholds["min_supported_type_macro_f1"]),
    }
    result = {"pipeline_version": VERSION,
              "sample_role": "fourth_disjoint_company_evaluation_not_externally_preregistered",
              "pack_experiment_id": pack["experiment_id"],
              "source_disjointness": source_audit,
              "reference_basis": {"reviewer_kind": "single_ai_assistant",
                                  "review_before_v4_model_outputs": True,
                                  "model_outputs_previously_seen": False,
                                  "human_gold_ready": False,
                                  "score_interpretation": policy["score_interpretation"]},
              "model_run_audit": model_audit,
              "model": model_score,
              "keyword": keyword_score,
              "paired_company_bootstrap": paired,
              "research_signal_gate": {
                  "checks": checks, "passed": all(checks.values()),
                  "thresholds": thresholds,
                  "scope": "Agreement with a single source-first AI review on a disjoint company cohort; not human-ground-truth accuracy or market predictive validity."}}
    inputs = {str(path.resolve()): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        raw_dir / RAW_MANIFEST, raw_dir / RAW_NAME,
        normal_dir / NORMAL_MANIFEST, normal_dir / PREDICTIONS,
        review_dir / REVIEW_MANIFEST, review_dir / "reference_labels.jsonl",
        POLICY, ORIGINAL_POLICY,
        ROOT / "research/configs" / UNIVERSE_NAME,
        *(path for _, path in PRIOR_UNIVERSES))}
    code = {name: file_sha256(Path(__file__).with_name(name)) for name in (
        "score_wuhan_v4.py", "wuhan_protocol_v4.py", "wuhan_v4_review.py",
        "prepare_wuhan_v4_holdout.py", "signal_validation.py",
        "compare_holdout.py", "keyword_baseline.py")}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        report = stage / "v4_fourth_company_comparison.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
        manifest = {"pipeline_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "inputs": inputs, "code_sha256": code,
                    "artifacts": {report.name: {"sha256": file_sha256(report)}}}
        (stage / "v4_fourth_company_comparison_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path, nargs="?", default=PACK_DIR)
    parser.add_argument("raw_dir", type=Path, nargs="?", default=RAW_DIR)
    parser.add_argument("normal_dir", type=Path, nargs="?", default=NORMAL_DIR)
    parser.add_argument("review_dir", type=Path, nargs="?", default=REVIEW_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    result = score(args.pack_dir, args.raw_dir, args.normal_dir, args.review_dir, args.output_dir)
    print(json.dumps({"model_f1": result["model"]["event_detection"]["f1"],
                      "keyword_f1": result["keyword"]["event_detection"]["f1"],
                      "gate_passed": result["research_signal_gate"]["passed"],
                      "checks": result["research_signal_gate"]["checks"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
