"""Independent signed contrasts, full raw statistics and all account summaries."""
from collections import Counter, defaultdict
import math
import statistics

from .temporal_transport_study import ROOT, CONFIG, OUTPUT, CELLS, load_study, read, encoded, require, checkpoint
from .audit_temporal_statistics import independent_metrics
from .verify_pre_wuhan_order_price_diagnostics_2019 import compare
from .semantic_memory_sensitivity import canonical_hash
from ..data_pipeline.provenance import file_sha256

FIELDS = ("mean", "volatility", "zero_fraction", "stock_correlation", "portfolio_volatility")


def identity(row):
    return (row["seed_id"], row["background_anchor"], row["feedback_scale"], row["residual_dependence"], row["risk_mode"], row["delivery"])


def scalar_view(data):
    stock, common = data["comparison"]["synthetic"], data["common_factor_metrics"]["synthetic"]
    return {"mean": stock["mean"], "volatility": stock["standard_deviation"], "zero_fraction": stock["zero_return_fraction"],
        "stock_correlation": common["mean_pairwise_stock_return_correlation"], "portfolio_volatility": common["equal_weight_daily_return_std"]}


def gap_view(values):
    return {"mean": values["absolute_mean_gap"], "volatility": None if values["volatility_ratio"] is None else abs(values["volatility_ratio"] - 1),
        "zero_fraction": values["zero_fraction_gap"], "stock_correlation": values["stock_correlation_gap"],
        "portfolio_volatility": None if values["portfolio_volatility_ratio"] is None else abs(values["portfolio_volatility_ratio"] - 1)}


