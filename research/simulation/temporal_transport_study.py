"""Full registered 2022 transport grid; the earlier numerical model is immutable.

This adapter changes calendar, source locations and measured QC counts only.
It never loads the 2021 producer's hard-coded missingness counts as 2022 facts.
"""
from collections import Counter
import statistics

from .temporal_information_study import ROOT, encoded, read, require, read_gzip, write_gzip, controls_for
from .temporal_whole_information_risk import build_path, VERSION
from .temporal_whole_information_study import path_key
from .temporal_residual_coupling_study import seal, checkpoint
from .temporal_residual_coupling import source_windows
from .temporal_return_metrics import freeze_mask
from .public_factor_metrics import values, difference, FIELDS
from ..data_pipeline.provenance import file_sha256

CONFIG = ROOT / "research/configs/temporal_transport_execution_2022_v1.json"
DESIGN = ROOT / "research/configs/temporal_transport_design_2022_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_transport_execution_2022_v1"
CELLS = [{"name": f"{anchor}_feedback_{label}_residual_{dependence}_risk_{mode}_delivery_{delivery}",
    "background_anchor": anchor, "feedback_scale": scale, "risk_mode": mode, "delivery": delivery,
    "residual_dependence": dependence}
    for anchor in ("initial_inventory", "current_inventory")
    for label, scale in (("full", 1.0), ("both_off", 0.0))
    for dependence in ("independent", "historical_rank")
    for mode in ("market_independent", "market_shared")
    for delivery in ("masked", "public", "whole_public")]


def bindings_ok(cfg):
    for name, digest in cfg["bindings"].items():
        path = ROOT / name
        require(path.resolve().is_relative_to(ROOT.resolve()) and file_sha256(path) == digest,
            "Frozen transport input/code changed: " + name)


def validate_protocol(cfg, design, original):
    require(cfg["version"] == "temporal-full-factorial-transport-execution-2022-v1"
        and cfg["whole_information_version"] == VERSION and cfg["variants"] == CELLS == design["variants"]
        and cfg["formal_execution_protocol_frozen"] is True and cfg["evaluation_mask_frozen"] is True
        and cfg["semantic_gate_enabled"] is False and cfg["no_new_parameter_default"] is True
        and cfg["no_outcome_driven_numerical_changes"] is True and cfg["full_scope_unique_conditions"] == 240,
        "Transport protocol changed registered scope or execution gates")
    for key in design["unchanged_numerical_keys"]:
        require(encoded(cfg[key]) == encoded(design[key]) == encoded(original[key]),
            "Transport changed a frozen numerical parameter: " + key)
    for key in ("stocks", "baskets", "dates", "source_trade_dates", "seeds", "joint_limits"):
        require(encoded(cfg[key]) == encoded(design[key]), "Transport changed registered scope: " + key)
    require(cfg["stocks"] == original["stocks"] and cfg["baskets"] == original["baskets"]
        and cfg["point_in_time_feed_certified"] is False and cfg["economic_total_return_certified"] is False
        and cfg["candidate_selected_as_default"] is False, "Transport source certification or cohort changed")
    require(len(cfg["seeds"]) == 5 and len(cfg["dates"]) == 58 and len(cfg["baskets"]) == 41,
        "Transport full scope was reduced")


def load_study(protocol_path=CONFIG):
    cfg, design = read(protocol_path), read(DESIGN)
    original = read(ROOT / design["original_numerical_protocol_path"])
    validate_protocol(cfg, design, original)
    bindings_ok(cfg)
    numeric = read_gzip(ROOT / cfg["numeric_inputs_path"])
    risk, features, joined = (numeric[key] for key in ("risk", "features", "joined"))
    mask = read_gzip(ROOT / cfg["evaluation_mask_path"])
    windows = read_gzip(ROOT / cfg["residual_rank_windows_path"])["windows"]
    require(mask == freeze_mask(joined, cfg["stocks"], cfg["dates"])
        and windows == source_windows(features), "Sealed transport targets or past ranks changed")
    require(cfg["per_condition_producer_scope"] == {
        "paths": len(cfg["baskets"]), "full_path_ledger_records": len(cfg["baskets"]) * len(cfg["dates"]),
        "asset_calls": len(cfg["stocks"]) * len(cfg["dates"]), "first_basket_complete_replays": 1},
        "Transport producer scale differs from full declared cohort")
    require(cfg["per_seed_audit_scope"] == audit_scope(cfg, joined), "Transport independent scope differs")
    return cfg, risk, features, joined, mask


