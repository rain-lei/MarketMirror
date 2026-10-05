"""Build a source-bound snapshot of company replies visible before a policy event."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable

from .annotation_pack import SCHEMA as PACK_SCHEMA, VERSION as PACK_VERSION, canonical_hash, digest, stratum
from .prompt_contract import QUOTE_PROMPT_VERSION, prompt_path
from ..baselines.run_experiments import load_experiment, visibility_anchor
from ..data_pipeline.build_dataset import VERSION as QA_VERSION
from ..data_pipeline.market_data import strict_date
from ..data_pipeline.provenance import file_sha256


VERSION = "policy-event-qna-snapshot-v1"
SELECTION_RULE = "latest_company_confirmed_reply_per_asset_before_cutoff"
UNIVERSE_SELECTION_RULE = "sha256_rank_of_pre_event_question_active_issuers"
FROZEN_MODEL_PROTOCOL = {
    "provider_base_url": "http://aigw.dlut.edu.cn/v1",
    "model_id": "DeepSeek-V4-Flash-0731-W8A8",
    "prompt_version": QUOTE_PROMPT_VERSION,
    "temperature": 0.0,
}
SCHEMA_PATH = PACK_SCHEMA


def _timestamp(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "qa_database", "qa_source_sha256", "universe_config", "event_config",
                "event_id", "question_window_start", "snapshot_as_of", "selection_rule"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("event snapshot config has missing or unknown fields")
    for field in ("run_id", "qa_database", "universe_config", "event_config", "event_id"):
        if not isinstance(config[field], str) or not config[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    if not re.fullmatch(r"[0-9a-f]{64}", config["qa_source_sha256"]):
        raise ValueError("qa_source_sha256 must be a lowercase SHA-256")
    if config["selection_rule"] != SELECTION_RULE:
        raise ValueError("unsupported event snapshot selection rule")
    strict_date(config["question_window_start"])
    as_of = _timestamp(config["snapshot_as_of"], "snapshot_as_of")
    if as_of.utcoffset().total_seconds() != 8 * 60 * 60:
        raise ValueError("snapshot_as_of must use the source timezone, +08:00")
    return config


def select_latest_confirmed_replies(rows: Iterable[tuple], stock_codes: set[str],
                                    lower: datetime, as_of: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select only the latest fully visible question/reply pair per configured company."""
    latest: dict[str, tuple[datetime, str, dict[str, Any]]] = {}
    eligible_questions = 0
    eligible_replies = 0
    for row in rows:
        qa_id, code, question_at, question_text, reply_at, reply_text, question_eligible, reply_eligible = row
        if code not in stock_codes or question_eligible != 1 or not isinstance(question_text, str):
            continue
        try:
            question_time = _timestamp(question_at, "question_available_at")
        except (TypeError, ValueError):
            continue
        if not lower <= question_time <= as_of:
            continue
        eligible_questions += 1
        if (reply_eligible != 1 or not isinstance(reply_at, str) or not isinstance(reply_text, str)):
            continue
        try:
            reply_time = _timestamp(reply_at, "reply_available_at")
        except ValueError:
            continue
        if reply_time < question_time or reply_time > as_of:
            continue
        eligible_replies += 1
        segments = [{"source": "question", "text": question_text}, {"source": "reply", "text": reply_text}]
        item_id = digest(f"{qa_id}|reply|{PACK_VERSION}")
        item = {"item_id": item_id, "qa_id": qa_id, "stock_code": code,
                "split": "pre_event_snapshot", "stage": "reply",
                "available_at": reply_at, "question_available_at": question_at,
                "reply_available_at": reply_at, "selection_stratum": stratum(reply_text),
                "segments": segments, "source_text_sha256": canonical_hash(segments)}
        prior = latest.get(code)
        candidate_key = (reply_time, qa_id)
        if prior is None or candidate_key > (prior[0], prior[1]):
            latest[code] = (reply_time, qa_id, item)
    items = [value[2] for _, value in sorted(latest.items())]
    counts = {"eligible_question_rows": eligible_questions,
              "eligible_confirmed_reply_rows": eligible_replies,
              "selected_companies": len(items), "companies_without_reply_snapshot": len(stock_codes) - len(items)}
    return items, counts


