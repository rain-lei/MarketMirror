"""Full 720-condition descriptive resource contrast on the already observed 2022 window."""
import statistics

from .temporal_transport_study import (
    ROOT, CONFIG as PARENT_CONFIG, OUTPUT as PARENT_OUTPUT, CELLS as PARENT_CELLS,
    encoded, read, require, read_gzip, write_gzip, controls_for, seed_paths, path_key,
    shocks_for, audit_scope, load_study as load_parent)
from .temporal_residual_coupling_study import seal, checkpoint
from .resource_unit_initialization import BASELINE, FIXED, NOTIONAL
from .public_factor_metrics import values, difference, FIELDS
from ..data_pipeline.provenance import file_sha256

CONFIG = ROOT / "research/configs/temporal_resource_unit_study_2022_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_transport_resource_units_2022_v1"
CELLS = [{**cell, "name": arm + "__" + cell["name"],
          "resource_arm": arm, "parent_cell_index": ci, "parent_condition_name": cell["name"]}
         for arm in (FIXED, NOTIONAL) for ci, cell in enumerate(PARENT_CELLS)]


def bindings_ok(cfg):
    for name, digest in cfg["bindings"].items():
        path = ROOT / name
        require(path.resolve().is_relative_to(ROOT.resolve()) and file_sha256(path) == digest,
                "Frozen resource study input/code changed: " + name)


def validate_protocol(cfg, parent):
    require(cfg["version"] == "temporal-resource-unit-descriptive-study-2022-v1"
        and cfg["variants"] == CELLS and cfg["resource_arms"] == [BASELINE, FIXED, NOTIONAL]
        and cfg["full_scope_unique_conditions"] == 720 and cfg["newly_simulated_conditions"] == 480
        and cfg["reused_independently_audited_conditions"] == 240
        and cfg["same_2022_window_previously_observed"] is True
        and cfg["independent_new_period_validation"] is False
        and cfg["empirical_agent_calibration_complete"] is False
        and cfg["point_in_time_feed_certified"] is False
        and cfg["historical_exchange_rules_certified"] is False
        and cfg["candidate_selected_as_default"] is False
        and cfg["semantic_gate_enabled"] is False
        and cfg["prices_reset_using_later_daily_source_quotes"] is False,
        "Resource study scope or interpretation gates changed")
    for key in ("model_design", "agents", "issuer_parameters", "background_response", "dense_audit_sessions",
                "stocks", "baskets", "dates", "seeds", "joint_limits", "evaluation_mask_path",
                "numeric_inputs_path", "residual_rank_windows_path", "source_trade_dates"):
        require(encoded(cfg[key]) == encoded(parent[key]), "Resource study changed an undeclared parent value: " + key)
    require(set(cfg["initial_raw_prices_minor"]) == set(parent["stocks"])
        and set(cfg["source_secid_by_stock"]) == set(parent["stocks"])
        and cfg["initial_reference_date"] < parent["dates"][0]
        and all(type(p) is int and p > 0 and p % parent["model_design"]["venue"]["tick_minor"] == 0
                for p in cfg["initial_raw_prices_minor"].values()), "Invalid complete initial source price contract")
    require(cfg["background_order_caps_and_target_offsets_held_in_shares"] is True,
            "Background quantity caps or offsets changed without registration")


def load_study(protocol_path=CONFIG):
    cfg = read(protocol_path)
    parent, risk, features, joined, mask = load_parent(PARENT_CONFIG)
    validate_protocol(cfg, parent)
    bindings_ok(cfg)
    require(file_sha256(PARENT_CONFIG) == cfg["parent_protocol_sha256"], "Original completed parent protocol changed")
    require(cfg["per_seed_audit_scope"] == resource_audit_scope(parent, joined), "Full resource audit scope differs")
    require(cfg["per_condition_producer_scope"] == parent["per_condition_producer_scope"], "Resource producer scope reduced")
    return cfg, risk, features, joined, mask


def resource_audit_scope(parent, joined):
    scope = audit_scope(parent, joined, len(CELLS))
    scope["independently_verified_initial_source_prices"] = len(parent["stocks"])
    scope["independently_reconstructed_initial_resource_accounts"] = scope["complete_wealth_drawdown_risk_accounts"]
    return scope


def summarize_resource_pairs(rows, seeds):
    identity = lambda r: (r["seed_id"], r["resource_arm"], r["parent_cell_index"])
    index = {identity(r): r for r in rows}
    grid = {(s["seed_id"], arm, ci) for s in seeds for arm in (BASELINE, FIXED, NOTIONAL)
            for ci in range(len(PARENT_CELLS))}
    require(len(rows) == len(index) == len(grid) == 720 and set(index) == grid,
            "Missing, duplicate or extra full resource conditions")
    for row in rows:
        parent = PARENT_CELLS[row["parent_cell_index"]]
        require(row["parent_condition_name"] == parent["name"]
            and all(row[k] == v for k,v in parent.items() if k != "name"), "Resource condition parent identity differs")
    pairs = []
    for seed in seeds:
        for ci in range(len(PARENT_CELLS)):
            for treatment, reference in ((FIXED, BASELINE), (NOTIONAL, BASELINE), (NOTIONAL, FIXED)):
                a, b = index[seed["seed_id"], treatment, ci], index[seed["seed_id"], reference, ci]
                pairs.append({"seed_id": seed["seed_id"], "parent_cell_index": ci,
                    "treatment_resource_arm": treatment, "reference_resource_arm": reference,
                    "metric_changes": difference(values(a), values(b)),
                    "joint_pass_treatment": a["joint_checks"]["all_five_pass"],
                    "joint_pass_reference": b["joint_checks"]["all_five_pass"]})
    groups = []
    for arm in (BASELINE, FIXED, NOTIONAL):
        for dependence in ("independent", "historical_rank"):
            for delivery in ("masked", "public", "whole_public"):
                for market in ("market_independent", "market_shared"):
                    selected = [r for r in rows if (r["resource_arm"], r["residual_dependence"],
                        r["delivery"], r["risk_mode"]) == (arm, dependence, delivery, market)]
                    require(len(selected) == 20, "A full resource stratum was reduced")
                    groups.append({"resource_arm": arm, "residual_dependence": dependence, "delivery": delivery,
                        "risk_mode": market, "conditions": len(selected),
                        "joint_pass_conditions": sum(r["joint_checks"]["all_five_pass"] for r in selected),
                        "metric_medians": {k: statistics.median(xs) if all(x is not None for x in xs) else None
                                           for k in FIELDS for xs in [[values(r)[k] for r in selected]]}})
    return {"resource_pairs": pairs, "strata": groups,
        "joint_pass_conditions_by_arm": {arm: sum(r["joint_checks"]["all_five_pass"] for r in rows if r["resource_arm"] == arm)
                                         for arm in (BASELINE, FIXED, NOTIONAL)}}
