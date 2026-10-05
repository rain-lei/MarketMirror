"""Make a deterministic, availability-gated local annotation pack from one QA source."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.aggregate_qa_features import KEYWORDS
from ..data_pipeline.build_dataset import SOURCE_TIMEZONE, VERSION as QA_VERSION
from ..data_pipeline.market_data import strict_date
from ..data_pipeline.provenance import file_sha256

VERSION = "semantic-annotation-pack-v1"
STAGES = ("question", "reply")
STRATA = (*KEYWORDS, "none")
SPLITS = ("train", "validation", "test")
SCHEMA = Path(__file__).resolve().parents[1] / "data_contracts" / "semantic_signal.schema.json"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def split_for_company(stock_code: str, seed: str, percents: dict[str, int]) -> str:
    bucket = int(digest(f"{seed}|company|{stock_code}"), 16) % 100
    boundary = 0
    for name in SPLITS:
        boundary += percents[name]
        if bucket < boundary:
            return name
    raise AssertionError("split percentages do not sum to 100")


def stratum(text: str) -> str:
    return next((name for name, terms in KEYWORDS.items() if any(term in text for term in terms)), "none")


def visible_bounds(config: dict[str, Any]) -> tuple[str, str]:
    window = config["visibility_window"]
    lower = datetime.combine(strict_date(window["start_date"]), time(), tzinfo=SOURCE_TIMEZONE)
    upper = datetime.combine(strict_date(window["end_date"]) + timedelta(days=1), time(), tzinfo=SOURCE_TIMEZONE)
    if lower >= upper:
        raise ValueError("reversed visibility window")
    return lower.isoformat(timespec="microseconds"), upper.isoformat(timespec="microseconds")


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "qa_database", "qa_source_sha256", "visibility_window", "seed",
                "company_split_percent", "per_stage_stratum_quota", "selection_basis"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("annotation config has missing or unknown fields")
    for name in ("run_id", "qa_database", "seed", "selection_basis"):
        if not isinstance(config[name], str) or not config[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    if not isinstance(config["qa_source_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", config["qa_source_sha256"]):
        raise ValueError("qa_source_sha256 must be a source hash")
    if not isinstance(config["visibility_window"], dict) or set(config["visibility_window"]) != {"start_date", "end_date"}:
        raise ValueError("visibility_window requires start_date and end_date")
    visible_bounds(config)
    percents, quota = config["company_split_percent"], config["per_stage_stratum_quota"]
    if not isinstance(percents, dict) or set(percents) != set(SPLITS) or not isinstance(quota, dict) or set(quota) != set(SPLITS):
        raise ValueError("company split and quotas require train, validation and test")
    if any(type(v) is not int or v <= 0 for v in percents.values()) or sum(percents.values()) != 100:
        raise ValueError("company split percentages must be positive and sum to 100")
    if any(type(v) is not int or v <= 0 or v > 1000 for v in quota.values()):
        raise ValueError("per-stage/stratum quotas must be positive bounded integers")
    return config


def _candidate(qa_id: str, stock_code: str, stage: str, question_at: str, question_text: str,
               reply_at: str | None, reply_text: str | None, seed: str, split: str) -> tuple[int, dict[str, Any]]:
    segments = [{"source": "question", "text": question_text}]
    if stage == "reply":
        segments.append({"source": "reply", "text": reply_text})
    available_at = question_at if stage == "question" else reply_at
    item_id = digest(f"{qa_id}|{stage}|{VERSION}")
    rank = int(digest(f"{seed}|item|{item_id}"), 16)
    return rank, {"item_id": item_id, "qa_id": qa_id, "stock_code": stock_code,
                  "split": split, "stage": stage, "available_at": available_at,
                  "selection_stratum": stratum(question_text if stage == "question" else reply_text),
                  "segments": segments, "source_text_sha256": canonical_hash(segments)}


def sample_candidates(rows, config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep bounded top candidates per cell, then exclude duplicate visible contexts."""
    lower, upper = visible_bounds(config)
    quotas = config["per_stage_stratum_quota"]
    heaps = defaultdict(list)
    eligible = Counter()
    for qa_id, code, qt, question, rt, reply, reply_eligible in rows:
        if not isinstance(code, str) or not isinstance(question, str) or not lower <= qt < upper:
            continue
        split = split_for_company(code, config["seed"], config["company_split_percent"])
        for stage in STAGES:
            if stage == "reply" and not (reply_eligible == 1 and isinstance(reply, str) and rt is not None and lower <= rt < upper):
                continue
            rank, item = _candidate(qa_id, code, stage, qt, question, rt, reply, config["seed"], split)
            cell = (split, stage, item["selection_stratum"])
            eligible[cell] += 1
            heap = heaps[cell]
            capacity = max(32, quotas[split] * 8)
            member = (-rank, item["item_id"], item)
            if len(heap) < capacity:
                heapq.heappush(heap, member)
            elif member[:2] > heap[0][:2]:
                heapq.heapreplace(heap, member)
    selected, seen_context = [], set()
    counts = {}
    for split in SPLITS:
        for stage in STAGES:
            for category in STRATA:
                cell = (split, stage, category)
                count = 0
                for _, _, item in sorted(heaps[cell], key=lambda member: (-member[0], member[1])):
                    if item["source_text_sha256"] in seen_context:
                        continue
                    selected.append(item)
                    seen_context.add(item["source_text_sha256"])
                    count += 1
                    if count == quotas[split]:
                        break
                counts["|".join(cell)] = count
                if count < quotas[split]:
                    raise ValueError(f"insufficient unique visible texts in {cell}: {count}/{quotas[split]}")
    if len({item["qa_id"] for item in selected if item["stage"] == "question"}) != sum(v["stage"] == "question" for v in selected):
        raise AssertionError("repeated question occurrence")
    company_splits = defaultdict(set)
    for item in selected:
        company_splits[item["stock_code"]].add(item["split"])
    if any(len(value) != 1 for value in company_splits.values()):
        raise AssertionError("company split leakage")
    return selected, {"selected_" + k: v for k, v in counts.items()} | {"eligible_" + "|".join(k): v for k, v in eligible.items()}


