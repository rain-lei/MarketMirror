"""Build a temporally later semantic holdout disjoint from a development pack."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .annotation_pack import (SCHEMA, VERSION as PACK_VERSION, canonical_hash, load_config,
                              sample_candidates, visible_bounds)
from .prompt_contract import prompt_path
from .signal_validation import load_pack
from ..data_pipeline.build_dataset import VERSION as QA_VERSION
from ..data_pipeline.provenance import file_sha256

VERSION = "semantic-external-holdout-v1"
EXTRA_FIELDS = {"development_pack", "frozen_prompt_version", "frozen_prompt_sha256", "frozen_model_id"}


def load_holdout_config(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {
            "run_id", "qa_database", "qa_source_sha256", "visibility_window", "seed",
            "company_split_percent", "per_stage_stratum_quota", "selection_basis", *EXTRA_FIELDS}:
        raise ValueError("holdout config has missing or unknown fields")
    base = {key: value for key, value in raw.items() if key not in EXTRA_FIELDS}
    with tempfile.TemporaryDirectory() as tmp:
        validation_path = Path(tmp) / "base_config.json"
        validation_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        load_config(validation_path)
    for name in ("development_pack", "frozen_prompt_version", "frozen_prompt_sha256", "frozen_model_id"):
        if not isinstance(raw[name], str) or not raw[name].strip():
            raise ValueError(f"{name} must be nonempty text")
    if file_sha256(prompt_path(raw["frozen_prompt_version"])) != raw["frozen_prompt_sha256"]:
        raise ValueError("frozen prompt hash differs from the versioned prompt")
    return raw, base


def _separate(output: Path, source: Path) -> bool:
    return output != source and output not in source.parents and source not in output.parents


def filtered_rows(rows, excluded_stocks: set[str], excluded_qa: set[str],
                  excluded_contexts: set[str], audit: Counter):
    """Apply identity and exact-context exclusions before any sampling ranks."""
    for row in rows:
        audit["eligible_question_rows_before_exclusion"] += 1
        qa_id, stock_code, _, question, _, reply, reply_eligible = row
        if stock_code in excluded_stocks or qa_id in excluded_qa:
            audit["excluded_identity_rows"] += 1
            continue
        if not isinstance(question, str):
            audit["excluded_invalid_question_rows"] += 1
            continue
        visible = [canonical_hash([{"source": "question", "text": question}])]
        if reply_eligible == 1 and isinstance(reply, str):
            visible.append(canonical_hash([{"source": "question", "text": question},
                                           {"source": "reply", "text": reply}]))
        if any(context in excluded_contexts for context in visible):
            audit["excluded_exact_context_rows"] += 1
            continue
        audit["remaining_question_rows"] += 1
        yield row


def build_holdout_pack(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config, sampling_config = load_holdout_config(config_path)
    development_dir = (config_path.parent / config["development_pack"]).resolve()
    database = (config_path.parent / config["qa_database"]).resolve()
    qa_manifest_path = database.parent / "run_manifest.json"
    development, dev_manifest = load_pack(development_dir)
    if not development:
        raise ValueError("development pack cannot be empty")
    if any(not _separate(output_dir, source) for source in
           (config_path, development_dir, database, qa_manifest_path)):
        raise ValueError("holdout output must be separate from all inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty holdout output directory")
    qa_manifest = json.loads(qa_manifest_path.read_text(encoding="utf-8"))
    sources = [source for source in qa_manifest["sources"]
               if source["sha256"] == config["qa_source_sha256"]]
    if len(sources) != 1:
        raise ValueError("configured QA source is absent or ambiguous")
    source_path = Path(sources[0]["path"]).resolve()
    prompt_file = prompt_path(config["frozen_prompt_version"]).resolve()
    inputs = {path: file_sha256(path) for path in
              (config_path, database, qa_manifest_path, source_path, SCHEMA,
               development_dir / "annotation_manifest.json",
               development_dir / "annotation_items.jsonl", prompt_file)}
    if (qa_manifest["pipeline_version"] != QA_VERSION
            or inputs[database] != qa_manifest["artifacts"][database.name]["sha256"]
            or inputs[source_path] != config["qa_source_sha256"]
            or inputs[prompt_file] != config["frozen_prompt_sha256"]):
        raise ValueError("dataset, original QA source, or frozen prompt differs from declared input")
    if dev_manifest["source"]["sha256"] != config["qa_source_sha256"]:
        raise ValueError("development and holdout packs must use the same declared QA source")

    excluded_stocks = {item["stock_code"] for item in development.values()}
    excluded_qa = {item["qa_id"] for item in development.values()}
    excluded_contexts = {item["source_text_sha256"] for item in development.values()}
    lower, upper = visible_bounds(sampling_config)
    audit: Counter = Counter()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        metadata = {key: json.loads(value) for key, value in
                    conn.execute("SELECT key, value_json FROM dataset_metadata")}
        if any(metadata[key] != qa_manifest[key] for key in
               ("dataset_id", "pipeline_version", "code_sha256", "feature_policy_sha256")):
            raise ValueError("QA database metadata differs from its manifest")
        rows = conn.execute(
            "SELECT qa_id,stock_code,question_available_at,question_text,reply_available_at,reply_text,reply_eligible "
            "FROM qa_record WHERE source_file_hash=? AND question_eligible=1 AND question_available_at>=? "
            "AND question_available_at<? ORDER BY qa_id", (config["qa_source_sha256"], lower, upper))
        selected, counts = sample_candidates(
            filtered_rows(rows, excluded_stocks, excluded_qa, excluded_contexts, audit), sampling_config)
    if not selected or (any(item["stock_code"] in excluded_stocks or item["qa_id"] in excluded_qa
                            or item["source_text_sha256"] in excluded_contexts for item in selected)
                        or any(not lower <= item["available_at"] < upper for item in selected)):
        raise AssertionError("holdout contains a development identity/context or violates its visible window")
    if any(file_sha256(path) != digest for path, digest in inputs.items()):
        raise RuntimeError("holdout source changed during selection")
    code_hashes = {name: file_sha256(Path(__file__).with_name(name)) for name in
                   ("holdout_pack.py", "annotation_pack.py", "signal_validation.py")}
    identity = {"input_sha256": {str(path): digest for path, digest in inputs.items()},
                "code_sha256": code_hashes}
    manifest: dict[str, Any] = {
        "pipeline_version": PACK_VERSION, "holdout_builder_version": VERSION,
        "experiment_id": canonical_hash(identity), "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": config["run_id"], "dataset_id": qa_manifest["dataset_id"],
        "config": config, "source": sources[0],
        "development_experiment_id": dev_manifest["experiment_id"],
        "exclusion_policy": "later visible window; development company, QA identity and exact visible context excluded before selection",
        "counts": {"items": len(selected), "companies": len({item["stock_code"] for item in selected}),
                   "question_items": sum(item["stage"] == "question" for item in selected),
                   "reply_items": sum(item["stage"] == "reply" for item in selected),
                   "development_companies_excluded": len(excluded_stocks),
                   "development_contexts_excluded": len(excluded_contexts),
                   **dict(audit), **counts},
        **identity,
        "limitations": [
            "This is an untouched external cohort only while its labels and model outputs are not used for prompt or rule development.",
            "Internal train/validation/test names are sampling groups; every selected item is external to the development pack.",
            "Keyword-stratified selection is not a population prevalence estimate.",
            "Company and exact-text separation do not rule out semantically equivalent disclosures across companies.",
            "Source timestamps are availability proxies, not independent publication-log evidence.",
            "Blank human labels and a frozen prompt do not establish semantic accuracy."]}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        with (staging / "annotation_items.jsonl").open("w", encoding="utf-8") as item_file, \
                (staging / "annotation_template.jsonl").open("w", encoding="utf-8") as label_file:
            for item in selected:
                item_file.write(json.dumps(item, ensure_ascii=False) + "\n")
                label_file.write(json.dumps({
                    "item_id": item["item_id"], "source_text_sha256": item["source_text_sha256"],
                    "annotator_id": None, "status": "unlabeled", "events": [], "notes": ""
                }, ensure_ascii=False) + "\n")
        manifest["artifacts"] = {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()}
        (staging / "annotation_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_holdout_pack(args.config, args.output_dir)
    print(json.dumps({"experiment_id": manifest["experiment_id"],
                      "counts": manifest["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
