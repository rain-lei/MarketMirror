"""Finite 3/6/9-step information-duration sensitivity using frozen role rules."""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from ..semantic.source_case_facts import ROOT, canonical
from .run_source_case_decisions import load_study, run_case, pair

VERSION = "six-source-case-information-duration-sensitivity-v1"
CONFIG = ROOT / "research/configs/source_case_duration_2026_v1.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def duration_mapping(mapping, duration):
    if type(duration) is not int or duration not in (3, 6, 9):
        raise ValueError("registered three, six or nine decision steps required")
    result = copy.deepcopy(mapping)
    result["information_duration_steps"] = duration
    return result


def run_duration(case, record, mapping, mechanism, seed, duration):
    run = run_case(case, record, duration_mapping(mapping, duration), mechanism, seed, "reviewed_llm")
    run.update(information_duration_steps=duration, information_duration_is_assumed=True)
    return run


def write_new(path, data):
    with path.open("xb") as stream:
        stream.write(canonical(data) + b"\n")


def load_plan():
    plan = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (plan["schema_version"] != VERSION or plan["durations"] != [3, 6, 9]
            or plan["new_durations"] != [3, 9] or plan["online_model_calls"] != 0
            or plan["independent_accuracy_claimed"] is not False):
        raise ValueError("complete finite development duration study required")
    for name, expected in plan["bindings"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("frozen duration input changed: " + name)
    parent, cases, records, mapping, mechanism = load_study()
    if plan["case_ids"] != parent["case_ids"] or plan["seeds"] != parent["seeds"]:
        raise ValueError("case or seed scope changed")
    return plan, cases, records, mapping, mechanism


def run_study(output):
    protocol = sha(CONFIG)
    plan, cases, records, mapping, mechanism = load_plan()
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "frozen_plan.json", plan)
    artifacts, references, pairs = {}, {}, []
    counts = Counter()
    for index, case in enumerate(cases):
        for seed in plan["seeds"]:
            base_name = f"case{index}_seed{seed}_reviewed_llm.json.gz"
            control_name = f"case{index}_seed{seed}_no_text.json.gz"
            base_path = ROOT / plan["reference_directory"] / base_name
            control_path = ROOT / plan["reference_directory"] / control_name
            with gzip.open(base_path, "rt", encoding="utf-8") as stream:
                baseline = json.load(stream)
            with gzip.open(control_path, "rt", encoding="utf-8") as stream:
                control = json.load(stream)
            references[base_path.relative_to(ROOT).as_posix()] = sha(base_path)
            references[control_path.relative_to(ROOT).as_posix()] = sha(control_path)
            baseline.update(information_duration_steps=6, information_duration_is_assumed=True)
            runs = {6: baseline}
            for duration in plan["new_durations"]:
                run = run_duration(case, records[case["case_id"]], mapping, mechanism, seed, duration)
                name = f"case{index}_seed{seed}_duration{duration}.json.gz"
                with (output / name).open("xb") as stream:
                    with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0, filename="") as packed:
                        packed.write(canonical(run) + b"\n")
                with gzip.open(output / name, "rt", encoding="utf-8") as stream:
                    if json.load(stream) != run:
                        raise ValueError("duration path changed on disk")
                artifacts[name] = sha(output / name)
                runs[duration] = run
                counts.update(new_paths=1, new_portfolio_days=18, new_asset_calls=54,
                              new_decisions=216, new_asset_explanations=648, new_source_links=18)
            for duration in plan["durations"]:
                row = pair(control, runs[duration])
                row.update(comparison_kind="duration_vs_no_text", treatment_duration_steps=duration,
                           no_text_reference_ignores_all_duration_settings=True)
                pairs.append(row)
            for duration in plan["new_durations"]:
                row = pair(baseline, runs[duration])
                row.update(comparison_kind="duration_vs_six", reference_duration_steps=6,
                           treatment_duration_steps=duration)
                pairs.append(row)
            counts.update(reused_six_step_paths=1, distinct_no_text_reference_paths=1)
            print(json.dumps({"case_index": index, "seed": seed, "new_paths": counts["new_paths"]}), flush=True)
    counts.update(pairs=len(pairs))
    load_plan()
    if sha(CONFIG) != protocol or dict(counts) != plan["expected_counts"]:
        raise ValueError("complete finite duration scope changed")
    summary = {"status": "COMPLETE_ALL_DURATION_PATHS_PENDING_INDEPENDENT_AUDIT", "version": VERSION,
        "protocol_sha256": protocol, "counts": dict(counts), "pairs": pairs,
        "independent_event_count": 6, "online_model_calls": 0,
        "historical_market_effect_identified": False, "independent_accuracy_claimed": False,
        "duration_is_assumed_decision_steps_not_calendar_days": True, "goal_complete": False}
    write_new(output / "summary.json", summary)
    for name in ("frozen_plan.json", "summary.json"):
        artifacts[name] = sha(output / name)
    write_new(output / "manifest.json", {"protocol_sha256": protocol, "version": VERSION,
        "artifacts": artifacts, "references": references, "bindings": plan["bindings"], "counts": dict(counts)})
    print(json.dumps({"status": summary["status"], "counts": dict(counts)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    run_study(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
