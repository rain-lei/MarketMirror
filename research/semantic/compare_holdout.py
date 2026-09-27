"""Score a frozen semantic holdout against adjudicated gold and a keyword control."""

from __future__ import annotations

import argparse
import json
import random
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .audit_model_run import audit_model_run
from .review_workflow import validate_adjudication
from .signal_validation import binary_counts, evaluate, load_pack, read_jsonl


VERSION = "semantic-holdout-comparison-v1"
BOOTSTRAP_REPLICATES = 5000
BOOTSTRAP_SEED = 20260927
FROZEN_GATEWAY = "http://aigw.dlut.edu.cn/v1"


def _percentile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[min(lower + 1, len(sorted_values) - 1)] * weight


def paired_company_bootstrap(items: dict[str, dict[str, Any]], gold: list[dict[str, Any]],
                             model: list[dict[str, Any]], keyword: list[dict[str, Any]],
                             replicates: int = BOOTSTRAP_REPLICATES,
                             seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """Resample companies, retaining all question/reply items of each sampled company."""
    if replicates < 1:
        raise ValueError("bootstrap replicates must be positive")
    truth = {row["item_id"]: bool(row["events"]) for row in gold}
    model_presence = {row["item_id"]: bool(row["events"]) for row in model}
    keyword_presence = {row["item_id"]: bool(row["events"]) for row in keyword}
    if set(truth) != set(items) or set(model_presence) != set(items) or set(keyword_presence) != set(items):
        raise ValueError("paired bootstrap requires complete matching item identities")
    groups: dict[str, list[str]] = defaultdict(list)
    for item_id, item in items.items():
        groups[item["stock_code"]].append(item_id)
    companies = sorted(groups)
    if not companies:
        raise ValueError("paired bootstrap requires at least one company")
    def f1(item_ids: list[str], presence: dict[str, bool]) -> float:
        return binary_counts([truth[item_id] for item_id in item_ids],
                             [presence[item_id] for item_id in item_ids])["f1"]
    all_ids = sorted(items)
    point_model = f1(all_ids, model_presence)
    point_keyword = f1(all_ids, keyword_presence)
    generator = random.Random(seed)
    differences = []
    for _ in range(replicates):
        sampled_ids = [item_id for _ in companies
                       for item_id in groups[generator.choice(companies)]]
        differences.append(f1(sampled_ids, model_presence) - f1(sampled_ids, keyword_presence))
    differences.sort()
    return {"unit": "company", "companies": len(companies), "items": len(items),
            "replicates": replicates, "seed": seed,
            "metric": "model minus keyword event-detection F1",
            "model_f1": point_model, "keyword_f1": point_keyword,
            "difference": point_model - point_keyword,
            "percentile_95_interval": [_percentile(differences, 0.025),
                                       _percentile(differences, 0.975)],
            "interpretation": "Paired descriptive uncertainty for this topic-stratified sample, not a population prevalence or causal inference interval."}


def _verified_artifact(directory: Path, manifest_name: str, artifact_name: str,
                       manifest_key: str | None, experiment_id: str) -> tuple[dict[str, Any], Path]:
    manifest = json.loads((directory / manifest_name).read_text(encoding="utf-8"))
    if manifest_key is not None and manifest.get(manifest_key) != experiment_id:
        raise ValueError(f"{manifest_name} belongs to another annotation pack")
    artifact_path = directory / artifact_name
    if file_sha256(artifact_path) != manifest["artifacts"][artifact_name]["sha256"]:
        raise ValueError(f"{artifact_name} differs from its manifest")
    return manifest, artifact_path


def compare_holdout(pack_dir: Path, comparison_dir: Path, gold_dir: Path,
                    raw_model_dir: Path, normalized_model_dir: Path,
                    keyword_dir: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, comparison_dir, gold_dir, raw_model_dir, normalized_model_dir, keyword_dir, output_dir = (
        path.resolve() for path in (pack_dir, comparison_dir, gold_dir, raw_model_dir,
                                    normalized_model_dir, keyword_dir, output_dir))
    sources = (pack_dir, comparison_dir, gold_dir, raw_model_dir, normalized_model_dir, keyword_dir)
    if len(set(sources)) != len(sources) or any(output_dir == path or output_dir in path.parents
                                               or path in output_dir.parents for path in sources):
        raise ValueError("all input and output directories must be separate")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty comparison output directory")
    items, pack_manifest = load_pack(pack_dir)
    experiment_id = pack_manifest["experiment_id"]
    config = pack_manifest["config"]

    comparison_manifest, comparison_path = _verified_artifact(
        comparison_dir, "comparison_manifest.json", "comparison_results.json",
        None, experiment_id)
    if comparison_manifest["code_sha256"] != file_sha256(Path(__file__).with_name("review_workflow.py")):
        raise ValueError("review comparison code differs from the current verified version")
    for path_string, digest in comparison_manifest["inputs"].items():
        if file_sha256(Path(path_string)) != digest:
            raise ValueError("reviewer input differs from comparison manifest")
    for name, info in comparison_manifest["artifacts"].items():
        if file_sha256(comparison_dir / name) != info["sha256"]:
            raise ValueError("comparison artifact differs from manifest")
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    if comparison["annotation_experiment_id"] != experiment_id:
        raise ValueError("review comparison belongs to another annotation pack")

    gold_manifest, gold_path = _verified_artifact(
        gold_dir, "gold_manifest.json", "gold_labels.jsonl", "annotation_experiment_id", experiment_id)
    if (gold_manifest["comparison_manifest_sha256"] != file_sha256(comparison_dir / "comparison_manifest.json")
            or gold_manifest["review"]["status"] != "all_items_adjudicated"
            or gold_manifest["code_sha256"] != file_sha256(Path(__file__).with_name("review_workflow.py"))):
        raise ValueError("gold does not match the completed independent review")
    gold = read_jsonl(gold_path)
    validate_adjudication(items, comparison, gold)

    raw_manifest, raw_path = _verified_artifact(
        raw_model_dir, "model_run_manifest.json", "model_raw_outputs.jsonl",
        "pack_experiment_id", experiment_id)
    if (raw_manifest["model_id"] != config["frozen_model_id"]
            or raw_manifest["prompt_version"] != config["frozen_prompt_version"]
            or raw_manifest["provider_base_url"] != FROZEN_GATEWAY
            or raw_manifest["temperature"] != 0
            or raw_manifest["input_sha256"]["prompt"] != config["frozen_prompt_sha256"]
            or raw_manifest["input_sha256"]["annotation_manifest"]
            != file_sha256(pack_dir / "annotation_manifest.json")
            or raw_manifest["input_sha256"]["annotation_items"]
            != file_sha256(pack_dir / "annotation_items.jsonl")
            or raw_manifest["requested_rows"] != len(items)
            or raw_manifest["remaining_rows"] != 0
            or raw_manifest["model_rows"] != len(items)):
        raise ValueError("raw model run differs from the frozen complete protocol")

    normalized_manifest, model_path = _verified_artifact(
        normalized_model_dir, "normalization_manifest.json", "model_predictions.jsonl",
        "pack_experiment_id", experiment_id)
    normalized_inputs = normalized_manifest["input_sha256"]
    if (normalized_inputs["annotation_manifest"] != file_sha256(pack_dir / "annotation_manifest.json")
            or normalized_inputs["annotation_items"] != file_sha256(pack_dir / "annotation_items.jsonl")
            or normalized_inputs["raw_outputs"] != file_sha256(raw_path)
            or normalized_inputs["model_run_manifest"] != file_sha256(raw_model_dir / "model_run_manifest.json")
            or normalized_manifest["model_ids"] != [config["frozen_model_id"]]
            or normalized_manifest["prompt_versions"] != [config["frozen_prompt_version"]]
            or normalized_manifest["model_rows"] != len(items)):
        raise ValueError("normalized model predictions differ from the frozen raw run")
    model_audit = audit_model_run(pack_dir, raw_model_dir, normalized_model_dir)
    if (not model_audit["scope"]["full_pack_requested"]
            or model_audit["raw"]["rows"] != len(items)
            or model_audit["normalized"]["rows"] != len(items)):
        raise ValueError("model run is not complete after independent raw-response audit")

    keyword_manifest, keyword_path = _verified_artifact(
        keyword_dir, "keyword_manifest.json", "keyword_predictions.jsonl",
        "pack_experiment_id", experiment_id)
    if (keyword_manifest["input_sha256"]["annotation_manifest.json"]
            != file_sha256(pack_dir / "annotation_manifest.json")
            or keyword_manifest["input_sha256"]["annotation_items.jsonl"]
            != file_sha256(pack_dir / "annotation_items.jsonl")):
        raise ValueError("keyword baseline differs from the holdout pack")

    model, keyword = read_jsonl(model_path), read_jsonl(keyword_path)
    model_score, keyword_score = evaluate(items, gold, model), evaluate(items, gold, keyword)
    if (model_score["label_audit"]["reviewed_items"] != len(items)
            or model_score["prediction_audit"]["prediction_rows"] != len(items)
            or keyword_score["prediction_audit"]["prediction_rows"] != len(items)
            or model_score["status"] != "scored" or keyword_score["status"] != "scored"):
        raise ValueError("holdout comparison requires complete gold and predictions")
    paired = paired_company_bootstrap(items, gold, model, keyword)
    model_f1 = model_score["event_detection"]["f1"]
    keyword_f1 = keyword_score["event_detection"]["f1"]
    macro_f1 = model_score["event_type_macro_f1_supported"]
    parse_rate = model_score["parse_error_items"] / len(items)
    gates = {"complete_independent_review": True,
             "all_model_rows": True,
             "parse_error_at_most_5_percent": parse_rate <= 0.05,
             "event_detection_f1_at_least_0_70": model_f1 >= 0.70,
             "event_detection_f1_not_below_keyword": model_f1 >= keyword_f1,
             "supported_type_macro_f1_at_least_0_60": macro_f1 is not None and macro_f1 >= 0.60}
    source_paths = (pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
                    comparison_dir / "comparison_manifest.json", comparison_path,
                    gold_dir / "gold_manifest.json", gold_path,
                    raw_model_dir / "model_run_manifest.json", raw_path,
                    normalized_model_dir / "normalization_manifest.json", model_path,
                    keyword_dir / "keyword_manifest.json", keyword_path)
    source_hashes = {str(path): file_sha256(path) for path in source_paths}
    result = {"experiment_id": experiment_id, "items": len(items),
              "model_run_audit": model_audit,
              "model": model_score, "keyword": keyword_score, "paired_company_bootstrap": paired,
              "research_signal_gate": {"checks": gates, "passed": all(gates.values()),
                                       "scope": "Eligibility for a controlled Agent signal ablation only; not real investor calibration, causal historical reproduction, or regulatory forecasting."},
              "limitations": ["Human reviewer identity and substantive semantic correctness require external oversight.",
                              "Topic-stratified selection is not a population prevalence sample.",
                              "The paired interval describes resampling uncertainty for selected companies; it does not repair selection bias."]}
    if any(file_sha256(Path(path)) != digest for path, digest in source_hashes.items()):
        raise RuntimeError("comparison input changed during scoring")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        report = staging / "holdout_comparison.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "pack_experiment_id": experiment_id, "input_sha256": source_hashes,
                    "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in
                                    ("compare_holdout.py", "signal_validation.py", "review_workflow.py",
                                     "audit_model_run.py", "parse_model_outputs.py", "quote_grounding.py",
                                     "prompt_contract.py")},
                    "artifacts": {report.name: {"sha256": file_sha256(report)}}}
        (staging / "comparison_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--comparison-dir", type=Path, required=True)
    parser.add_argument("--gold-dir", type=Path, required=True)
    parser.add_argument("--raw-model-dir", type=Path, required=True)
    parser.add_argument("--normalized-model-dir", type=Path, required=True)
    parser.add_argument("--keyword-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = compare_holdout(args.pack_dir, args.comparison_dir, args.gold_dir,
                             args.raw_model_dir, args.normalized_model_dir,
                             args.keyword_dir, args.output_dir)
    print(json.dumps({"items": result["items"],
                      "model_f1": result["model"]["event_detection"]["f1"],
                      "keyword_f1": result["keyword"]["event_detection"]["f1"],
                      "gate_passed": result["research_signal_gate"]["passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
