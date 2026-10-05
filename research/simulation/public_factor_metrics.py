"""Retain the complete public target/quote factorial and paired seed effects."""
from __future__ import annotations

import statistics

FIELDS = ("mean", "volatility", "zero_fraction", "stock_correlation", "portfolio_volatility")


def expected_coverage(seeds, baskets, sessions, assets, strategies, backgrounds):
    if any(type(v) is not int or v <= 0 for v in (seeds, baskets, sessions, assets, strategies, backgrounds)):
        raise ValueError("public factor coverage requires positive integer dimensions")
    original = seeds * 4 * baskets * sessions
    days = original * 4
    return {"conditions": seeds * 16, "paths": seeds * 16 * baskets,
        "full_path_ledger_records": days, "asset_calls": days * assets,
        "strategy_allocation_checks": days * strategies,
        "legacy_complete_paths_matched": seeds * 4 * baskets,
        "legacy_daily_records_matched": original, "baseline_same_state_records_audited": original,
        "same_state_public_records": original * 3,
        "quote_only_same_state_strategy_targets": original * strategies,
        "target_only_same_state_backgrounds": original * backgrounds * assets,
        "paired_private_receipt_values": seeds * 15 * baskets * sessions * assets * (strategies + backgrounds),
        "dense_asset_calls": seeds * 16 * baskets * 2 * assets,
        "independent_lagged_feature_rows": baskets * assets * sessions,
        "independent_seeded_public_rows": seeds * baskets * assets * sessions,
        "common_innovation_draws": seeds * sessions}


def values(row):
    a, c = row["comparison"]["synthetic"], row["common_factor_metrics"]["synthetic"]
    return {"mean": a["mean"], "volatility": a["standard_deviation"], "zero_fraction": a["zero_return_fraction"],
        "stock_correlation": c["mean_pairwise_stock_return_correlation"], "portfolio_volatility": c["equal_weight_daily_return_std"]}


def difference(a, b):
    return {k: a[k] - b[k] if a[k] is not None and b[k] is not None else None for k in FIELDS}


def summarize(variants):
    seeds = list(dict.fromkeys(v["seed_id"] for v in variants))
    index = {(v["seed_id"], v["background_anchor"], v["feedback_scale"], v["public_mode"]): v for v in variants}
    grid = {(s, a, f, p) for s in seeds for a in ("initial_inventory", "current_inventory")
            for f in (1.0, 0.0) for p in ("none", "targets_only", "quotes_only", "both")}
    if set(index) != grid or len(variants) != len(grid):
        raise ValueError("public summary requires the complete fixed factorial")
    summary, interactions = {}, {}
    for name in dict.fromkeys(v["name"] for v in variants):
        rows = [v for v in variants if v["name"] == name]
        changes = []
        for r in rows:
            base = index[r["seed_id"], r["background_anchor"], r["feedback_scale"], "none"]
            def gaps(v):
                return {"mean": v["absolute_mean_gap"], "volatility": abs(v["volatility_ratio"] - 1),
                    "zero_fraction": v["zero_fraction_gap"], "stock_correlation": v["stock_correlation_gap"],
                    "portfolio_volatility": abs(v["portfolio_volatility_ratio"] - 1)}
            d = difference(gaps(r["joint_checks"]["values"]), gaps(base["joint_checks"]["values"]))
            changes.append({"seed_id": r["seed_id"], "gap_changes": d,
                "all_five_nonworse": all(v is not None and v <= 1e-12 for v in d.values())})
        summary[name] = {"seed_count": len(rows), "joint_pass_seeds": sum(r["joint_checks"]["all_five_pass"] for r in rows),
            "metric_medians": {k: statistics.median(v) if v else None for k in FIELDS
                for v in [[values(r)[k] for r in rows if values(r)[k] is not None]]},
            "paired_gap_changes": changes, "all_five_nonworse_seeds": sum(r["all_five_nonworse"] for r in changes)}
    for anchor in ("initial_inventory", "current_inventory"):
        interactions[anchor] = []
        for seed in seeds:
            entry = {"seed_id": seed}
            for f in (1.0, 0.0):
                v = {p: values(index[seed, anchor, f, p]) for p in ("none", "targets_only", "quotes_only", "both")}
                for p in ("targets_only", "quotes_only", "both"):
                    entry[p + "|" + str(f)] = difference(v[p], v["none"])
                entry["target_quote_interaction|" + str(f)] = difference(difference(v["both"], v["targets_only"]), difference(v["quotes_only"], v["none"]))
            for p in ("targets_only", "quotes_only", "both"):
                entry[p + "|own_feedback_interaction"] = difference(entry[p + "|0.0"], entry[p + "|1.0"])
            interactions[anchor].append(entry)
    return {"seed_summary": summary, "paired_public_channel_effects_and_interactions": interactions}
