"""Compile and archive a source-first single-assistant review of the fifth cohort."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .assistant_review import validate_review
from .prepare_wuhan_v5_holdout import PROMPT, ROOT
from .signal_validation import read_jsonl
from .wuhan_protocol_v5 import MODULE as PROTOCOL_MODULE, PACK_DIR, verify_pack


VERSION = "wuhan-v5-source-first-single-assistant-review-v1"
MODULE = Path(__file__).resolve()
POLICY = ROOT / "research/configs/wuhan_v5_review_policy.json"
WORK = ROOT / "research_outputs/wuhan_v5_source_first_review_work/decisions_work.jsonl"
REVIEW_DIR = ROOT / "research_outputs/wuhan_pre_event_fresh_v5_holdout_review_v1"
DECISIONS_NAME = "reference_labels.jsonl"
WORK_NAME = "decisions_work.jsonl"
MANIFEST_NAME = "source_first_review_manifest.json"
REVIEWER = "ai:codex-v5-source-first"


def _policy() -> dict:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    if (policy.get("protocol") != "wuhan-fifth-company-source-first-review-v5"
            or policy.get("sample_role") != "fifth_disjoint_company_evaluation"
            or policy.get("reviewer_kind") != "single_ai_assistant"
            or policy.get("review_before_model_outputs") is not True
            or policy.get("model_outputs_previously_seen") is not False
            or policy.get("human_gold_ready") is not False
            or policy.get("frozen_prompt_sha256") != file_sha256(PROMPT)):
        raise ValueError("v5 source-first review policy differs from frozen prompt")
    return policy


def compile_work(pack_dir: Path = PACK_DIR, work_path: Path = WORK) -> tuple[list[dict], dict]:
    items, pack = verify_pack(pack_dir)
    work_path = work_path.resolve()
    if not work_path.is_file():
        raise FileNotFoundError("v5 source-first decisions work file is missing")
    raw = work_path.read_text(encoding="utf-8-sig")
    decisions = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(decisions) != len(items) or len(items) != 106:
        raise ValueError("v5 review requires exactly one decision per reply")
    ordered = list(items.values())
    labels: list[dict] = []
    for index, (item, decision) in enumerate(zip(ordered, decisions), start=1):
        if (not isinstance(decision, dict) or set(decision) != {"index", "events", "notes"}
                or type(decision["index"]) is not int or decision["index"] != index
                or not isinstance(decision["events"], list)
                or not isinstance(decision["notes"], str)
                or len(decision["notes"].strip()) < 12):
            raise ValueError(f"v5 review work row {index} is incomplete or out of order")
        reply = next(segment["text"] for segment in item["segments"]
                     if segment["source"] == "reply")
        events = []
        for event in decision["events"]:
            if not isinstance(event, dict) or set(event) != {
                    "type", "quote", "direction", "intensity", "uncertainty"}:
                raise ValueError(f"v5 review event fields differ at row {index}")
            quote = event["quote"]
            if not isinstance(quote, str) or not quote or reply.count(quote) != 1:
                raise ValueError(f"v5 review quote must occur exactly once in reply {index}")
            if any(type(event[name]) not in (int, float)
                   or not math.isfinite(event[name]) or not 0 <= event[name] <= 1
                   for name in ("intensity", "uncertainty")):
                raise ValueError(f"v5 review event numeric judgment invalid at row {index}")
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
    if (audit["reviewed_items"] != len(items) or audit["needs_review_items"]
            or audit["unlabeled_items"]):
        raise ValueError("v5 source-first review is not complete")
    return labels, {"pack_experiment_id": pack["experiment_id"], "audit": audit,
                    "event_type_counts": dict(Counter(event["event_type"]
                                                      for row in labels for event in row["events"]))}


def assert_no_model_run(experiment_id: str) -> None:
    for path in (ROOT / "research_outputs").rglob("model_run_manifest.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("pack_experiment_id") == experiment_id:
            raise ValueError("v5 reference must be finalized before any fifth-cohort model output")


def _code_hashes() -> dict[str, str]:
    names = ("wuhan_v5_review.py", "assistant_review.py",
             "wuhan_protocol_v5.py", "prepare_wuhan_v5_holdout.py")
    return {name: file_sha256(MODULE.with_name(name)) for name in names}


def finalize(pack_dir: Path = PACK_DIR, work_path: Path = WORK,
             output_dir: Path = REVIEW_DIR) -> dict:
    pack_dir, work_path, output_dir = (path.resolve() for path in
                                       (pack_dir, work_path, output_dir))
    policy = _policy()
    labels, summary = compile_work(pack_dir, work_path)
    assert_no_model_run(summary["pack_experiment_id"])
    outputs = (ROOT / "research_outputs").resolve()
    if (output_dir == outputs or outputs not in output_dir.parents
            or any(output_dir == path or output_dir in path.parents or path in output_dir.parents
                   for path in (pack_dir, work_path))
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("v5 review archive must be a new separate local output")
    inputs = {str(path.resolve()): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        work_path, POLICY, PROMPT)}
    result = {
        "pipeline_version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pack_experiment_id": summary["pack_experiment_id"],
        "sample_role": policy["sample_role"],
        "reviewer_kind": policy["reviewer_kind"],
        "review_before_model_outputs": True,
        "model_outputs_previously_seen": False,
        "independent_company_sample": True,
        "external_preregistration": False,
        "human_gold_ready": False,
        "score_interpretation": policy["score_interpretation"],
        "audit": summary["audit"],
        "event_type_counts": summary["event_type_counts"],
    }
    if any(file_sha256(Path(name)) != digest for name, digest in inputs.items()):
        raise RuntimeError("v5 review source or policy changed during finalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        with (stage / DECISIONS_NAME).open("w", encoding="utf-8", newline="\n") as handle:
            for row in labels:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        shutil.copyfile(work_path, stage / WORK_NAME)
        (stage / "source_first_review_report.md").write_text(
            "# 武汉第五批公司样本：v5 模型前逐条复核\n\n"
            f"已逐条复核 {summary['audit']['reviewed_items']}/{len(labels)} 条回复；"
            f"{summary['audit']['event_items']} 条含事件，共 {summary['audit']['events']} 个事件。\n\n"
            "每条决定只依据冻结的事前提问与公司回复，事件引文均来自回复且精确定位。"
            "此参考由单一 AI 助手完成，不是人工金标准；第五批样本未作外部预注册。\n",
            encoding="utf-8")
        manifest = {**result, "inputs": inputs, "code_sha256": _code_hashes(),
                    "artifacts": {path.name: {"sha256": file_sha256(path)}
                                  for path in stage.iterdir()}}
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def load_review(pack_dir: Path = PACK_DIR, review_dir: Path = REVIEW_DIR) -> tuple[list[dict], dict]:
    pack_dir, review_dir = pack_dir.resolve(), review_dir.resolve()
    _policy()
    items, pack = verify_pack(pack_dir)
    manifest = json.loads((review_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION
            or manifest.get("pack_experiment_id") != pack["experiment_id"]
            or manifest.get("review_before_model_outputs") is not True
            or manifest.get("model_outputs_previously_seen") is not False
            or manifest.get("human_gold_ready") is not False
            or manifest.get("code_sha256") != _code_hashes()):
        raise ValueError("v5 archived review identity or chronology differs")
    expected_inputs = {str(path.resolve()): file_sha256(path) for path in (
        pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
        WORK, POLICY, PROMPT)}
    if manifest.get("inputs") != expected_inputs:
        raise ValueError("v5 archived review inputs differ")
    expected_artifacts = {DECISIONS_NAME, WORK_NAME, "source_first_review_report.md"}
    if set(manifest.get("artifacts", {})) != expected_artifacts:
        raise ValueError("v5 archived review artifact list differs")
    for name in expected_artifacts:
        if file_sha256(review_dir / name) != manifest["artifacts"][name]["sha256"]:
            raise ValueError("v5 archived review artifact hash differs")
    labels = read_jsonl(review_dir / DECISIONS_NAME)
    reconstructed, summary = compile_work(pack_dir, review_dir / WORK_NAME)
    if (labels != reconstructed or len(labels) != len(items)
            or manifest.get("audit") != summary["audit"]
            or manifest.get("event_type_counts") != summary["event_type_counts"]):
        raise ValueError("v5 archived labels differ from work decisions or source")
    return labels, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--finalize", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        _policy()
        _, summary = compile_work()
        assert_no_model_run(summary["pack_experiment_id"])
        result = summary
    elif args.finalize:
        result = finalize()
    else:
        _, result = load_review()
    print(json.dumps({"pack_experiment_id": result["pack_experiment_id"],
                      "reviewed_items": result["audit"]["reviewed_items"],
                      "event_items": result["audit"]["event_items"],
                      "events": result["audit"]["events"],
                      "event_type_counts": result["event_type_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
