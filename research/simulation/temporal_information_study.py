"""Isolated full-grid temporal study; legacy numerical settings stay frozen."""
from __future__ import annotations
from collections import Counter
import gzip
import json
from pathlib import Path
import statistics

from .public_information_study import CELLS
from .public_factor_metrics import values, difference
from .temporal_risk_inputs import compile_positions
from .temporal_return_metrics import freeze_mask
from .temporal_public_information_risk import build_path
from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/temporal_public_information_risk_2021_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_public_information_risk_2021_v1"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(value, message):
    if not value:
        raise ValueError(message)


def read_gzip(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def write_gzip(path, value):
    with Path(path).open("xb") as target, gzip.GzipFile(fileobj=target, filename="", mode="wb", mtime=0) as stream:
        stream.write(encoded(value))


def bindings_ok(cfg):
    for name, expected in cfg["bindings"].items():
        require(file_sha256(ROOT / name) == expected, "frozen temporal input/code changed: " + name)


def load_study():
    cfg = read(CONFIG)
    require(cfg["version"] == "temporal-available-observation-public-information-complete-study-2021-v1"
        and cfg["variants"] == CELLS and cfg["semantic_gate_enabled"] is False
        and cfg["no_new_parameter_default"] is True, "temporal protocol scope differs")
    bindings_ok(cfg)
    with gzip.open(ROOT / cfg["positions_path"], "rt", encoding="utf-8") as stream:
        positions = list(map(json.loads, stream))
    risk, factors, joined = compile_positions(positions, cfg["stocks"], cfg["dates"], cfg["history_window"])
    mask = freeze_mask(joined, cfg["stocks"], cfg["dates"])
    require(read_gzip(ROOT / cfg["evaluation_mask_path"]) == mask, "evaluation mask differs from preregistered source targets")
    return cfg, risk, factors, joined, mask


def seed_paths(cfg, risk, factors, seed):
    core = {**cfg["model_design"]["core"], "seed": seed["arrival_seed"],
        "scenario_id": f"endogenous_cohort_seed{seed['arrival_seed']}"}
    background = {**cfg["model_design"]["background"], "seed": seed["background_seed"]}
    parameters = {**cfg["issuer_parameters"], "innovation_seed": seed["innovation_seed"], "information_seed": seed["information_seed"]}
    common_seed = "marketmirror-public-market-factor-v1:" + seed["innovation_seed"]
    paths = {mode + "_" + delivery: build_path(risk, factors, "temporal_candidate_2021", parameters, mode, common_seed, delivery)
        for mode in ("market_independent", "market_shared") for delivery in ("masked", "public")}
    return core, background, paths


def path_key(cell):
    return cell["risk_mode"] + "_" + cell["delivery"]


def controls_for(anchor):
    require(anchor in ("initial_inventory", "current_inventory"), "undeclared inventory anchor")
    return None if anchor == "initial_inventory" else {"background_anchor": "current_inventory", "strategy_wait": "original"}


def shocks_for(basket, dates):
    return {"scenario_id": "temporal_no_policy_or_industry_pulses_2021", "common": [0.0] * len(dates),
        "asset_specific": {s: [0.0] * len(dates) for s in basket}}


def seal(folder, names, identity):
    record = {"identity": identity, "protocol_sha256": file_sha256(CONFIG),
        "artifacts": {n: file_sha256(folder / n) for n in names}}
    with (folder / "checkpoint.json").open("xb") as stream:
        stream.write(encoded(record))


def checkpoint(folder, identity):
    record = read(folder / "checkpoint.json")
    require(record["identity"] == identity and record["protocol_sha256"] == file_sha256(CONFIG), "temporal checkpoint identity differs")
    require({p.name for p in folder.iterdir()} == set(record["artifacts"]) | {"checkpoint.json"}, "temporal checkpoint scope differs")
    for name, digest in record["artifacts"].items():
        require(Path(name).name == name and file_sha256(folder / name) == digest, "temporal checkpoint artifact changed")
    return record


def summarize(variants, seeds):
    lookup = {(r["seed_id"], r["name"]): r for r in variants}
    require(len(lookup) == len(variants) == 80 and set(lookup) == {(s["seed_id"], c["name"]) for s in seeds for c in CELLS},
        "all eighty temporal conditions required")
    groups, paired = {}, []
    for cell in CELLS:
        rows = [lookup[s["seed_id"], cell["name"]] for s in seeds]
        groups[cell["name"]] = {"seed_count": 5, "joint_pass_seeds": sum(r["joint_checks"]["all_five_pass"] for r in rows),
            "metric_medians": {k: statistics.median(v) if v else None for k in values(rows[0])
                for v in [[values(r)[k] for r in rows if values(r)[k] is not None]]}}
    def gaps(row):
        g = row["joint_checks"]["values"]
        return {"mean": g["absolute_mean_gap"], "volatility": abs(g["volatility_ratio"] - 1) if g["volatility_ratio"] is not None else None,
            "zero_fraction": g["zero_fraction_gap"], "stock_correlation": g["stock_correlation_gap"],
            "portfolio_volatility": abs(g["portfolio_volatility_ratio"] - 1) if g["portfolio_volatility_ratio"] is not None else None}
    for seed in seeds:
        for anchor in ("initial_inventory", "current_inventory"):
            for label, scale in (("full", 1.0), ("both_off", 0.0)):
                prefix = anchor + "_feedback_" + label + "_risk_"
                for treatment, reference, pair in (
                    ("market_independent_delivery_public", "market_independent_delivery_masked", "public_coverage_independent"),
                    ("market_shared_delivery_public", "market_shared_delivery_masked", "public_coverage_shared"),
                    ("market_shared_delivery_masked", "market_independent_delivery_masked", "coupling_masked"),
                    ("market_shared_delivery_public", "market_independent_delivery_public", "coupling_public")):
                    treated, baseline = lookup[seed["seed_id"], prefix + treatment], lookup[seed["seed_id"], prefix + reference]
                    change = difference(gaps(treated), gaps(baseline))
                    paired.append({"seed_id": seed["seed_id"], "background_anchor": anchor, "feedback_scale": scale, "pair": pair,
                        "metric_changes": difference(values(treated), values(baseline)), "gap_changes": change,
                        "all_five_nonworse": all(v is not None and v <= 1e-12 for v in change.values())})
    return {"seed_summary": groups, "paired_effects": paired,
        "joint_pass_conditions": sum(r["joint_checks"]["all_five_pass"] for r in variants),
        "joint_pass_by_delivery": dict(Counter(r["delivery"] for r in variants if r["joint_checks"]["all_five_pass"]))}
