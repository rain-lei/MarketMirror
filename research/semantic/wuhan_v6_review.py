"""Finalize a source-first single-assistant review of the sixth cohort."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .assistant_review import validate_review
from .prepare_wuhan_v6_holdout import POLICY, PROMPT, ROOT
from .signal_validation import EVENT_TYPES, DIRECTIONS, read_jsonl
from .wuhan_v6_gate import load_policy
from .wuhan_v6_snapshot import OUTPUT_DIR as SOURCE_DIR, verify_snapshot


VERSION = "wuhan-v6-sixth-source-first-single-assistant-review-v1"
MODULE = Path(__file__).resolve()
WORK = ROOT / "research_outputs/wuhan_v6_source_first_review_work/decisions_work.jsonl"
REVIEW_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v6_holdout_review_v1"
DECISIONS_NAME = "reference_labels.jsonl"
WORK_NAME = "decisions_work.jsonl"
MANIFEST_NAME = "source_first_review_manifest.json"
REVIEWER = "ai:codex-v6-source-first"


def compile_decisions(items: dict[str, dict], decisions: list[dict]) -> tuple[list[dict], dict]:
    """Require one substantive decision and unique reply quote per frozen item."""
    if len(decisions) != len(items):
        raise ValueError("sixth review needs one decision per visible reply")
    labels: list[dict] = []
    for index, (item, decision) in enumerate(zip(items.values(), decisions), start=1):
        if (not isinstance(decision, dict) or set(decision) != {"index", "events", "notes"}
                or type(decision["index"]) is not int or decision["index"] != index
                or not isinstance(decision["events"], list)
                or not isinstance(decision["notes"], str)
                or len(decision["notes"].strip()) < 12):
            raise ValueError(f"sixth review row {index} is incomplete or out of order")
        reply = next(segment["text"] for segment in item["segments"]
                     if segment["source"] == "reply")
        events = []
        for event in decision["events"]:
            if (not isinstance(event, dict) or set(event) != {
                    "type", "quote", "direction", "intensity", "uncertainty"}
                    or event["type"] not in EVENT_TYPES
                    or event["direction"] not in DIRECTIONS):
                raise ValueError(f"sixth review event fields differ at row {index}")
            quote = event["quote"]
            if not isinstance(quote, str) or not quote or reply.count(quote) != 1:
                raise ValueError(f"sixth review quote must occur once in reply {index}")
            if any(type(event[name]) not in (int, float)
                   or not math.isfinite(event[name]) or not 0 <= event[name] <= 1
                   for name in ("intensity", "uncertainty")):
                raise ValueError(f"sixth review numeric judgment invalid at row {index}")
            start = reply.index(quote)
            events.append({
                "event_type": event["type"], "direction": event["direction"],
                "affected_industries": [], "horizon": "unknown",
                "intensity": event["intensity"], "uncertainty": event["uncertainty"],
                "evidence_spans": [{"source": "reply", "start": start,
                                    "end": start + len(quote), "quote": quote}],
            })
        labels.append({"item_id": item["item_id"],
                       "source_text_sha256": item["source_text_sha256"],
                       "annotator_id": REVIEWER, "status": "labeled",
                       "events": events, "notes": decision["notes"]})
    audit = validate_review(items, labels)
    if audit["reviewed_items"] != len(items) or audit["needs_review_items"]:
        raise ValueError("sixth review is incomplete")
    return labels, audit


def compile_work(source_dir: Path = SOURCE_DIR, work_path: Path = WORK) -> tuple[list[dict], dict]:
    policy = load_policy(POLICY)
    items, source = verify_snapshot(output_dir=source_dir)
    decisions = read_jsonl(work_path)
    if len(items) != policy["sample_size"]:
        raise ValueError("sixth review source count differs from pre-sample policy")
    labels, audit = compile_decisions(items, decisions)
    return labels, {"source_experiment_id": source["experiment_id"],
                    "audit": audit,
                    "event_type_counts": dict(Counter(
                        event["event_type"] for row in labels for event in row["events"]))}


def check_work(source_dir: Path = SOURCE_DIR, work_path: Path = WORK) -> dict:
    """Validate a review prefix without presenting it as a sealed full reference."""
    policy = load_policy(POLICY)
    items, source = verify_snapshot(output_dir=source_dir)
    decisions = read_jsonl(work_path)
    if (len(items) != policy["sample_size"] or not decisions
            or len(decisions) > len(items)):
        raise ValueError("sixth review work must be a nonempty prefix of the fixed sample")
    prefix = dict(list(items.items())[:len(decisions)])
    _, audit = compile_decisions(prefix, decisions)
    assert_no_model_run(source["experiment_id"])
    return {"pack_experiment_id": source["experiment_id"],
            "audit": audit, "remaining_items": len(items) - len(decisions),
            "work_sha256": file_sha256(work_path)}


def assert_no_model_run(experiment_id: str) -> None:
    for path in (ROOT / "research_outputs").rglob("model_run_manifest.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        if (run.get("pack_experiment_id") == experiment_id
                or run.get("source_experiment_id") == experiment_id):
            raise ValueError("sixth reference must precede every sixth-cohort model output")


def _code_hashes() -> dict[str, str]:
    return {name: file_sha256(MODULE.with_name(name)) for name in (
        "wuhan_v6_review.py", "wuhan_v6_snapshot.py", "wuhan_v6_gate.py",
        "prepare_wuhan_v6_holdout.py", "assistant_review.py")}


def _output_path(output_dir: Path, source_dir: Path, work_path: Path) -> Path:
    output_dir = output_dir.resolve()
    outputs = (ROOT / "research_outputs").resolve()
    if (output_dir == outputs or outputs not in output_dir.parents
            or any(output_dir == item or output_dir in item.parents or item in output_dir.parents
                   for item in (source_dir.resolve(), work_path.resolve()))
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("sixth review archive must be a new separate output directory")
    return output_dir


def finalize(source_dir: Path = SOURCE_DIR, work_path: Path = WORK,
             output_dir: Path = REVIEW_DIR) -> dict:
    source_dir, work_path = source_dir.resolve(), work_path.resolve()
    output_dir = _output_path(output_dir, source_dir, work_path)
    policy = load_policy(POLICY)
    labels, summary = compile_work(source_dir, work_path)
    assert_no_model_run(summary["source_experiment_id"])
    inputs = {str(path.resolve()): file_sha256(path) for path in (
        source_dir / "annotation_manifest.json", source_dir / "annotation_items.jsonl",
        work_path, POLICY, PROMPT)}
    code = _code_hashes()
    result = {
        "pipeline_version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pack_experiment_id": summary["source_experiment_id"],
        "sample_role": policy["sample_role"],
        "reviewer_kind": policy["reviewer_kind"],
        "review_before_model_outputs": True,
        "model_outputs_previously_seen": False,
        "human_gold_ready": False,
        "external_preregistration": False,
        "score_interpretation": policy["score_interpretation"],
        "audit": summary["audit"],
        "event_type_counts": summary["event_type_counts"],
    }
    if any(file_sha256(Path(name)) != digest for name, digest in inputs.items()):
        raise RuntimeError("sixth review source or policy changed before finalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        with (stage / DECISIONS_NAME).open("w", encoding="utf-8", newline="\n") as handle:
            for row in labels:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copyfile(work_path, stage / WORK_NAME)
        (stage / "source_first_review_report.md").write_text(
            "# 武汉第六批公司：模型前逐条复核\n\n"
            f"已逐条复核 {summary['audit']['reviewed_items']}/{len(labels)} 条回复；"
            f"{summary['audit']['event_items']} 条含事件，共 {summary['audit']['events']} 个事件。\n\n"
            "只依据冻结的事前提问与公司回复；事件引文须在回复中唯一精确定位。"
            "参考由单一 AI 助手完成，不是人工金标准或外部预注册。\n",
            encoding="utf-8")
        manifest = {**result, "inputs": inputs, "code_sha256": code,
                    "artifacts": {path.name: {"sha256": file_sha256(path)}
                                  for path in stage.iterdir()}}
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if (_code_hashes() != code
                or any(file_sha256(Path(name)) != digest for name, digest in inputs.items())):
            raise RuntimeError("sixth review inputs or code changed during output staging")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    load_review(source_dir, output_dir, work_path)
    return result


def load_review(source_dir: Path = SOURCE_DIR,
                review_dir: Path = REVIEW_DIR,
                work_path: Path = WORK) -> tuple[list[dict], dict]:
    source_dir, review_dir, work_path = (path.resolve() for path in
                                         (source_dir, review_dir, work_path))
    policy = load_policy(POLICY)
    items, source = verify_snapshot(output_dir=source_dir)
    manifest = json.loads((review_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION
            or manifest.get("pack_experiment_id") != source["experiment_id"]
            or manifest.get("sample_role") != policy["sample_role"]
            or manifest.get("review_before_model_outputs") is not True
            or manifest.get("model_outputs_previously_seen") is not False
            or manifest.get("human_gold_ready") is not False
            or manifest.get("code_sha256") != _code_hashes()):
        raise ValueError("sixth archived review identity or chronology differs")
    expected_inputs = {str(path.resolve()): file_sha256(path) for path in (
        source_dir / "annotation_manifest.json", source_dir / "annotation_items.jsonl",
        work_path, POLICY, PROMPT)}
    if manifest.get("inputs") != expected_inputs:
        raise ValueError("sixth archived review inputs differ")
    expected_artifacts = {DECISIONS_NAME, WORK_NAME, "source_first_review_report.md"}
    if set(manifest.get("artifacts", {})) != expected_artifacts:
        raise ValueError("sixth archived review artifact list differs")
    for name in expected_artifacts:
        if file_sha256(review_dir / name) != manifest["artifacts"][name]["sha256"]:
            raise ValueError("sixth archived review artifact hash differs")
    labels = read_jsonl(review_dir / DECISIONS_NAME)
    reconstructed, summary = compile_work(source_dir, review_dir / WORK_NAME)
    if (labels != reconstructed or len(labels) != len(items)
            or manifest.get("audit") != summary["audit"]
            or manifest.get("event_type_counts") != summary["event_type_counts"]):
        raise ValueError("sixth archived labels differ from work decisions or source")
    return labels, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--check-work", action="store_true")
    group.add_argument("--finalize", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.check_work:
        result = check_work()
    elif args.preflight:
        _, summary = compile_work()
        assert_no_model_run(summary["source_experiment_id"])
        result = {"pack_experiment_id": summary["source_experiment_id"], **summary}
    elif args.finalize:
        result = finalize()
    else:
        _, result = load_review()
    print(json.dumps({"pack_experiment_id": result["pack_experiment_id"],
                      "reviewed_items": result["audit"]["reviewed_items"],
                      "event_items": result["audit"]["event_items"],
                      "events": result["audit"]["events"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
