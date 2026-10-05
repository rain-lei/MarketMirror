"""Whole-cohort liquidity by price-selection factorial; frozen source baseline reused."""
from __future__ import annotations

from collections import Counter
import statistics

from .temporal_information_study import (
    ROOT, CONFIG as BASE_CONFIG, OUTPUT as BASE_OUTPUT, CELLS, encoded, read, require, read_gzip,
    write_gzip, load_study as load_base, seed_paths, path_key, controls_for, shocks_for, bindings_ok as base_bindings_ok,
)
from .public_factor_metrics import values, difference
from ..data_pipeline.provenance import file_sha256

CONFIG = ROOT / "research/configs/temporal_liquidity_auction_2021_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_liquidity_auction_2021_v1"
ARMS = [
    {"arm": "fixed_capacity_nearest_tie", "liquidity_mode": "fixed", "price_tie_break": "nearest_prior", "reuses_original_full_baseline": True},
    {"arm": "lagged_capacity_nearest_tie", "liquidity_mode": "lagged_turnover", "price_tie_break": "nearest_prior", "reuses_original_full_baseline": False},
    {"arm": "fixed_capacity_pressure_tie", "liquidity_mode": "fixed", "price_tie_break": "accepted_order_pressure", "reuses_original_full_baseline": False},
    {"arm": "lagged_capacity_pressure_tie", "liquidity_mode": "lagged_turnover", "price_tie_break": "accepted_order_pressure", "reuses_original_full_baseline": False},
]
NEW_ARMS = ARMS[1:]


def bindings_ok(cfg):
    for name, expected in cfg["bindings"].items():
        require(file_sha256(ROOT / name) == expected, "frozen liquidity experiment changed: " + name)


def load_study():
    cfg, base = read(CONFIG), read(BASE_CONFIG)
    require(cfg["version"] == "temporal-lagged-liquidity-by-price-selection-full-factorial-2021-v1"
            and cfg["arms"] == ARMS and cfg["variants"] == CELLS
            and cfg["semantic_gate_enabled"] is False and cfg["no_new_parameter_default"] is True,
            "factorial study protocol differs")
    bindings_ok(cfg)
    base_bindings_ok(base)
    _, risk, factors, joined, mask = load_base()
    path = read_gzip(ROOT / cfg["liquidity_path_path"])
    return cfg, risk, factors, joined, mask, path


def seal(folder, names, identity):
    record = {"identity": identity, "protocol_sha256": file_sha256(CONFIG),
              "artifacts": {n: file_sha256(folder / n) for n in names}}
    with (folder / "checkpoint.json").open("xb") as f:
        f.write(encoded(record))


def checkpoint(folder, identity):
    r = read(folder / "checkpoint.json")
    require(r["identity"] == identity and r["protocol_sha256"] == file_sha256(CONFIG), "factorial checkpoint identity differs")
    require({p.name for p in folder.iterdir()} == set(r["artifacts"]) | {"checkpoint.json"}, "factorial checkpoint scope differs")
    for n, h in r["artifacts"].items():
        require(file_sha256(folder / n) == h, "factorial checkpoint changed")
    return r


def gaps(row):
    g = row["joint_checks"]["values"]
    return {"mean": g["absolute_mean_gap"], "volatility": abs(g["volatility_ratio"] - 1) if g["volatility_ratio"] is not None else None,
        "zero_fraction": g["zero_fraction_gap"], "stock_correlation": g["stock_correlation_gap"],
        "portfolio_volatility": abs(g["portfolio_volatility_ratio"] - 1) if g["portfolio_volatility_ratio"] is not None else None}


def summarize(variants, seeds):
    lookup = {(r["seed_id"], r["name"], r["arm"]): r for r in variants}
    require(len(lookup) == len(variants) == 320 and set(lookup) == {(s["seed_id"], c["name"], a["arm"])
        for s in seeds for c in CELLS for a in ARMS}, "all three hundred twenty factorial conditions required")
    paired, interactions, groups = [], [], {}
    for seed in seeds:
        for cell in CELLS:
            key = seed["seed_id"], cell["name"]
            rows = [lookup[*key, a["arm"]] for a in ARMS]
            for t, b, label in ((1, 0, "liquidity_at_nearest"), (3, 2, "liquidity_at_pressure"),
                                (2, 0, "price_selection_at_fixed_capacity"), (3, 1, "price_selection_at_lagged_capacity")):
                delta = difference(gaps(rows[t]), gaps(rows[b]))
                paired.append({"seed_id": seed["seed_id"], "name": cell["name"], "pair": label,
                    "treated_arm": rows[t]["arm"], "baseline_arm": rows[b]["arm"],
                    "metric_changes": difference(values(rows[t]), values(rows[b])), "gap_changes": delta,
                    "all_five_gaps_nonworse": all(v is not None and v <= 1e-12 for v in delta.values())})
            interactions.append({"seed_id": seed["seed_id"], "name": cell["name"],
                "metric_interaction": difference(difference(values(rows[3]), values(rows[2])), difference(values(rows[1]), values(rows[0]))),
                "gap_interaction": difference(difference(gaps(rows[3]), gaps(rows[2])), difference(gaps(rows[1]), gaps(rows[0])))})
    for arm in ARMS:
        rows = [r for r in variants if r["arm"] == arm["arm"]]
        groups[arm["arm"]] = {"conditions": len(rows), "joint_pass_conditions": sum(r["joint_checks"]["all_five_pass"] for r in rows),
            "criterion_pass_counts": dict(Counter(k for r in rows for k, v in r["joint_checks"]["criteria"].items() if v)),
            "metric_distributions": {k: {"minimum": min(v), "median": statistics.median(v), "maximum": max(v)} if v else None
                for k in values(rows[0]) for v in [[values(r)[k] for r in rows if values(r)[k] is not None]]}}
    return {"arm_summary": groups, "paired_effects": paired, "interactions": interactions,
        "joint_pass_conditions": sum(r["joint_checks"]["all_five_pass"] for r in variants),
        "full_factorial_conditions": len(variants), "newly_simulated_conditions": 240, "reused_independently_audited_conditions": 80}
