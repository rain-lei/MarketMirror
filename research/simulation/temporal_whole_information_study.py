"""Eighty unique whole-coverage conditions against the archived original 160."""
import statistics

from .temporal_information_study import (ROOT, CONFIG as BASE_CONFIG, OUTPUT as BASE_OUTPUT,
    encoded, read, require, read_gzip, write_gzip, controls_for, shocks_for, load_study as load_base)
from .temporal_residual_coupling_study import (CONFIG as RESIDUAL_CONFIG, OUTPUT as RESIDUAL_OUTPUT,
    seal as previous_seal, checkpoint as previous_checkpoint)
from .temporal_whole_information_risk import VERSION, build_path
from .temporal_residual_coupling import source_windows
from .public_factor_metrics import values
from ..data_pipeline.provenance import file_sha256

CONFIG = ROOT / "research/configs/temporal_whole_information_2021_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_whole_information_2021_v1"
CELLS = [{"name": f"{anchor}_feedback_{label}_residual_{dependence}_risk_{mode}_delivery_whole_public",
    "background_anchor": anchor, "feedback_scale": scale, "risk_mode": mode, "delivery": "whole_public",
    "residual_dependence": dependence}
    for anchor in ("initial_inventory", "current_inventory")
    for label, scale in (("full", 1.0), ("both_off", 0.0))
    for dependence in ("independent", "historical_rank")
    for mode in ("market_independent", "market_shared")]


def path_key(cell):
    return cell["residual_dependence"] + "_" + cell["risk_mode"] + "_" + cell["delivery"]


def bindings_ok(cfg):
    for name, expected in cfg["bindings"].items():
        require(file_sha256(ROOT / name) == expected, "frozen whole information binding changed: " + name)


def load_study():
    cfg = read(CONFIG)
    require(cfg["version"] == "temporal-whole-information-complete-study-2021-v1"
        and cfg["whole_information_version"] == VERSION and cfg["variants"] == CELLS
        and cfg["semantic_gate_enabled"] is False and cfg["no_new_parameter_default"] is True,
        "whole information scope differs")
    bindings_ok(cfg)
    base, risk, features, joined, mask = load_base()
    for key in ("stocks", "dates", "seeds", "baskets", "model_design", "agents", "issuer_parameters",
                "background_response", "joint_limits", "dense_audit_sessions"):
        require(cfg[key] == base[key], "whole coverage changed original controlled scope: " + key)
    require(source_windows(features) == read(ROOT / cfg["residual_rank_preparation_path"])["windows"],
        "whole coverage common past source differs")
    return cfg, risk, features, joined, mask


def seed_paths(cfg, risk, features, seed):
    core = {**cfg["model_design"]["core"], "seed": seed["arrival_seed"],
        "scenario_id": f"endogenous_cohort_seed{seed['arrival_seed']}"}
    background = {**cfg["model_design"]["background"], "seed": seed["background_seed"]}
    parameters = {**cfg["issuer_parameters"], "innovation_seed": seed["innovation_seed"],
        "information_seed": seed["information_seed"]}
    common = "marketmirror-public-market-factor-v1:" + seed["innovation_seed"]
    coupling = cfg["residual_common_seed_namespace"] + seed["innovation_seed"]
    paths = {dependence + "_" + mode + "_whole_public": build_path(risk, features, "temporal_candidate_2021",
        parameters, mode, common, "whole_public", coupling if dependence == "historical_rank" else None)
        for dependence in ("independent", "historical_rank") for mode in ("market_independent", "market_shared")}
    return core, background, paths


def seal(folder, names, identity, protocol_path=CONFIG):
    return previous_seal(folder, names, identity, protocol_path)


def checkpoint(folder, identity, protocol_path=CONFIG):
    return previous_checkpoint(folder, identity, protocol_path)


def summarize(variants, seeds):
    indexed = {(r["seed_id"], r["name"]): r for r in variants}
    require(len(indexed) == len(variants) == 80 and set(indexed) ==
        {(s["seed_id"], c["name"]) for s in seeds for c in CELLS}, "all eighty unique new conditions required")
    groups = {}
    for cell in CELLS:
        rows = [indexed[s["seed_id"], cell["name"]] for s in seeds]
        groups[cell["name"]] = {"seed_count": len(rows),
            "joint_pass_seeds": sum(r["joint_checks"]["all_five_pass"] for r in rows),
            "metric_medians": {k: statistics.median(numbers) if all(x is not None for x in numbers) else None
                for k in values(rows[0]) for numbers in [[values(r)[k] for r in rows]]}}
    return {"seed_summary": groups, "joint_pass_conditions": sum(r["joint_checks"]["all_five_pass"] for r in variants),
        "scope": "80 unique new whole-message conditions; the archived 160 are compared separately"}