def shocks_for(basket, dates):
    return {"scenario_id": "temporal_no_policy_or_industry_pulses_2022", "common": [0.0] * len(dates),
        "asset_specific": {stock: [0.0] * len(dates) for stock in basket}}


def seed_paths(cfg, risk, features, seed):
    core = {**cfg["model_design"]["core"], "seed": seed["arrival_seed"],
        "scenario_id": f"endogenous_cohort_seed{seed['arrival_seed']}"}
    background = {**cfg["model_design"]["background"], "seed": seed["background_seed"]}
    parameters = {**cfg["issuer_parameters"], "innovation_seed": seed["innovation_seed"],
        "information_seed": seed["information_seed"]}
    common = "marketmirror-public-market-factor-v1:" + seed["innovation_seed"]
    coupling = cfg["residual_common_seed_namespace"] + seed["innovation_seed"]
    paths = {dependence + "_" + mode + "_" + delivery: build_path(risk, features, "temporal_candidate_2022",
        parameters, mode, common, delivery, coupling if dependence == "historical_rank" else None)
        for dependence in ("independent", "historical_rank")
        for mode in ("market_independent", "market_shared") for delivery in ("masked", "public", "whole_public")}
    return core, background, paths


def audit_scope(cfg, joined, conditions=None):
    n = len(CELLS) if conditions is None else conditions
    stocks, days, baskets = len(cfg["stocks"]), len(cfg["dates"]), len(cfg["baskets"])
    strategies = len(cfg["agents"]) * len(cfg["model_design"]["core"]["inventory"])
    backgrounds = cfg["model_design"]["background"]["participants"]
    positions = stocks * days
    return {"independently_rebuilt_residual_rank_windows": positions,
        "independently_verified_common_date_draws": days,
        "independently_verified_coupled_innovations": 6 * positions,
        "independently_rebuilt_budget_rows": 12 * positions,
        "independently_integrated_public_budget_rows": 4 * positions,
        "independently_integrated_whole_public_budget_rows": 4 * positions,
        "paths": n * baskets, "full_path_ledger_records": n * baskets * days,
        "asset_calls": n * positions, "strategy_allocation_checks": n * baskets * days * strategies,
        "dense_asset_calls": n * stocks * len(cfg["dense_audit_sessions"]),
        "strict_combined_receipt_positions": n * positions * (strategies + backgrounds),
        "complete_wealth_drawdown_risk_accounts": n * (baskets * strategies + stocks * backgrounds),
        "suspended_asset_calls": n * sum(row["execution_available"] is False for rows in joined.values() for row in rows),
        "unknown_target_asset_calls_preserved": n * sum(row["observed_return"] is None for rows in joined.values() for row in rows),
        "conditions": n}


def gaps(row):
    v = row["joint_checks"]["values"]
    return {"mean": v["absolute_mean_gap"], "volatility": None if v["volatility_ratio"] is None else abs(v["volatility_ratio"] - 1),
        "zero_fraction": v["zero_fraction_gap"], "stock_correlation": v["stock_correlation_gap"],
        "portfolio_volatility": None if v["portfolio_volatility_ratio"] is None else abs(v["portfolio_volatility_ratio"] - 1)}


