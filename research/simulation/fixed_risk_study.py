"""Shared frozen scope and statistics for the complete received-risk experiment."""
from __future__ import annotations
from collections import Counter
import gzip
import json
from pathlib import Path
import statistics

from ..data_pipeline.provenance import file_sha256
from .public_factor_numeric_inputs_v3 import load_inputs, verify_numeric
from .run_pre_wuhan_public_factor_channels_2019 import parameters_for, seeded_design, build_features, record_bytes
from .audit_public_market_factor import load_raw_groups, independent_panel
from .run_pre_wuhan_background_response_2019 import MARKET
from .issuer_valuation import RISK_FIELDS
from .fixed_marginal_risk import build_path
from .audit_fixed_marginal_risk import expected_budget_path
from .verify_pre_wuhan_own_price_feedback_2019_v2 import independent_issuer
from .public_factor_metrics import values, difference

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_fixed_marginal_risk_2019_v1.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_fixed_marginal_risk_2019_v1"
REBUILD = ROOT / "research_outputs/pre_wuhan_fixed_marginal_risk_rebuild_2019_v1"
CELLS = [{"name": a + "_feedback_" + label + "_risk_" + mode, "background_anchor": a,
    "feedback_scale": scale, "risk_mode": mode} for a in ("initial_inventory", "current_inventory")
    for label, scale in (("full", 1.0), ("both_off", 0.0))
    for mode in ("legacy", "market_independent", "market_shared")]

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def require(value, message):
    if not value:
        raise ValueError(message)

def load_study():
    cfg = read(CONFIG)
    require(cfg["version"] == "fixed-marginal-received-risk-complete-study-v1" and cfg["variants"] == CELLS
        and cfg["semantic_gate_enabled"] is False, "frozen full risk study scope differs")
    for name, expected in {**cfg["code_bindings"], **cfg["reference_bindings"]}.items():
        require(file_sha256(ROOT / name) == expected, "frozen risk input changed: " + name)
    preflight = read(ROOT / cfg["preflight_completion"])
    require(preflight["status"] == "COMPLETE_FIXED_RISK_INPUT_PREFLIGHT_AND_BYTE_REBUILD"
        and len(preflight["jobs"]) == 4 and all(type(j["observed_process_exit_code"]) is int
            and j["observed_process_exit_code"] == 0 and j["terminal_found"] is True for j in preflight["jobs"]), "preflight actual exits incomplete")
    for job in preflight["jobs"]:
        require(file_sha256(ROOT / job["log_path"]) == job["log_sha256"], "preflight log changed")
    loaded = load_inputs()
    require(cfg["seeds"] == loaded[0]["seeds"] and cfg["joint_limits"] == loaded[0]["joint_limits"], "full study seeds or limits changed")
    return cfg, loaded

def seed_paths(loaded, seed, independent=False):
    cfg, base, _, market, _, _, groups, joined, _, _, _, issuer, _, _, _ = loaded
    core, background, legacy = seeded_design(base, issuer, seed)
    raw = independent_issuer(issuer, seed)
    require(raw == legacy, "legacy seed reconstruction differs")
    params = parameters_for(cfg, seed)
    panel = independent_panel(load_raw_groups(MARKET.parent / "market_daily.csv", market), joined, params)
    if independent:
        result = {"legacy": raw, **{m: expected_budget_path(raw, panel, m) for m in ("market_independent", "market_shared")}}
    else:
        features = build_features(groups, joined, params)
        risk = {s: [{k: r[k] for k in RISK_FIELDS} for r in rows] for s, rows in legacy["by_stock"].items()}
        result = {"legacy": legacy, **{m: build_path(risk, features, legacy["scenario_id"], legacy["parameters"], m, params["innovation_seed"])
            for m in ("market_independent", "market_shared")}}
        for m in ("market_independent", "market_shared"):
            require(result[m] == expected_budget_path(raw, panel, m), "full seed raw budget inputs differ")
    return core, background, result

def write_gzip_json(path, value):
    with Path(path).open("wb") as raw, gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as stream:
        stream.write(record_bytes(value))

def read_gzip_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)

def seal(folder, names, identity):
    record = {"identity": identity, "protocol_sha256": file_sha256(CONFIG),
        "artifacts": {n: file_sha256(folder / n) for n in names}}
    (folder / "checkpoint.json").write_bytes(record_bytes(record))
    return record

def checkpoint(folder, identity):
    record = read(folder / "checkpoint.json")
    require(record["identity"] == identity and record["protocol_sha256"] == file_sha256(CONFIG), "risk checkpoint identity differs")
    require({p.name for p in folder.iterdir()} == set(record["artifacts"]) | {"checkpoint.json"}, "risk checkpoint artifact scope differs")
    for name, expected in record["artifacts"].items():
        require(Path(name).name == name and file_sha256(folder / name) == expected, "risk checkpoint artifact changed")
    return record

def legacy_partition(seed_index, cell_index):
    old_index = cell_index // 3 * 4
    return ROOT / f"research_outputs/pre_wuhan_public_factor_checkpoints_v3/prepared/legacy_{seed_index}_{old_index}.jsonl.gz"

def legacy_name(cell):
    return cell["background_anchor"] + "_feedback_" + ("full" if cell["feedback_scale"] else "both_off")

def summary(variants, seeds):
    lookup = {(r["seed_id"], r["name"]): r for r in variants}
    require(len(lookup) == len(variants) == 60 and set(lookup) == {(s["seed_id"], c["name"]) for s in seeds for c in CELLS}, "full risk summary grid incomplete")
    groups, paired = {}, []
    for c in CELLS:
        rows = [lookup[s["seed_id"], c["name"]] for s in seeds]
        keys = tuple(values(rows[0]))
        groups[c["name"]] = {"seed_count": 5, "joint_pass_seeds": sum(r["joint_checks"]["all_five_pass"] for r in rows),
            "metric_medians": {k: statistics.median(v) if v else None for k in keys
                for v in [[values(r)[k] for r in rows if values(r)[k] is not None]]}}
    def gaps(row):
        g = row["joint_checks"]["values"]
        return {"mean": g["absolute_mean_gap"], "volatility": abs(g["volatility_ratio"] - 1),
            "zero_fraction": g["zero_fraction_gap"], "stock_correlation": g["stock_correlation_gap"],
            "portfolio_volatility": abs(g["portfolio_volatility_ratio"] - 1)}
    for seed in seeds:
        for anchor in ("initial_inventory", "current_inventory"):
            for label, scale in (("full", 1.0), ("both_off", 0.0)):
                index = {m: lookup[seed["seed_id"], anchor + "_feedback_" + label + "_risk_" + m]
                    for m in ("legacy", "market_independent", "market_shared")}
                for treatment, reference in (("market_shared", "market_independent"), ("market_independent", "legacy"), ("market_shared", "legacy")):
                    change = difference(gaps(index[treatment]), gaps(index[reference]))
                    paired.append({"seed_id": seed["seed_id"], "background_anchor": anchor, "feedback_scale": scale,
                        "treatment": treatment, "reference": reference, "metric_changes": difference(values(index[treatment]), values(index[reference])),
                        "gap_changes": change, "all_five_nonworse": all(v is not None and v <= 1e-12 for v in change.values())})
    return {"seed_summary": groups, "paired_effects": paired,
        "joint_pass_conditions": sum(r["joint_checks"]["all_five_pass"] for r in variants)}
