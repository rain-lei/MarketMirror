"""Score a revised Wuhan prompt on the seen development items only.

This report can guide protocol repair but never grants Agent signal eligibility.
An untouched company cohort needs a separately frozen validation and review.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .assistant_review import POLICY_PATH, load_review
from .compare_holdout import paired_company_bootstrap
from .keyword_baseline import predict_item
from .signal_validation import evaluate, load_pack, read_jsonl
from .wuhan_protocol_v3 import (NORMAL_MANIFEST, PREDICTIONS, audit_v3, verify_pack)


VERSION = "wuhan-v3-development-comparison-v1"
ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT_PACK = ROOT / "research_outputs/wuhan_pre_event_pit_snapshot_2020_v3"
DEVELOPMENT_REVIEW = ROOT / "research_outputs/wuhan_pre_event_assistant_review_v3"
DEVELOPMENT_KEYWORD = ROOT / "research_outputs/wuhan_pre_event_pit_keywords_2020_v3"


def score(pack_dir: Path, raw_dir: Path, normal_dir: Path, output_dir: Path) -> dict:
    pack_dir, raw_dir, normal_dir, output_dir = (
        path.resolve() for path in (pack_dir, raw_dir, normal_dir, output_dir))
    items, derived = verify_pack(pack_dir)
    source = derived["derived_protocol"]
    if Path(source["source_pack_directory"]) != DEVELOPMENT_PACK.resolve():
        raise ValueError("development score is restricted to the previously reviewed company pack")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("development score output must be a new empty directory")
    raw_audit = audit_v3(pack_dir, raw_dir, normal_dir)
    gold, review = load_review(DEVELOPMENT_PACK, DEVELOPMENT_REVIEW)
    old_items, old_pack = load_pack(DEVELOPMENT_PACK)
    if items != old_items or source["source_pack_experiment_id"] != old_pack["experiment_id"]:
        raise ValueError("derived pack differs from development review source")
    keyword_manifest = json.loads((DEVELOPMENT_KEYWORD / "keyword_manifest.json").read_text(encoding="utf-8"))
    keyword_path = DEVELOPMENT_KEYWORD / "keyword_predictions.jsonl"
    keyword = read_jsonl(keyword_path)
    if (keyword_manifest.get("pack_experiment_id") != old_pack["experiment_id"]
            or keyword_manifest.get("artifacts", {}).get(keyword_path.name, {}).get("sha256")
            != file_sha256(keyword_path)
            or keyword != [predict_item(item) for item in old_items.values()]):
        raise ValueError("development keyword comparator differs from source")
    predictions = read_jsonl(normal_dir / PREDICTIONS)
    model_score = evaluate(items, gold, predictions)
    keyword_score = evaluate(items, gold, keyword)
    paired = paired_company_bootstrap(items, gold, predictions, keyword)
    thresholds = json.loads(POLICY_PATH.read_text(encoding="utf-8"))["thresholds"]
    detection = model_score["event_detection"]["f1"]
    macro = model_score["event_type_macro_f1_supported"]
    parse_rate = model_score["parse_error_items"] / len(items)
    checks = {"all_items_scored": model_score["prediction_audit"]["prediction_rows"] == len(items),
              "parse_error_at_most_5_percent": parse_rate <= thresholds["max_parse_error_rate"],
              "event_detection_f1_at_least_0_70": detection >= thresholds["min_event_detection_f1"],
              "event_detection_f1_not_below_keyword": detection >= keyword_score["event_detection"]["f1"],
              "supported_type_macro_f1_at_least_0_60": (
                  macro is not None and macro >= thresholds["min_supported_type_macro_f1"])}
    result = {"pipeline_version": VERSION, "sample_role": "seen_development_only",
              "source_pack_experiment_id": old_pack["experiment_id"],
              "derived_pack_experiment_id": derived["experiment_id"],
              "review_basis": {"protocol": review["protocol"], "independent_reference": False,
                               "gold_ready": False, "model_outputs_previously_seen": True},
              "model_run_audit": raw_audit, "model": model_score,
              "keyword": keyword_score, "paired_company_bootstrap": paired,
              "development_targets": {"checks": checks, "met": all(checks.values()),
                                      "agent_signal_eligible": False,
                                      "reason": "Prompt and policy were developed after seeing this reference; independent validation is required."}}
    inputs = {str(path): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        raw_dir / "model_run_manifest.json", raw_dir / "model_raw_outputs.jsonl",
        normal_dir / NORMAL_MANIFEST, normal_dir / PREDICTIONS,
        DEVELOPMENT_PACK / "annotation_manifest.json",
        DEVELOPMENT_PACK / "annotation_items.jsonl",
        DEVELOPMENT_REVIEW / "assistant_review_manifest.json",
        DEVELOPMENT_REVIEW / "reference_labels.jsonl",
        DEVELOPMENT_KEYWORD / "keyword_manifest.json", keyword_path, POLICY_PATH)}
    code = {name: file_sha256(Path(__file__).with_name(name)) for name in (
        "score_wuhan_v3_development.py", "wuhan_protocol_v3.py", "signal_validation.py",
        "compare_holdout.py", "assistant_review.py", "keyword_baseline.py")}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        report = staging / "development_comparison.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
        manifest = {"pipeline_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "inputs": inputs, "code_sha256": code,
                    "artifacts": {report.name: {"sha256": file_sha256(report)}}}
        (staging / "development_comparison_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
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
                      "development_targets_met": result["development_targets"]["met"],
                      "agent_signal_eligible": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