def select_point_in_time_universe(codes: Iterable[str], seed: str, sample_size: int) -> list[str]:
    """Select a deterministic, outcome-blind sample from issuers with pre-event questions."""
    if not isinstance(seed, str) or not seed.strip():
        raise ValueError("universe seed must be a nonempty string")
    if type(sample_size) is not int or sample_size <= 0:
        raise ValueError("universe sample_size must be a positive integer")
    eligible = {code for code in codes if isinstance(code, str) and re.fullmatch(r"\d{6}", code)}
    if len(eligible) < sample_size:
        raise ValueError("pre-event question cohort is smaller than requested sample")
    return sorted(eligible, key=lambda code: (digest(f"{seed}|company|{code}"), code))[:sample_size]


def build_snapshot(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config_hash = file_sha256(config_path)
    config = load_config(config_path)
    database = (config_path.parent / config["qa_database"]).resolve()
    universe_path = (config_path.parent / config["universe_config"]).resolve()
    event_path = (config_path.parent / config["event_config"]).resolve()
    qa_manifest_path = database.parent / "run_manifest.json"
    qa_manifest = json.loads(qa_manifest_path.read_text(encoding="utf-8"))
    source_matches = [source for source in qa_manifest.get("sources", [])
                      if source.get("sha256") == config["qa_source_sha256"]]
    if len(source_matches) != 1:
        raise ValueError("configured QA source is absent or ambiguous in its run manifest")
    source = source_matches[0]
    source_path = Path(source["path"])
    if not source_path.is_absolute():
        source_path = (qa_manifest_path.parent / source_path).resolve()
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    stock_codes = universe.get("stock_codes")
    if (not isinstance(stock_codes, list) or not stock_codes
            or any(not isinstance(code, str) or not re.fullmatch(r"\d{6}", code) for code in stock_codes)
            or len(set(stock_codes)) != len(stock_codes)):
        raise ValueError("universe config must contain unique six-digit stock_codes")
    universe_selection = universe.get("selection")
    if universe_selection is not None:
        required_selection = {"selection_rule", "sample_size", "seed", "qa_source_sha256",
                              "question_window_start", "snapshot_as_of"}
        if (not isinstance(universe_selection, dict) or set(universe_selection) != required_selection
                or universe_selection.get("selection_rule") != UNIVERSE_SELECTION_RULE
                or type(universe_selection.get("sample_size")) is not int
                or universe_selection["sample_size"] != len(stock_codes)
                or not isinstance(universe_selection.get("seed"), str)
                or not universe_selection["seed"].strip()
                or universe_selection.get("qa_source_sha256") != config["qa_source_sha256"]
                or universe_selection.get("question_window_start") != config["question_window_start"]
                or universe_selection.get("snapshot_as_of") != config["snapshot_as_of"]):
            raise ValueError("point-in-time universe selection metadata differs from snapshot inputs")
    event_config = load_experiment(event_path)
    event = next((event for event in event_config["events"] if event["event_id"] == config["event_id"]), None)
    if event is None:
        raise ValueError("event_id is absent from event config")
    as_of = _timestamp(config["snapshot_as_of"], "snapshot_as_of")
    if as_of.date() >= strict_date(event["event_date"]):
        raise ValueError("pre-event snapshot must end before the nominal event date")
    visible_on, visibility_rule = visibility_anchor(event)
    visible_midnight = datetime.combine(visible_on, time(), tzinfo=as_of.tzinfo)
    if as_of >= visible_midnight:
        raise ValueError("snapshot cutoff must precede the event visibility anchor")
    lower = datetime.combine(strict_date(config["question_window_start"]), time(), tzinfo=as_of.tzinfo)
    if lower > as_of:
        raise ValueError("question window starts after snapshot cutoff")
    selected_prompt = prompt_path(FROZEN_MODEL_PROTOCOL["prompt_version"]).resolve()
    inputs = (config_path, database, qa_manifest_path, source_path, universe_path, event_path,
              SCHEMA_PATH, selected_prompt)
    input_hashes = {str(path): file_sha256(path) for path in inputs}
    if (qa_manifest.get("pipeline_version") != QA_VERSION
            or qa_manifest.get("artifacts", {}).get(database.name, {}).get("sha256") != input_hashes[str(database)]
            or input_hashes[str(source_path)] != config["qa_source_sha256"]):
        raise ValueError("QA database or configured original source differs from its manifest")
    protected_inputs = (config_path, database, qa_manifest_path, source_path, universe_path, event_path, SCHEMA_PATH)
    if any(output_dir == path or output_dir in path.parents for path in protected_inputs):
        raise ValueError("snapshot output must remain separate from source inputs")
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("snapshot output path exists and is not a directory")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty event snapshot directory")
    placeholders = ",".join("?" for _ in stock_codes)
    query = ("SELECT qa_id,stock_code,question_available_at,question_text,reply_available_at,reply_text,"
             "question_eligible,reply_eligible FROM qa_record WHERE source_file_hash=? AND stock_code IN (" + placeholders + ") "
             "AND question_available_at>=? AND question_available_at<=? ORDER BY stock_code,reply_available_at,qa_id")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        metadata = {key: json.loads(value) for key, value in
                    connection.execute("SELECT key,value_json FROM dataset_metadata")}
        if any(metadata.get(key) != qa_manifest.get(key) for key in
               ("dataset_id", "pipeline_version", "code_sha256", "feature_policy_sha256")):
            raise ValueError("QA database metadata differs from run manifest")
        universe_frame_companies = None
        if universe_selection is not None:
            candidate_rows = connection.execute(
                "SELECT DISTINCT stock_code FROM qa_record WHERE source_file_hash=? AND question_eligible=1 "
                "AND stock_code IS NOT NULL AND question_available_at>=? AND question_available_at<=?",
                (config["qa_source_sha256"], lower.isoformat(timespec="microseconds"),
                 as_of.isoformat(timespec="microseconds"))).fetchall()
            eligible_codes = [row[0] for row in candidate_rows]
            expected_codes = select_point_in_time_universe(
                eligible_codes, universe_selection["seed"], universe_selection["sample_size"])
            if stock_codes != expected_codes:
                raise ValueError("configured point-in-time universe differs from the source-derived sample")
            universe_frame_companies = len({code for code in eligible_codes
                                            if isinstance(code, str) and re.fullmatch(r"\d{6}", code)})
        rows = connection.execute(query, (config["qa_source_sha256"], *stock_codes,
                                          lower.isoformat(timespec="microseconds"),
                                          as_of.isoformat(timespec="microseconds")))
        items, counts = select_latest_confirmed_replies(rows, set(stock_codes), lower, as_of)
    if any(file_sha256(path) != digest_value for path, digest_value in
           ((Path(path), value) for path, value in input_hashes.items())):
        raise RuntimeError("event snapshot input changed during selection")
    code_paths = {"event_snapshot.py": Path(__file__), "annotation_pack.py": Path(__file__).with_name("annotation_pack.py"),
                  "signal_validation.py": Path(__file__).with_name("signal_validation.py")}
    code_hashes = {name: file_sha256(path) for name, path in code_paths.items()}
    experiment_id = canonical_hash({"input_sha256": input_hashes, "code_sha256": code_hashes,
                                    "selection_rule": SELECTION_RULE})
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        items_path = staging / "annotation_items.jsonl"
        with items_path.open("w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        manifest = {"pipeline_version": PACK_VERSION, "snapshot_pipeline_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(), "run_id": config["run_id"],
                    "experiment_id": experiment_id, "event_id": event["event_id"],
                    "frozen_model_protocol": {**FROZEN_MODEL_PROTOCOL,
                                              "prompt_sha256": input_hashes[str(selected_prompt)]},
                    "event_date": event["event_date"], "visibility_anchor_date": visible_on.isoformat(),
                    "visibility_alignment_rule": visibility_rule, "snapshot_as_of": config["snapshot_as_of"],
                    "dataset_id": qa_manifest["dataset_id"], "universe_size": len(stock_codes),
                    "selection_rule": SELECTION_RULE, "universe_selection": universe_selection,
                    "universe_frame_companies": universe_frame_companies, "source": source,
                    "config": config, "input_sha256": input_hashes, "code_sha256": code_hashes,
                    "counts": {"items": len(items), "companies": len({item["stock_code"] for item in items}),
                               "question_items": 0, "reply_items": len(items), **counts},
                    "limitations": [
                        "One latest company-confirmed reply per asset is a targeted pre-event snapshot, not a prevalence sample.",
                        "Question/reply timestamps are source availability proxies; first public appearance was not independently verified.",
                        "Companies without a reply visible by the cutoff receive no text item; no post-event reply is backfilled.",
                        "Model predictions require item-by-item assistant review under the selected protocol before exploratory signal comparison.",
                        "Raw question and reply text remains in this ignored local output and is not committed."]}
        manifest["artifacts"] = {items_path.name: {"sha256": file_sha256(items_path)}}
        manifest_path = staging / "annotation_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_snapshot(args.config, args.output_dir)
    print(json.dumps({"experiment_id": result["experiment_id"], "counts": result["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
