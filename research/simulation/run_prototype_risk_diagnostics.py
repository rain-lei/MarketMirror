"""Report all registered prototype path risks without adding simulations."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from ..semantic.source_case_facts import ROOT, canonical
from .prototype_risk_metrics import derive_path_metrics, ROLES

CONFIG = ROOT / "research/configs/prototype_risk_diagnostics_2026_v1.json"
VERSION = "full-240-prototype-path-risk-diagnostics-v1"
ROLE_METRICS = ("total_return", "step_return_sample_stdev", "max_close_drawdown",
                "filled_given_accepted", "filled_given_requested", "cash_clipped_quantity_fraction")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")


def load_plan():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (cfg["schema_version"] != VERSION or cfg["new_market_paths"] != 0 or cfg["online_model_calls"] != 0
            or len(cfg["paths"]) != 240 or cfg["role_metrics"] != list(ROLE_METRICS)):
        raise ValueError("all archived prototype path scope required")
    for name, expected in cfg["bindings"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("frozen risk input changed: " + name)
    if len({r["path"] for r in cfg["paths"]}) != 240:
        raise ValueError("duplicate prototype raw paths cannot add observations")
    return cfg


def range_summary(values):
    present = [x for x in values if x is not None]
    return {"available": len(present), "missing": len(values) - len(present),
            "minimum": min(present) if present else None,
            "median": statistics.median(present) if present else None,
            "maximum": max(present) if present else None}


def summarize_rows(rows, plan):
    role_groups, market_groups, by_key = defaultdict(list), defaultdict(list), {}
    for row in rows:
        key = (row["study"], row["case_id"], row["mode"], row["duration_steps"])
        market_groups[key].append(row)
        by_key[(*key, row["seed"])] = row
        for role in ROLES:
            role_groups[(*key, role)].append(row)
    aggregates = []
    for key, values in sorted(role_groups.items()):
        if sorted(v["seed"] for v in values) != plan["seeds"]:
            raise ValueError("all five seeds required for every risk aggregate")
        role = key[-1]
        aggregates.append({"study": key[0], "case_id": key[1], "mode": key[2], "duration_steps": key[3], "role": role,
            "seeds": plan["seeds"], "metrics": {name: range_summary([r["roles"][role][name] for r in values]) for name in ROLE_METRICS},
            "maxima": {name: range_summary([r["roles"][role]["maxima"][name] for r in values])
                       for name in ("single_asset_wealth_weight", "total_stock_wealth_weight", "covariance_portfolio_risk")},
            "counts": {k: sum(r["roles"][role]["counts"][k] for r in values) for k in values[0]["roles"][role]["counts"]}})
    markets = []
    for key, values in sorted(market_groups.items()):
        if sorted(v["seed"] for v in values) != plan["seeds"]:
            raise ValueError("all five seeds required for every market aggregate")
        markets.append({"study": key[0], "case_id": key[1], "mode": key[2], "duration_steps": key[3],
            "metrics": {name: range_summary([r["market"][name] for r in values])
                        for name in ("step_equal_weight_return_sample_stdev", "flat_asset_step_fraction", "fee_pool_minor")},
            "counts": {k: sum(r["market"]["counts"][k] for r in values) for k in values[0]["market"]["counts"]}})
    pairs = []
    for comparison in plan["comparisons"]:
        left = by_key[tuple(comparison["reference_key"])]
        right = by_key[tuple(comparison["treatment_key"])]
        roles = {}
        for role in ROLES:
            r, t = left["roles"][role], right["roles"][role]
            roles[role] = {"metric_differences": {k: t[k] - r[k] if r[k] is not None and t[k] is not None else None for k in ROLE_METRICS},
                "risk_breach_step_difference": t["counts"]["post_close_risk_breach_steps"] - r["counts"]["post_close_risk_breach_steps"],
                "concentration_breach_step_difference": t["counts"]["post_close_concentration_breach_steps"] - r["counts"]["post_close_concentration_breach_steps"]}
        pairs.append({**comparison, "roles": roles})
    return aggregates, markets, pairs


def run_study(output):
    protocol = sha(CONFIG)
    plan = load_plan()
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "frozen_plan.json", plan)
    rows, counts = [], Counter()
    with (output / "path_metrics.jsonl").open("xb") as stream:
        for index, entry in enumerate(plan["paths"]):
            with gzip.open(ROOT / entry["path"], "rt", encoding="utf-8") as raw_stream:
                raw = json.load(raw_stream)
            if raw["case_id"] != entry["case_id"] or raw["seed"] != entry["seed"]:
                raise ValueError("registered risk case identity differs")
            metrics = derive_path_metrics(raw)
            metrics.update(**{k: entry[k] for k in ("study", "mode", "duration_steps")},
                           source_path=entry["path"], source_sha256=sha(ROOT / entry["path"]))
            stream.write(canonical(metrics) + b"\n")
            rows.append(metrics)
            counts.update(metrics["scope"])
            if (index + 1) % 30 == 0:
                print(json.dumps({"derived_paths": index + 1}), flush=True)
    with (output / "path_metrics.jsonl").open(encoding="utf-8") as stream:
        if [json.loads(line) for line in stream] != rows:
            raise ValueError("risk metrics changed on disk")
    aggregates, markets, pairs = summarize_rows(rows, plan)
    counts.update(role_groups=len(aggregates), market_groups=len(markets), pairs=len(pairs))
    if dict(counts) != plan["expected_counts"] or sha(CONFIG) != protocol:
        raise ValueError("complete risk diagnostic scope changed")
    load_plan()
    summary = {"status": "COMPLETE_ALL_240_ARCHIVED_RISK_PATHS_PENDING_INDEPENDENT_AUDIT",
        "version": VERSION, "protocol_sha256": protocol, "counts": dict(counts),
        "role_aggregates": aggregates, "market_aggregates": markets, "pairs": pairs,
        "new_market_paths": 0, "online_model_calls": 0, "historical_prediction_proven": False,
        "uncertainty_ranges_are_seed_ranges_not_confidence_intervals": True, "goal_complete": False}
    write_new(output / "summary.json", summary)
    artifacts = {name: sha(output / name) for name in ("frozen_plan.json", "path_metrics.jsonl", "summary.json")}
    write_new(output / "manifest.json", {"version": VERSION, "protocol_sha256": protocol,
                                        "artifacts": artifacts, "bindings": plan["bindings"], "counts": dict(counts)})
    print(json.dumps({"status": summary["status"], "counts": dict(counts)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    run_study(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
