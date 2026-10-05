"""Score the frozen v3 model on the disjoint Wuhan company cohort."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .assistant_review import POLICY_PATH as ORIGINAL_POLICY
from .audit_wuhan_validation_source import audit as audit_source
from .compare_holdout import paired_company_bootstrap
from .keyword_baseline import predict_item
from .signal_validation import evaluate, read_jsonl
from .wuhan_protocol_v3 import NORMAL_MANIFEST, PREDICTIONS, audit_v3, verify_pack
from .wuhan_validation_review import (MANIFEST_NAME as REVIEW_MANIFEST, POLICY,
                                       VALIDATION_PACK, load_blind_review)


VERSION = "wuhan-v3-independent-company-comparison-v1"
REVIEW_DIR = VALIDATION_PACK.parent / "wuhan_pre_event_validation_blind_review_v1"


def score(pack_dir: Path, raw_dir: Path, normal_dir: Path, output_dir: Path) -> dict:
    pack_dir, raw_dir, normal_dir, output_dir = (
        path.resolve() for path in (pack_dir, raw_dir, normal_dir, output_dir))
    if pack_dir != VALIDATION_PACK.resolve():
        raise ValueError("independent score requires the frozen disjoint company pack")
    if (output_dir in (pack_dir, raw_dir, normal_dir)
            or any(output_dir in path.parents or path in output_dir.parents
                   for path in (pack_dir, raw_dir, normal_dir))
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("independent score output needs a new directory separate from inputs")
    items, pack = verify_pack(pack_dir)
    labels, review = load_blind_review(pack_dir, REVIEW_DIR)
    source, _ = audit_source()
    if (source["validation_reply_items"] != len(items)
            or source["validation_experiment_id"] != pack["derived_protocol"]["source_pack_experiment_id"]
            or any(source[field] != 0 for field in ("stock_overlap", "qa_overlap", "visible_text_overlap"))):
        raise ValueError("independent company source audit differs from frozen cohort")
    raw_audit = audit_v3(pack_dir, raw_dir, normal_dir)
    raw_manifest = json.loads((raw_dir / "model_run_manifest.json").read_text(encoding="utf-8"))
    reviewed_at = datetime.fromisoformat(review["generated_at"])
    requested_at = datetime.fromisoformat(raw_manifest["generated_at"])
    if requested_at <= reviewed_at:
        raise ValueError("model run must follow the archived blind review")
    predictions = read_jsonl(normal_dir / PREDICTIONS)
    keyword = [predict_item(item) for item in items.values()]
    model_score = evaluate(items, labels, predictions)
    keyword_score = evaluate(items, labels, keyword)
    paired = paired_company_bootstrap(items, labels, predictions, keyword)
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    thresholds = policy["thresholds"]
    if thresholds != json.loads(ORIGINAL_POLICY.read_text(encoding="utf-8"))["thresholds"]:
        raise ValueError("independent thresholds differ from the original signal gate")
    detection = model_score["event_detection"]["f1"]
    macro = model_score["event_type_macro_f1_supported"]
    parse_rate = model_score["parse_error_items"] / len(items)
    checks = {
        "blind_reference_complete_before_model": review["audit"]["reviewed_items"] == len(items)
            and requested_at > reviewed_at,
        "source_disjoint": True,
        "all_items_scored": model_score["prediction_audit"]["prediction_rows"] == len(items)
            and raw_audit["model_rows"] == len(items),
        "parse_error_at_most_5_percent": parse_rate <= thresholds["max_parse_error_rate"],
        "event_detection_f1_at_least_0_70": detection >= thresholds["min_event_detection_f1"],
        "event_detection_f1_not_below_keyword": detection >= keyword_score["event_detection"]["f1"],
        "supported_type_macro_f1_at_least_0_60": (
            macro is not None and macro >= thresholds["min_supported_type_macro_f1"]),
    }
    result = {"pipeline_version": VERSION,
              "sample_role": "independent_company_validation",
              "pack_experiment_id": pack["experiment_id"],
              "source_audit": source,
              "reference_basis": {"reviewer_kind": "single_ai_assistant",
                                  "blind_to_v3_model_outputs": True,
                                  "independent_company_sample": True,
                                  "human_gold_ready": False,
                                  "score_interpretation": policy["score_interpretation"]},
              "model_run_audit": raw_audit, "model": model_score,
              "keyword": keyword_score, "paired_company_bootstrap": paired,
              "research_signal_gate": {"checks": checks, "passed": all(checks.values()),
                                       "thresholds": thresholds,
                                       "scope": "Independent-company agreement with one AI reference; no calibrated market or causal prediction claim."}}
    inputs = {str(path): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        raw_dir / "model_run_manifest.json", raw_dir / "model_raw_outputs.jsonl",
        normal_dir / NORMAL_MANIFEST, normal_dir / PREDICTIONS,
        REVIEW_DIR / REVIEW_MANIFEST, REVIEW_DIR / "reference_labels.jsonl",
        POLICY, ORIGINAL_POLICY)}
    code = {name: file_sha256(Path(__file__).with_name(name)) for name in (
        "score_wuhan_v3_validation.py", "wuhan_protocol_v3.py",
        "wuhan_validation_review.py", "audit_wuhan_validation_source.py",
        "signal_validation.py", "compare_holdout.py", "keyword_baseline.py")}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        report = stage / "independent_comparison.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
        manifest = {"pipeline_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "inputs": inputs, "code_sha256": code,
                    "artifacts": {report.name: {"sha256": file_sha256(report)}}}
        (stage / "independent_comparison_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("normal_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = score(args.pack_dir, args.raw_dir, args.normal_dir, args.output_dir)
    print(json.dumps({"model_f1": result["model"]["event_detection"]["f1"],
                      "keyword_f1": result["keyword"]["event_detection"]["f1"],
                      "research_signal_gate_passed": result["research_signal_gate"]["passed"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