def verify_contrasts(variants, seeds, summary, rebuilt):
    indexed = {identity(row): row for row in variants}
    grid = {(s["seed_id"], c["background_anchor"], c["feedback_scale"], c["residual_dependence"], c["risk_mode"], c["delivery"])
        for s in seeds for c in CELLS}
    require(len(variants) == len(indexed) == len(grid) and set(indexed) == set(rebuilt) == grid, "Incomplete independent factorial")
    metric = {key: scalar_view(data) for key, data in rebuilt.items()}
    gaps = {key: gap_view(data["joint_values"]) for key, data in rebuilt.items()}
    def contrast(terms, source):
        return {field: math.fsum(weight * source[key][field] for key, weight in terms)
            if all(source[key][field] is not None for key, _ in terms) else None for field in FIELDS}
    # Independently use signed four-cell contrasts instead of differences of the
    # producer's pair records; compare each identity regardless of list order.
    kinds = {"coverage_pairs": ("seed_id", "background_anchor", "feedback_scale", "residual_dependence", "risk_mode", "reference_delivery"),
        "residual_dependence_pairs": ("seed_id", "background_anchor", "feedback_scale", "risk_mode", "delivery"),
        "coverage_dependence_interactions": ("seed_id", "background_anchor", "feedback_scale", "risk_mode", "reference_delivery"),
        "coverage_anchor_interactions": ("seed_id", "residual_dependence", "feedback_scale", "risk_mode", "reference_delivery")}
    required = {kind: set() for kind in kinds}
    for sid, anchor, scale, dep, mode, delivery in grid:
        if delivery != "whole_public":
            continue
        for ref in ("masked", "public"):
            required["coverage_pairs"].add((sid, anchor, scale, dep, mode, ref))
            if dep == "historical_rank":
                required["coverage_dependence_interactions"].add((sid, anchor, scale, mode, ref))
            if anchor == "current_inventory":
                required["coverage_anchor_interactions"].add((sid, dep, scale, mode, ref))
    required["residual_dependence_pairs"] = {(sid, anchor, scale, mode, delivery)
        for sid, anchor, scale, dep, mode, delivery in grid if dep == "historical_rank"}
    for kind, fields in kinds.items():
        rows = summary[kind]
        seen = {tuple(row[field] for field in fields) for row in rows}
        require(len(rows) == len(seen) and seen == required[kind], "Incomplete or duplicated independent contrast: " + kind)
        for row in rows:
            sid, scale, mode = row["seed_id"], row["feedback_scale"], row["risk_mode"]
            if kind == "coverage_pairs":
                anchor, dep = row["background_anchor"], row["residual_dependence"]
                terms = [((sid, anchor, scale, dep, mode, "whole_public"), 1),
                    ((sid, anchor, scale, dep, mode, row["reference_delivery"]), -1)]
            elif kind == "residual_dependence_pairs":
                anchor, delivery = row["background_anchor"], row["delivery"]
                terms = [((sid, anchor, scale, "historical_rank", mode, delivery), 1),
                    ((sid, anchor, scale, "independent", mode, delivery), -1)]
            elif kind == "coverage_dependence_interactions":
                anchor, ref = row["background_anchor"], row["reference_delivery"]
                terms = [((sid, anchor, scale, "historical_rank", mode, "whole_public"), 1),
                    ((sid, anchor, scale, "historical_rank", mode, ref), -1),
                    ((sid, anchor, scale, "independent", mode, "whole_public"), -1),
                    ((sid, anchor, scale, "independent", mode, ref), 1)]
            else:
                dep, ref = row["residual_dependence"], row["reference_delivery"]
                terms = [((sid, "current_inventory", scale, dep, mode, "whole_public"), 1),
                    ((sid, "current_inventory", scale, dep, mode, ref), -1),
                    ((sid, "initial_inventory", scale, dep, mode, "whole_public"), -1),
                    ((sid, "initial_inventory", scale, dep, mode, ref), 1)]
            expected_metric, expected_gap = contrast(terms, metric), contrast(terms, gaps)
            interaction = kind.endswith("interactions")
            compare(expected_metric, row["metric_interaction" if interaction else "metric_changes"])
            compare(expected_gap, row["gap_interaction" if interaction else "gap_changes"])
            if not interaction:
                require(row["all_five_gaps_nonworse"] is all(value is not None and value <= 1e-12 for value in expected_gap.values()),
                    "Independent nonworse flag differs")
    expected_groups = {}
    for cell in CELLS:
        keys = [identity({"seed_id": seed["seed_id"], **cell}) for seed in seeds]
        expected_groups[cell["name"]] = {"seed_count": len(seeds),
            "joint_pass_seeds": sum(rebuilt[key]["all_five_pass"] for key in keys),
            "metric_medians": {field: statistics.median(numbers) if all(v is not None for v in numbers) else None
                for field in FIELDS for numbers in [[metric[key][field] for key in keys]]}}
    compare(expected_groups, summary["seed_summary"])
    require(summary["joint_pass_conditions"] == sum(data["all_five_pass"] for data in rebuilt.values()),
        "Independent joint-pass count differs")
    return {kind: len(rows) for kind, rows in required.items()}


