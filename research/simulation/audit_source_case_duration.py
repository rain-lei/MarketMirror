"""Independently re-open all new and reused duration paths, clocks and accounts."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path

from ..semantic.source_case_facts import ROOT, canonical
from .run_source_case_decisions import load_study

CONFIG = ROOT / "research/configs/source_case_duration_2026_v1.json"
ARCHIVED_AUDITOR = ROOT / "research_outputs/audit_source_case_decisions_20261004_v1.py"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit(output):
    plan = json.loads(CONFIG.read_text(encoding="utf-8"))
    for name, expected in plan["bindings"].items():
        require(sha(ROOT / name) == expected, "frozen duration binding changed: " + name)
    parent, cases, records, mapping, mechanism = load_study()
    spec = importlib.util.spec_from_file_location("frozen_independent_source_case_auditor", ARCHIVED_AUDITOR)
    auditor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auditor)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["protocol_sha256"] == sha(CONFIG) and manifest["bindings"] == plan["bindings"], "duration protocol differs")
    require(json.loads((output / "frozen_plan.json").read_text(encoding="utf-8")) == plan, "duration frozen plan differs")
    names = {f"case{i}_seed{s}_duration{d}.json.gz" for i in range(6) for s in plan["seeds"] for d in (3, 9)}
    require(set(manifest["artifacts"]) == names | {"summary.json", "frozen_plan.json"}, "new duration scope differs")
    require({p.name for p in output.glob("*.json.gz")} == names, "saved duration path coverage differs")
    for name, expected in manifest["artifacts"].items():
        require(sha(output / name) == expected, "saved duration artifact changed: " + name)
    paths, references, pairs, counts = {}, {}, [], Counter()
    for index, case in enumerate(cases):
        for seed in plan["seeds"]:
            runs = {}
            for mode in ("no_text", "reviewed_llm"):
                path = ROOT / plan["reference_directory"] / f"case{index}_seed{seed}_{mode}.json.gz"
                require(sha(path) == plan["bindings"][path.relative_to(ROOT).as_posix()], "reused source path changed")
                with gzip.open(path, "rt", encoding="utf-8") as stream:
                    raw = json.load(stream)
                require(raw["seed"] == seed and raw["case_id"] == case["case_id"] and raw["mode"] == mode,
                        "reused duration case identity differs")
                auditor.verify_run(raw, case, records[case["case_id"]], mapping, mechanism, parent)
                paths[path.relative_to(ROOT).as_posix()] = raw
                references[path.relative_to(ROOT).as_posix()] = sha(path)
                counts.update(audited_paths=1, audited_portfolio_days=18, audited_asset_calls=54,
                              audited_decisions=216, audited_final_accounts=48)
                if mode == "no_text":
                    control = raw
                else:
                    runs[6] = raw
            for duration in (3, 9):
                name = f"case{index}_seed{seed}_duration{duration}.json.gz"
                with gzip.open(output / name, "rt", encoding="utf-8") as stream:
                    raw = json.load(stream)
                require(raw["seed"] == seed and raw["case_id"] == case["case_id"] and raw["mode"] == "reviewed_llm"
                        and raw["information_duration_steps"] == duration and raw["information_duration_is_assumed"] is True,
                        "new duration case identity or assumption differs")
                variant_mapping = {**mapping, "information_duration_steps": duration}
                auditor.verify_run(raw, case, records[case["case_id"]], variant_mapping, mechanism, parent)
                require(sum(x["information_active"] for x in raw["source_links"]) == duration, "exact active duration differs")
                counts.update(audited_paths=1, audited_portfolio_days=18, audited_asset_calls=54,
                              audited_decisions=216, audited_final_accounts=48)
                runs[duration] = raw
            first = min(r["step"] for r in runs[6]["source_links"] if r["information_active"])
            for duration in (3, 9):
                until = first + min(duration, 6)
                require(runs[duration]["model_result"]["trace"][:until] == runs[6]["model_result"]["trace"][:until],
                        "duration changed the path before its information difference")
            for duration in (3, 6, 9):
                row = auditor.pair_direct(control, runs[duration])
                row.update(comparison_kind="duration_vs_no_text", treatment_duration_steps=duration,
                           no_text_reference_ignores_all_duration_settings=True)
                pairs.append(row)
            for duration in (3, 9):
                row = auditor.pair_direct(runs[6], runs[duration])
                row.update(comparison_kind="duration_vs_six", reference_duration_steps=6,
                           treatment_duration_steps=duration)
                pairs.append(row)
            print(json.dumps({"audited_case": index, "seed": seed, "audited_paths": counts["audited_paths"]}), flush=True)
    require(manifest["references"] == references, "duration reused references differ")
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    require(summary["counts"] == manifest["counts"] == plan["expected_counts"], "producer duration counts differ")
    require(summary["pairs"] == pairs and len(pairs) == 150, "duration signed pairs or full scope differ")
    require(summary["online_model_calls"] == 0 and summary["historical_market_effect_identified"] is False
            and summary["independent_accuracy_claimed"] is False and summary["independent_event_count"] == 6,
            "duration interpretation changed")
    require(dict(counts) == plan["expected_audit_counts"], "full raw-ledger audit scope differs")
    receipt = {"status": "PASS_ALL_SIX_CASES_THREE_DURATIONS_SOURCE_CLOCKS_FULL_SETTLEMENT_AND_150_PAIRS",
        "protocol_sha256": sha(CONFIG), "frozen_bindings_verified": len(plan["bindings"]),
        "counts": dict(counts), "new_paths": 60, "reused_six_step_paths": 30, "no_text_references": 30,
        "pairs": 150, "auditor_sha256": sha(Path(__file__)), "archived_path_auditor_sha256": sha(ARCHIVED_AUDITOR),
        "historical_market_effect_identified": False, "online_model_calls": 0, "goal_complete": False}
    with (output / "independent_verification.json").open("xb") as stream:
        stream.write(canonical(receipt) + b"\n")
    print(json.dumps(receipt), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    audit(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
