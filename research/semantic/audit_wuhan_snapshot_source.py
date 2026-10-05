"""Independently reconcile the frozen Wuhan Agent text pack with the Q&A database.

The saved audit contains aggregate counts and hashes, never question or reply text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, time
from pathlib import Path

from ..data_pipeline.aggregate_qa_features import KEYWORDS
from ..data_pipeline.provenance import file_sha256
from .annotation_pack import VERSION as PACK_VERSION, canonical_hash
from .signal_validation import load_pack


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/wuhan_pre_event_pit_snapshot_2020.json"
PACK = ROOT / "research_outputs/wuhan_pre_event_pit_snapshot_2020_v3"
OUTPUT = ROOT / "research_outputs/wuhan_snapshot_source_audit_v1.json"
ITEM_FIELDS = {"item_id", "qa_id", "stock_code", "split", "stage", "available_at",
               "question_available_at", "reply_available_at", "selection_stratum",
               "segments", "source_text_sha256"}


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Q&A availability time has no timezone")
    return parsed


def _stratum(reply: str) -> str:
    return next((name for name, words in KEYWORDS.items()
                 if any(word in reply for word in words)), "none")


def check_selected_items(items: dict[str, dict], selected: dict[str, tuple],
                         cutoff: datetime) -> None:
    if len(items) != len(selected) or {item["stock_code"] for item in items.values()} != set(selected):
        raise ValueError("frozen pack does not cover exactly the selected visible replies")
    for item in items.values():
        if set(item) != ITEM_FIELDS:
            raise ValueError("Agent text item contains unexpected fields")
        code = item["stock_code"]
        qa_id, question_at, question, reply_at, reply = selected[code]
        segments = [{"source": "question", "text": question},
                    {"source": "reply", "text": reply}]
        item_id = hashlib.sha256(f"{qa_id}|reply|{PACK_VERSION}".encode()).hexdigest()
        if (item["qa_id"] != qa_id or item["item_id"] != item_id
                or item["split"] != "pre_event_snapshot" or item["stage"] != "reply"
                or item["available_at"] != reply_at
                or item["question_available_at"] != question_at
                or item["reply_available_at"] != reply_at
                or item["segments"] != segments
                or item["selection_stratum"] != _stratum(reply)
                or item["source_text_sha256"] != canonical_hash(segments)
                or _time(reply_at) > cutoff or _time(question_at) > cutoff):
            raise ValueError("Agent text item differs from its as-of source row")


def audit(config_path: Path = CONFIG, pack_dir: Path = PACK) -> dict:
    config_path, pack_dir = config_path.resolve(), pack_dir.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    items, pack = load_pack(pack_dir)
    universe_path = (config_path.parent / config["universe_config"]).resolve()
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    database = (config_path.parent / config["qa_database"]).resolve()
    dataset_manifest_path = database.parent / "run_manifest.json"
    dataset_manifest = json.loads(dataset_manifest_path.read_text(encoding="utf-8"))
    source = [row for row in dataset_manifest["sources"]
              if row["sha256"] == config["qa_source_sha256"]]
    if len(source) != 1:
        raise ValueError("configured Q&A source is not unique")
    source_path = Path(source[0]["path"])
    if not source_path.is_absolute():
        source_path = (dataset_manifest_path.parent / source_path).resolve()
    source_path = source_path.resolve()
    if (pack.get("config") != config or pack.get("dataset_id") != dataset_manifest["dataset_id"]
            or pack.get("source") != source[0] or pack.get("universe_size") != len(universe["stock_codes"])
            or pack.get("universe_selection") != universe.get("selection")
            or pack.get("snapshot_as_of") != config["snapshot_as_of"]
            or pack.get("counts", {}).get("question_items") != 0
            or pack.get("counts", {}).get("reply_items") != len(items)
            or pack.get("counts", {}).get("companies") != len(items)):
        raise ValueError("snapshot configuration, source or manifest differs")
    expected_inputs = {config_path, database, dataset_manifest_path, source_path,
                       universe_path, (config_path.parent / config["event_config"]).resolve()}
    declared = {Path(name).resolve(): digest for name, digest in pack["input_sha256"].items()}
    if (not expected_inputs <= set(declared)
            or any(file_sha256(path) != digest for path, digest in declared.items())
            or dataset_manifest.get("artifacts", {}).get(database.name, {}).get("sha256") != declared[database]
            or declared[source_path] != config["qa_source_sha256"]):
        raise ValueError("snapshot source or provenance hash differs")
    code_files = {"event_snapshot.py", "annotation_pack.py", "signal_validation.py"}
    if (set(pack.get("code_sha256", {})) != code_files
            or any(file_sha256(Path(__file__).with_name(name)) != digest
                   for name, digest in pack["code_sha256"].items())):
        raise ValueError("snapshot builder code differs from archived version")
    codes = universe["stock_codes"]
    if (not isinstance(codes, list) or not codes or len(codes) != len(set(codes))
            or universe["selection"]["sample_size"] != len(codes)
            or universe["selection"]["qa_source_sha256"] != config["qa_source_sha256"]):
        raise ValueError("snapshot company cohort is invalid")
    lower = datetime.combine(datetime.fromisoformat(config["question_window_start"]).date(),
                             time(), tzinfo=_time(config["snapshot_as_of"]).tzinfo)
    cutoff = _time(config["snapshot_as_of"])
    if lower > cutoff:
        raise ValueError("snapshot question window is reversed")
    start, end = lower.isoformat(timespec="microseconds"), cutoff.isoformat(timespec="microseconds")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        metadata = {key: json.loads(value) for key, value in
                    connection.execute("SELECT key,value_json FROM dataset_metadata")}
        if any(metadata.get(key) != dataset_manifest.get(key)
               for key in ("dataset_id", "pipeline_version", "code_sha256", "feature_policy_sha256")):
            raise ValueError("Q&A database metadata differs from its manifest")
        frame = {row[0] for row in connection.execute(
            "SELECT DISTINCT stock_code FROM qa_record WHERE source_file_hash=? "
            "AND question_eligible=1 AND stock_code IS NOT NULL "
            "AND question_available_at>=? AND question_available_at<=?",
            (config["qa_source_sha256"], start, end))}
        seed = universe["selection"]["seed"]
        ranked = sorted(frame, key=lambda code: (hashlib.sha256(
            f"{seed}|company|{code}".encode()).hexdigest(), code))[:len(codes)]
        if codes != ranked or pack.get("universe_frame_companies") != len(frame):
            raise ValueError("frozen companies differ from the source-derived pre-event frame")
        placeholders = ",".join("?" for _ in codes)
        query = ("SELECT qa_id,stock_code,question_available_at,question_text,"
                 "reply_available_at,reply_text,question_eligible,reply_eligible "
                 f"FROM qa_record WHERE source_file_hash=? AND stock_code IN ({placeholders}) "
                 "AND question_available_at>=? AND question_available_at<=?")
        selected: dict[str, tuple] = {}
        questions = replies = 0
        for qa_id, code, question_at, question, reply_at, reply, q_ok, r_ok in connection.execute(
                query, (config["qa_source_sha256"], *codes, start, end)):
            if q_ok != 1 or not isinstance(question, str) or not lower <= _time(question_at) <= cutoff:
                continue
            questions += 1
            if (r_ok != 1 or not isinstance(reply, str) or not isinstance(reply_at, str)
                    or not _time(question_at) <= _time(reply_at) <= cutoff):
                continue
            replies += 1
            row = (qa_id, question_at, question, reply_at, reply)
            previous = selected.get(code)
            if previous is None or (_time(reply_at), qa_id) > (_time(previous[3]), previous[0]):
                selected[code] = row
    check_selected_items(items, selected, cutoff)
    counts = pack["counts"]
    if (counts["eligible_question_rows"] != questions
            or counts["eligible_confirmed_reply_rows"] != replies
            or counts["selected_companies"] != len(selected)
            or counts["companies_without_reply_snapshot"] != len(codes) - len(selected)):
        raise ValueError("snapshot counts differ from independent source query")
    return {"pipeline_version": "wuhan-snapshot-source-audit-v1",
            "snapshot_experiment_id": pack["experiment_id"],
            "source_sha256": {"database": declared[database], "original_workbook": declared[source_path],
                              "pack_manifest": file_sha256(pack_dir / "annotation_manifest.json"),
                              "pack_items": file_sha256(pack_dir / "annotation_items.jsonl"),
                              "audit_code": file_sha256(Path(__file__))},
            "cutoff": config["snapshot_as_of"], "frame_companies": len(frame),
            "selected_companies": len(codes), "eligible_questions": questions,
            "eligible_visible_replies": replies, "agent_reply_items": len(selected),
            "companies_without_visible_reply": len(codes) - len(selected),
            "financial_or_source_quarter_fields_in_agent_items": False,
            "interpretation": "All archived Agent reply items exactly match the latest eligible source Q&A rows visible by the cutoff. This does not verify first public appearance of the source timestamps or model quality."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = audit()
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("archived snapshot source audit differs from recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("snapshot source audit output already exists")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "frame_companies", "selected_companies", "eligible_questions",
        "eligible_visible_replies", "agent_reply_items", "companies_without_visible_reply")},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