def audit_summary():
    cfg, _, _, _, mask = load_study()
    result_path, target = OUTPUT / "results.json", OUTPUT / "final_verification.json"
    require(not target.exists(), "Preserve final independent transport verification")
    result = read(result_path)
    require(result["status"] == "COMPLETE_FULL_TRANSPORT_STUDY_2022_V1"
        and result["protocol_sha256"] == file_sha256(CONFIG), "Complete registered transport results required")
    for name, digest in result["artifacts"].items():
        require(file_sha256(ROOT / name) == digest, "Transport completed artifact changed")
    rebuilt, origins, aggregates, bindings = {}, {}, {}, {}
    accounts = 0
    compact = {identity(row): row for row in result["variants"]}
    for si, seed in enumerate(cfg["seeds"]):
        for ci, cell in enumerate(CELLS):
            folder = OUTPUT / f"seed_{si}/cell_{ci}"
            checkpoint(folder, [seed["seed_id"], cell["name"]], CONFIG)
            saved = read(folder / "condition.json")
            row = saved["variant"]
            key = identity(row)
            require(key not in rebuilt, "Duplicate physical transport condition")
            compare({k: v for k, v in row.items() if k != "daily_asset_rows"}, compact[key])
            raw = independent_metrics(row["daily_asset_rows"], mask, cfg["joint_limits"])
            for name in ("comparison", "common_factor_metrics", "all_model_return_distribution"):
                compare(raw[name], row[name])
            compare(raw["joint_values"], row["joint_checks"]["values"])
            require(raw["joint_criteria"] == row["joint_checks"]["criteria"]
                and raw["all_five_pass"] is row["joint_checks"]["all_five_pass"], "Independent transport criteria differ")
            rebuilt[key] = raw
            origin = []
            group = aggregates.setdefault(cell["residual_dependence"] + "|" + cell["delivery"], defaultdict(Counter))
            require([path["basket_index"] for path in saved["paths"]] == list(range(len(cfg["baskets"]))), "Missing account basket")
            for path in saved["paths"]:
                initial = {}
                summary = path["summary"]
                for actor, account in summary["accounts"].items():
                    nav = sum(account["wallets"].values()) + sum(account["shares"][stock] * summary["final_prices_minor"][stock]
                        for stock in cfg["baskets"][path["basket_index"]])
                    require(nav == account["final_wealth_minor"] and account["initial_wealth_minor"] > 0
                        and account["wealth_multiple"] == nav / account["initial_wealth_minor"]
                        and math.isfinite(account["max_drawdown"]) and 0 <= account["max_drawdown"] <= 1, "Transport account summary differs")
                    role = "background" if account["kind"] == "background" else account["role"]
                    agg = group[role]
                    agg["accounts"] += 1
                    for name in ("initial_wealth_minor", "final_wealth_minor", "risk_breach_sessions", "concentration_breach_sessions"):
                        agg[name] += account[name]
                    agg["max_drawdown_sum"] += account["max_drawdown"]
                    initial[actor] = account["initial_wealth_minor"]
                    accounts += 1
                origin.append({"participant_specs": path["participant_specs"], "initial_wealth": initial})
            origins[key] = {"sha256": canonical_hash(origin), "accounts": sum(len(path["initial_wealth"]) for path in origin)}
            bindings[(folder / "checkpoint.json").relative_to(ROOT).as_posix()] = file_sha256(folder / "checkpoint.json")
        print(f"Independent transport factorial statistics and accounts: seed {si + 1}/5 complete.", flush=True)
    contrasts = verify_contrasts(result["variants"], cfg["seeds"], result, rebuilt)
    wealth_pairs = dependence_accounts = 0
    for key, origin in origins.items():
        sid, anchor, scale, dep, mode, delivery = key
        if delivery == "whole_public":
            for ref in ("masked", "public"):
                require(origin == origins[sid, anchor, scale, dep, mode, ref], "Information coverage changed initial resources")
                wealth_pairs += origin["accounts"]
        if dep == "historical_rank":
            require(origin == origins[sid, anchor, scale, "independent", mode, delivery], "Residual dependence changed initial resources")
            dependence_accounts += origin["accounts"]
    require(accounts == cfg["planned_full_scope"]["complete_account_summaries"] == 472320
        and wealth_pairs == 314880 and dependence_accounts == 236160, "Incomplete full-cohort wealth checks")
    proof = {"status": "PASS_FULL_240_TRANSPORT_RAW_STATISTICS_CONTRASTS_AND_ALL_ACCOUNTS",
        "protocol_sha256": file_sha256(CONFIG), "unique_conditions": len(rebuilt), **contrasts,
        "complete_account_summaries": accounts, "coverage_same_initial_resource_account_pairs": wealth_pairs,
        "dependence_same_initial_resource_account_pairs": dependence_accounts,
        "role_wealth_aggregates": {group: {role: dict(values) for role, values in roles.items()} for group, roles in aggregates.items()},
        "artifacts": {**bindings, result_path.relative_to(ROOT).as_posix(): file_sha256(result_path)},
        "no_new_parameter_default": True, "point_in_time_feed_certified": False, "economic_total_return_certified": False}
    with target.open("xb") as stream:
        stream.write(encoded(proof))
    print("Independent full transport factorial and all accounts verified.", flush=True)


if __name__ == "__main__":
    audit_summary()