def summarize(variants, seeds):
    require(len(variants) == len(seeds) * len(CELLS), "Incomplete transport summary")
    index = {(r["seed_id"], r["background_anchor"], r["feedback_scale"], r["residual_dependence"], r["risk_mode"], r["delivery"]): r
        for r in variants}
    grid = {(s["seed_id"], c["background_anchor"], c["feedback_scale"], c["residual_dependence"], c["risk_mode"], c["delivery"])
        for s in seeds for c in CELLS}
    require(set(index) == grid and len(index) == len(variants), "Duplicate, missing or extra transport condition")
    for seed in seeds:
        for cell in CELLS:
            row = index[seed["seed_id"], cell["background_anchor"], cell["feedback_scale"], cell["residual_dependence"], cell["risk_mode"], cell["delivery"]]
            require(all(row[key] == value for key, value in cell.items()), "Transport condition name or identity changed")
    def effect(treatment, reference):
        treated, original = index[treatment], index[reference]
        change = difference(gaps(treated), gaps(original))
        return {"metric_changes": difference(values(treated), values(original)), "gap_changes": change,
            "all_five_gaps_nonworse": all(v is not None and v <= 1e-12 for v in change.values())}
    coverage, dependence, dep_interactions, anchor_interactions, lookup = [], [], [], [], {}
    for seed in seeds:
        sid = seed["seed_id"]
        for anchor in ("initial_inventory", "current_inventory"):
            for scale in (1.0, 0.0):
                for mode in ("market_independent", "market_shared"):
                    for dep in ("independent", "historical_rank"):
                        for ref in ("masked", "public"):
                            row = {"seed_id": sid, "background_anchor": anchor, "feedback_scale": scale,
                                "risk_mode": mode, "residual_dependence": dep, "reference_delivery": ref,
                                **effect((sid, anchor, scale, dep, mode, "whole_public"), (sid, anchor, scale, dep, mode, ref))}
                            coverage.append(row)
                            lookup[sid, anchor, scale, dep, mode, ref] = row
                    for delivery in ("masked", "public", "whole_public"):
                        dependence.append({"seed_id": sid, "background_anchor": anchor, "feedback_scale": scale,
                            "risk_mode": mode, "delivery": delivery,
                            **effect((sid, anchor, scale, "historical_rank", mode, delivery), (sid, anchor, scale, "independent", mode, delivery))})
                    for ref in ("masked", "public"):
                        rank, iid = (lookup[sid, anchor, scale, dep, mode, ref] for dep in ("historical_rank", "independent"))
                        dep_interactions.append({"seed_id": sid, "background_anchor": anchor, "feedback_scale": scale,
                            "risk_mode": mode, "reference_delivery": ref,
                            "metric_interaction": difference(rank["metric_changes"], iid["metric_changes"]),
                            "gap_interaction": difference(rank["gap_changes"], iid["gap_changes"])})
        for scale in (1.0, 0.0):
            for mode in ("market_independent", "market_shared"):
                for dep in ("independent", "historical_rank"):
                    for ref in ("masked", "public"):
                        current, initial = (lookup[sid, anchor, scale, dep, mode, ref] for anchor in ("current_inventory", "initial_inventory"))
                        anchor_interactions.append({"seed_id": sid, "residual_dependence": dep, "feedback_scale": scale,
                            "risk_mode": mode, "reference_delivery": ref,
                            "metric_interaction": difference(current["metric_changes"], initial["metric_changes"]),
                            "gap_interaction": difference(current["gap_changes"], initial["gap_changes"])})
    require((len(coverage), len(dependence), len(dep_interactions), len(anchor_interactions)) ==
        (32 * len(seeds), 24 * len(seeds), 16 * len(seeds), 16 * len(seeds)), "Incomplete factorial contrasts")
    groups = {}
    for cell in CELLS:
        rows = [r for r in variants if r["name"] == cell["name"]]
        groups[cell["name"]] = {"seed_count": len(rows), "joint_pass_seeds": sum(r["joint_checks"]["all_five_pass"] for r in rows),
            "metric_medians": {k: statistics.median(numbers) if all(v is not None for v in numbers) else None
                for k in FIELDS for numbers in [[values(r)[k] for r in rows]]}}
    return {"seed_summary": groups, "coverage_pairs": coverage, "residual_dependence_pairs": dependence,
        "coverage_dependence_interactions": dep_interactions, "coverage_anchor_interactions": anchor_interactions,
        "joint_pass_conditions": sum(r["joint_checks"]["all_five_pass"] for r in variants)}
