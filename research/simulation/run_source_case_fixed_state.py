"""Replay registered source cases in fixed states; no online LLM requests."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from ..semantic.source_case_facts import ROOT, canonical
from .run_source_case_decisions import load_study
from .source_case_fixed_state import VERSION, compare_archived_state

CONFIG = ROOT / "research/configs/source_case_fixed_state_2026_v1.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, data):
    with path.open("xb") as stream:
        stream.write(canonical(data) + b"\n")


def load_plan():
    plan = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (plan["schema_version"] != VERSION or plan["strengths"] != [0.5, 1.0, 1.5]
            or plan["new_market_paths"] != 0 or plan["online_model_calls"] != 0
            or plan["case_count"] != 6 or plan["independent_accuracy_claimed"] is not False):
        raise ValueError("complete registered fixed-state development plan required")
    for name, expected in plan["bindings"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("frozen fixed-state input changed: " + name)
    parent, cases, records, mapping, mechanism = load_study()
    if plan["case_ids"] != parent["case_ids"] or plan["seeds"] != parent["seeds"]:
        raise ValueError("case or seed scope changed")
    return plan, cases, records, mapping, mechanism


def run_study(output):
    protocol = sha(CONFIG)
    plan, cases, records, mapping, mechanism = load_plan()
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "frozen_plan.json", plan)
    counts, artifacts, aggregates = Counter(), {}, {}
    for index, case in enumerate(cases):
        for seed in plan["seeds"]:
            source = ROOT / plan["reference_directory"] / f"case{index}_seed{seed}_no_text.json.gz"
            with gzip.open(source, "rt", encoding="utf-8") as stream:
                raw = json.load(stream)
            result = compare_archived_state(raw, case, records[case["case_id"]], mapping,
                                            mechanism["portfolio_case"], mechanism["venue"]["lot_size"])
            result.update(reference_path=source.relative_to(ROOT).as_posix(), reference_sha256=sha(source))
            name = f"case{index}_seed{seed}_fixed_state.json.gz"
            with (output / name).open("xb") as stream:
                with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0, filename="") as packed:
                    packed.write(canonical(result) + b"\n")
            with gzip.open(output / name, "rt", encoding="utf-8") as stream:
                if json.load(stream) != result:
                    raise ValueError("fixed-state archive changed on disk")
            artifacts[name] = sha(output / name)
            counts.update(reference_paths=1, checkpoints=len(result["checkpoints"]), groups=len(result["groups"]))
            for group in result["groups"]:
                counts.update(decisions=len(group["rows"]), asset_explanations=3 * len(group["rows"]),
                              mode_pairs=len(group["comparisons"]))
                for pair in group["comparisons"]:
                    key = "|".join((case["case_id"], group["context"], group["checkpoint"], group["role"],
                                    pair["reference"], pair["treatment"]))
                    aggregate = aggregates.setdefault(key, Counter())
                    aggregate.update(comparisons=1)
                    aggregate.update({k: int(pair[k]) for k in ("beliefs_changed", "targets_changed", "requests_changed", "gates_changed")})
            print(json.dumps({"case_index": index, "seed": seed, "decisions": counts["decisions"]}), flush=True)
    if dict(counts) != plan["expected_counts"] or sha(CONFIG) != protocol:
        raise ValueError("registered fixed-state scope changed or incomplete")
    load_plan()
    summary = {"version": VERSION, "status": "COMPLETE_ALL_FIXED_STATES_PENDING_INDEPENDENT_AUDIT",
               "protocol_sha256": protocol, "counts": dict(counts),
               "aggregates": {k: dict(v) for k, v in sorted(aggregates.items())},
               "independent_event_count": 6, "new_market_paths": 0, "online_model_calls": 0,
               "actual_fills_computed": False, "historical_market_effect_identified": False,
               "goal_complete": False}
    write_new(output / "summary.json", summary)
    for name in ("frozen_plan.json", "summary.json"):
        artifacts[name] = sha(output / name)
    write_new(output / "manifest.json", {"protocol_sha256": protocol, "artifacts": artifacts,
              "bindings": plan["bindings"], "counts": dict(counts), "version": VERSION})
    print(json.dumps({"status": summary["status"], "counts": dict(counts)}), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    run_study(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