def build_annotation_pack(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config_hash = file_sha256(config_path)
    config = load_config(config_path)
    database = (config_path.parent / config["qa_database"]).resolve()
    qa_manifest_path = database.parent / "run_manifest.json"
    qa_manifest = json.loads(qa_manifest_path.read_text(encoding="utf-8"))
    source = [s for s in qa_manifest["sources"] if s["sha256"] == config["qa_source_sha256"]]
    if len(source) != 1:
        raise ValueError("configured QA source is absent or ambiguous")
    source_path = Path(source[0]["path"]).resolve()
    inputs = {p: file_sha256(p) for p in (config_path, database, qa_manifest_path, source_path, SCHEMA)}
    if qa_manifest["pipeline_version"] != QA_VERSION or inputs[database] != qa_manifest["artifacts"][database.name]["sha256"]:
        raise ValueError("QA dataset version/hash differs from manifest")
    if inputs[source_path] != config["qa_source_sha256"]:
        raise ValueError("original QA source changed since dataset construction")
    if output_dir == database.parent or output_dir == source_path.parent or output_dir == config_path.parent:
        raise ValueError("annotation output must not replace an input directory")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty annotation output directory")
    lower, upper = visible_bounds(config)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        metadata = {k: json.loads(v) for k, v in conn.execute("SELECT key, value_json FROM dataset_metadata")}
        if any(metadata[k] != qa_manifest[k] for k in ("dataset_id", "pipeline_version", "code_sha256", "feature_policy_sha256")):
            raise ValueError("QA database metadata differs from manifest")
        rows = conn.execute(
            "SELECT qa_id,stock_code,question_available_at,question_text,reply_available_at,reply_text,reply_eligible "
            "FROM qa_record WHERE source_file_hash=? AND question_eligible=1 AND question_available_at>=? "
            "AND question_available_at<? ORDER BY qa_id", (config["qa_source_sha256"], lower, upper))
        items, counts = sample_candidates(rows, config)
    if any(file_sha256(p) != h for p, h in inputs.items()):
        raise RuntimeError("annotation input changed during selection")
    source_hashes = {"inputs": {str(p): h for p, h in inputs.items()},
                     "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in
                                     ("annotation_pack.py", "signal_validation.py")}}
    experiment_id = canonical_hash(source_hashes)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        with (staging / "annotation_items.jsonl").open("w", encoding="utf-8") as item_file, \
                (staging / "annotation_template.jsonl").open("w", encoding="utf-8") as template_file:
            for item in items:
                item_file.write(json.dumps(item, ensure_ascii=False) + "\n")
                template = {"item_id": item["item_id"], "source_text_sha256": item["source_text_sha256"],
                            "annotator_id": None, "status": "unlabeled", "events": [], "notes": ""}
                template_file.write(json.dumps(template, ensure_ascii=False) + "\n")
        manifest = {"pipeline_version": VERSION, "experiment_id": experiment_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(), "run_id": config["run_id"],
                    "dataset_id": qa_manifest["dataset_id"], "config": config, "source": source[0],
                    "counts": {"items": len(items), "companies": len({i["stock_code"] for i in items}),
                               "question_items": sum(i["stage"] == "question" for i in items),
                               "reply_items": sum(i["stage"] == "reply" for i in items), **counts},
                    **source_hashes, "limitations": [
                        "Keyword-stratified annotation sample is purposeful, not a prevalence estimate.",
                        "All template labels are empty; no human or model annotation quality has been measured.",
                        "Company splits prevent same-stock leakage but do not prove semantic independence across issuers.",
                        "Question/reply availability is based on source timestamps, not verified publication logs.",
                        "Raw public QA text stays in this ignored local output, never in tracked code files."]}
        manifest["artifacts"] = {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}
        (staging / "annotation_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_annotation_pack(args.config, args.output_dir)
    print(json.dumps({"experiment_id": result["experiment_id"], "counts": result["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
