"""Build and verify a source-bound sixth-cohort Q&A pack after v6 freezes."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, time, timezone
from pathlib import Path

from ..baselines.run_experiments import load_experiment, visibility_anchor
from ..data_pipeline.market_data import strict_date
from ..data_pipeline.provenance import file_sha256
from .annotation_pack import SCHEMA, VERSION as PACK_VERSION, canonical_hash, digest
from .prepare_wuhan_v6_holdout import (
    CONFIG_DIR, POLICY, PROMPT, ROOT, SNAPSHOT_NAME, UNIVERSE_NAME, _json,
    _timestamp, _topic_names, verify_existing,
)
from .signal_validation import load_pack


VERSION = "wuhan-visible-question-topic-snapshot-v6"
PROMPT_VERSION = "wuhan-company-confirmed-v6"
MODEL = "DeepSeek-V4-Flash-0731-W8A8"
BASE_URL = "http://aigw.dlut.edu.cn/v1"
MODULE = Path(__file__).resolve()
UNIVERSE_PATH = CONFIG_DIR / UNIVERSE_NAME
CONFIG_PATH = CONFIG_DIR / SNAPSHOT_NAME
OUTPUT_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v6_holdout_source_v1"
ITEMS_NAME = "annotation_items.jsonl"
MANIFEST_NAME = "annotation_manifest.json"


def select_fixed_items(rows: list[tuple], selected: list[dict], cutoff: str) -> list[dict]:
    """Materialize only frozen QA identifiers, checking question topic and visibility."""
    by_id = {row[0]: row for row in rows}
    if len(by_id) != len(rows) or len(selected) != len({p["qa_id"] for p in selected}):
        raise ValueError("selected QA identities are missing or repeated")
    cutoff_at = _timestamp(cutoff)
    items: list[dict] = []
    for pair in selected:
        row = by_id.get(pair["qa_id"])
        if row is None:
            raise ValueError("frozen QA identifier is absent from source")
        (qa_id, code, question_at, question, reply_at, reply,
         question_eligible, reply_eligible) = row
        if (code != pair["stock_code"] or question_at != pair["question_available_at"]
                or reply_at != pair["reply_available_at"] or question_eligible != 1
                or reply_eligible != 1 or not isinstance(question, str) or not question.strip()
                or not isinstance(reply, str) or not reply.strip()
                or pair["question_topic"] not in _topic_names(question)):
            raise ValueError("frozen question topic, reply or selected metadata differs")
        if not _timestamp(question_at) <= _timestamp(reply_at) <= cutoff_at:
            raise ValueError("frozen pair was not visible by the pre-event cutoff")
        segments = [{"source": "question", "text": question},
                    {"source": "reply", "text": reply}]
        items.append({
            "item_id": digest(f"{qa_id}|reply|{PACK_VERSION}"),
            "qa_id": qa_id, "stock_code": code,
            "split": "pre_event_snapshot", "stage": "reply",
            "available_at": reply_at,
            "question_available_at": question_at,
            "reply_available_at": reply_at,
            "question_topic": pair["question_topic"],
            "selection_stratum": pair["question_topic"],
            "segments": segments,
            "source_text_sha256": canonical_hash(segments),
        })
    if (len({item["item_id"] for item in items}) != len(items)
            or len({item["stock_code"] for item in items}) != len(items)):
        raise ValueError("sixth snapshot repeated a QA pair or company")
    return items


def _inputs(config_path: Path, universe_path: Path) -> tuple[dict[str, str], Path, dict]:
    config = _json(config_path)
    database = (config_path.parent / config["qa_database"]).resolve()
    qa_manifest_path = database.parent / "run_manifest.json"
    qa_manifest = _json(qa_manifest_path)
    source_matches = [entry for entry in qa_manifest.get("sources", [])
                      if entry.get("sha256") == config["qa_source_sha256"]]
    if len(source_matches) != 1:
        raise ValueError("sixth source workbook is absent or ambiguous")
    source_path = Path(source_matches[0]["path"])
    if not source_path.is_absolute():
        source_path = (qa_manifest_path.parent / source_path).resolve()
    event_path = (config_path.parent / config["event_config"]).resolve()
    event_config = load_experiment(event_path)
    event = next((row for row in event_config["events"]
                  if row["event_id"] == config["event_id"]), None)
    if event is None:
        raise ValueError("sixth configured event is absent")
    cutoff = _timestamp(config["snapshot_as_of"])
    visible_on, _ = visibility_anchor(event)
    if (cutoff.date() >= strict_date(event["event_date"])
            or cutoff >= datetime.combine(visible_on, time(), tzinfo=cutoff.tzinfo)):
        raise ValueError("sixth Q&A cutoff must precede event visibility")
    paths = (config_path, universe_path, database, qa_manifest_path, source_path,
             event_path, SCHEMA, PROMPT, POLICY)
    hashes = {str(path.resolve()): file_sha256(path) for path in paths}
    if (hashes[str(source_path.resolve())] != config["qa_source_sha256"]
            or qa_manifest.get("artifacts", {}).get(database.name, {}).get("sha256")
            != hashes[str(database)]):
        raise ValueError("sixth source database or workbook hash differs")
    universe = _json(universe_path)
    if universe.get("selection_audit", {}).get("evaluation_policy_sha256") != hashes[str(POLICY)]:
        raise ValueError("sixth evaluation policy changed after company selection")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        metadata = {name: json.loads(value) for name, value in
                    connection.execute("SELECT key,value_json FROM dataset_metadata")}
    if any(metadata.get(name) != qa_manifest.get(name) for name in
           ("dataset_id", "pipeline_version", "code_sha256", "feature_policy_sha256")):
        raise ValueError("sixth QA database metadata differs from its source manifest")
    return hashes, database, qa_manifest


def _code_hashes() -> dict[str, str]:
    hashes = {name: file_sha256(MODULE.with_name(name)) for name in (
        "wuhan_v6_snapshot.py", "prepare_wuhan_v6_holdout.py",
        "wuhan_v6_gate.py", "annotation_pack.py", "signal_validation.py")}
    hashes["run_experiments.py"] = file_sha256(
        MODULE.parents[1] / "baselines/run_experiments.py")
    hashes["market_data.py"] = file_sha256(
        MODULE.parents[1] / "data_pipeline/market_data.py")
    return hashes


def _output_path(path: Path, inputs: dict[str, str]) -> Path:
    path = path.resolve()
    outputs = (ROOT / "research_outputs").resolve()
    protected = [Path(name).resolve() for name in inputs]
    if (path == outputs or outputs not in path.parents
            or any(path == item or path in item.parents or item in path.parents
                   for item in protected)
            or path.exists() and any(path.iterdir())):
        raise ValueError("sixth snapshot output must be a new separate research_outputs child")
    return path


def _source_rows(database: Path, source_hash: str, selected: list[dict]) -> list[tuple]:
    placeholders = ",".join("?" for _ in selected)
    query = (
        "SELECT qa_id,stock_code,question_available_at,question_text,"
        "reply_available_at,reply_text,question_eligible,reply_eligible "
        "FROM qa_record WHERE source_file_hash=? AND qa_id IN (" + placeholders + ")"
    )
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute(query, (source_hash,
                                          *(pair["qa_id"] for pair in selected))).fetchall()


def build_snapshot(config_path: Path = CONFIG_PATH, universe_path: Path = UNIVERSE_PATH,
                   output_dir: Path = OUTPUT_DIR) -> dict:
    config_path, universe_path = config_path.resolve(), universe_path.resolve()
    verify_existing(universe_path, config_path)
    config = _json(config_path)
    universe = _json(universe_path)
    inputs, database, qa_manifest = _inputs(config_path, universe_path)
    output_dir = _output_path(output_dir, inputs)
    selected = universe["selected_pairs"]
    if len(selected) != universe["selection_audit"]["sample_size"]:
        raise ValueError("sixth fixed pair count differs from quota")
    rows = _source_rows(database, config["qa_source_sha256"], selected)
    items = select_fixed_items(rows, selected, config["snapshot_as_of"])
    if len(items) != len(selected):
        raise ValueError("sixth selected source coverage is incomplete")
    if any(file_sha256(Path(name)) != expected for name, expected in inputs.items()):
        raise RuntimeError("sixth source input changed during item construction")
    code = _code_hashes()
    experiment_id = canonical_hash({"pipeline_version": VERSION, "input_sha256": inputs,
                                    "code_sha256": code,
                                    "selection_rule": universe["selection_audit"]["selection_rule"]})
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        item_path = stage / ITEMS_NAME
        with item_path.open("w", encoding="utf-8", newline="\n") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        manifest = {
            "pipeline_version": PACK_VERSION,
            "snapshot_pipeline_version": VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": config["run_id"],
            "experiment_id": experiment_id,
            "sample_role": "sixth_disjoint_company_evaluation",
            "frozen_model_protocol": {
                "provider_base_url": BASE_URL, "model_id": MODEL,
                "prompt_version": PROMPT_VERSION,
                "prompt_sha256": inputs[str(PROMPT.resolve())],
                "temperature": 0.0,
            },
            "selection_rule": universe["selection_audit"]["selection_rule"],
            "selection_audit": universe["selection_audit"],
            "snapshot_as_of": config["snapshot_as_of"],
            "source": next(entry for entry in qa_manifest["sources"]
                           if entry["sha256"] == config["qa_source_sha256"]),
            "config": config,
            "input_sha256": inputs,
            "code_sha256": code,
            "counts": {"items": len(items), "companies": len(items),
                       "question_items": 0, "reply_items": len(items),
                       "selected_topic_replies": {
                           name: sum(item["question_topic"] == name for item in items)
                           for name in universe["selection_audit"]["topic_quotas"]}},
            "artifacts": {ITEMS_NAME: {"sha256": file_sha256(item_path)}},
            "limitations": [
                "Company-disjoint, answer-visible question-topic enrichment is not a market event prevalence sample.",
                "Source timestamps proxy first public availability; original appearance is not independently verified.",
                "A question topic does not guarantee a company-confirmed event of that type.",
                "Reference review and model evaluation must follow this frozen source in that order.",
            ],
        }
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if (_code_hashes() != code
                or any(file_sha256(Path(name)) != expected for name, expected in inputs.items())):
            raise RuntimeError("sixth snapshot source or code changed during output staging")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    verify_snapshot(config_path, universe_path, output_dir)
    return manifest


def verify_snapshot(config_path: Path = CONFIG_PATH, universe_path: Path = UNIVERSE_PATH,
                    output_dir: Path = OUTPUT_DIR) -> tuple[dict, dict]:
    config_path, universe_path, output_dir = (path.resolve() for path in
                                              (config_path, universe_path, output_dir))
    verify_existing(universe_path, config_path)
    inputs, database, _ = _inputs(config_path, universe_path)
    items, manifest = load_pack(output_dir)
    universe = _json(universe_path)
    code = _code_hashes()
    expected_id = canonical_hash({"pipeline_version": VERSION, "input_sha256": inputs,
                                  "code_sha256": code,
                                  "selection_rule": universe["selection_audit"]["selection_rule"]})
    if (manifest.get("snapshot_pipeline_version") != VERSION
            or manifest.get("experiment_id") != expected_id
            or manifest.get("input_sha256") != inputs
            or manifest.get("code_sha256") != code
            or manifest.get("sample_role") != "sixth_disjoint_company_evaluation"
            or manifest.get("frozen_model_protocol") != {
                "provider_base_url": BASE_URL, "model_id": MODEL,
                "prompt_version": PROMPT_VERSION,
                "prompt_sha256": inputs[str(PROMPT.resolve())],
                "temperature": 0.0}
            or manifest.get("selection_audit") != universe["selection_audit"]
            or len(items) != len(universe["selected_pairs"])
            or [item["qa_id"] for item in items.values()]
            != [pair["qa_id"] for pair in universe["selected_pairs"]]
            or [item["stock_code"] for item in items.values()]
            != universe["stock_codes"]):
        raise ValueError("sixth snapshot identity, content or source differs")
    reconstructed = select_fixed_items(
        _source_rows(database, _json(config_path)["qa_source_sha256"],
                     universe["selected_pairs"]), universe["selected_pairs"],
        _json(config_path)["snapshot_as_of"])
    if list(items.values()) != reconstructed:
        raise ValueError("sixth snapshot item text differs from frozen source rows")
    return items, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.build:
        manifest = build_snapshot()
        print(json.dumps({"experiment_id": manifest["experiment_id"],
                          "items": manifest["counts"]["items"]}, ensure_ascii=False))
    else:
        items, manifest = verify_snapshot()
        print(json.dumps({"experiment_id": manifest["experiment_id"],
                          "items": len(items)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
