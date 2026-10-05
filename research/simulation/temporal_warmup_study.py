"""Full fixed-grid carried warmup study against the complete original cold Q1."""
from __future__ import annotations

from collections import Counter

from .temporal_information_study import (
    ROOT, CONFIG as BASE_CONFIG, OUTPUT as BASE_OUTPUT, CELLS, encoded, read, require, read_gzip,
    write_gzip, load_study as load_base, seed_paths, path_key, controls_for, shocks_for,
    summarize as summarize_cells, bindings_ok as base_bindings_ok,
)
from .public_factor_metrics import values, difference
from ..data_pipeline.provenance import file_sha256

CONFIG = ROOT / "research/configs/temporal_carried_warmup_study_2021_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_carried_warmup_study_2021_v1"


def bindings_ok(cfg):
    for name, digest in cfg["bindings"].items():
        require(file_sha256(ROOT / name) == digest, "frozen carried warmup input or code changed: " + name)


def load_study():
    cfg = read(CONFIG)
    require(cfg["version"] == "temporal-carried-state-date-aligned-warmup-complete-study-2021-v1"
        and cfg["variants"] == CELLS and cfg["warmup_sessions"] == 42 and len(cfg["dates"]) == 100
        and cfg["semantic_gate_enabled"] is False and cfg["no_new_parameter_default"] is True,
        "carried warmup protocol differs")
    bindings_ok(cfg)
    base, qr, qf, qj, mask = load_base()
    base_bindings_ok(base)
    compiled = read_gzip(ROOT / cfg["compiled_inputs_path"])
    risk, factors, joined = compiled["risk"], compiled["features"], compiled["joined"]
    require(cfg["evaluation_dates"] == base["dates"] == cfg["dates"][42:]
        and cfg["stocks"] == base["stocks"] and cfg["seeds"] == base["seeds"]
        and cfg["baskets"] == base["baskets"] and cfg["model_design"] == base["model_design"]
        and cfg["agents"] == base["agents"] and cfg["issuer_parameters"] == base["issuer_parameters"]
        and cfg["background_response"] == base["background_response"] and cfg["joint_limits"] == base["joint_limits"],
        "warmup changed original evaluation, source, stochastic or numerical settings")
    for combined, quarter in ((risk, qr), (factors, qf), (joined, qj)):
        require(set(combined) == set(quarter) == set(cfg["stocks"]), "warmup company scope differs")
        for stock in cfg["stocks"]:
            require([r["trade_date"] for r in combined[stock]] == cfg["dates"]
                and combined[stock][42:] == quarter[stock], "Q1 source values or actual dates changed")
    return cfg, risk, factors, joined, mask


def seal(folder, names, identity):
    record = {"identity": identity, "protocol_sha256": file_sha256(CONFIG),
        "artifacts": {n: file_sha256(folder / n) for n in names}}
    with (folder / "checkpoint.json").open("xb") as f:
        f.write(encoded(record))


def checkpoint(folder, identity):
    record = read(folder / "checkpoint.json")
    require(record["identity"] == identity and record["protocol_sha256"] == file_sha256(CONFIG), "warmup checkpoint identity differs")
    require({p.name for p in folder.iterdir()} == set(record["artifacts"]) | {"checkpoint.json"}, "warmup checkpoint scope differs")
    for name, digest in record["artifacts"].items():
        require(file_sha256(folder / name) == digest, "warmup checkpoint artifact changed")
    return record


def gaps(variant):
    v = variant["joint_checks"]["values"]
    return {"mean": v["absolute_mean_gap"], "volatility": None if v["volatility_ratio"] is None else abs(v["volatility_ratio"] - 1),
        "zero_fraction": v["zero_fraction_gap"], "stock_correlation": v["stock_correlation_gap"],
        "portfolio_volatility": None if v["portfolio_volatility_ratio"] is None else abs(v["portfolio_volatility_ratio"] - 1)}


def summarize(variants, baseline, seeds):
    keys = {(s["seed_id"], c["name"]) for s in seeds for c in CELLS}
    warm, cold = ({(v["seed_id"], v["name"]): v for v in rows} for rows in (variants, baseline))
    require(len(warm) == len(variants) == len(cold) == len(baseline) == 80 and set(warm) == set(cold) == keys,
        "all eighty carried paths and original cold conditions are required")
    paired = []
    for seed in seeds:
        for cell in CELLS:
            key = seed["seed_id"], cell["name"]
            change = difference(gaps(warm[key]), gaps(cold[key]))
            paired.append({"seed_id": key[0], "name": key[1], "pair": "carried_42_session_warmup_vs_original_cold_q1",
                "metric_changes": difference(values(warm[key]), values(cold[key])), "gap_changes": change,
                "all_five_gaps_nonworse": all(v is not None and v <= 1e-12 for v in change.values())})
    return {"paired_warmup_effects": paired, "within_warmup_summary": summarize_cells(variants, seeds),
        "joint_pass_conditions": sum(v["joint_checks"]["all_five_pass"] for v in variants),
        "baseline_joint_pass_conditions": sum(v["joint_checks"]["all_five_pass"] for v in baseline),
        "joint_pass_by_delivery": dict(Counter(v["delivery"] for v in variants if v["joint_checks"]["all_five_pass"]))}
