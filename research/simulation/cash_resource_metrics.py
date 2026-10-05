"""Paired cash-odds effects, all five outcomes retained without selection."""
from __future__ import annotations

import statistics

FIELDS = ("mean", "volatility", "zero_fraction", "stock_correlation", "portfolio_volatility")


def expected_coverage(seeds, baskets, sessions, assets, strategies, backgrounds):
    values = (seeds, baskets, sessions, assets, strategies, backgrounds)
    if any(type(v) is not int or v <= 0 for v in values):
        raise ValueError("cash coverage dimensions must be positive integers")
    original_days = seeds * 4 * baskets * sessions
    days = original_days * 3
    return {"conditions": seeds * 12, "paths": seeds * 12 * baskets,
            "full_path_ledger_records": days, "asset_calls": days * assets,
            "strategy_allocation_checks": days * strategies,
            "legacy_complete_paths_matched": seeds * 4 * baskets,
            "legacy_daily_records_matched": original_days,
            "baseline_funding_records_audited": original_days,
            "same_state_funding_records": original_days * 2,
            "initial_resource_receipts_audited": seeds * 8 * baskets,
            "paired_private_receipt_values": seeds * 11 * baskets * sessions * assets * (strategies + backgrounds),
            "dense_asset_calls": seeds * 12 * baskets * 2 * assets}


def measures(row):
    s, c = row["comparison"]["synthetic"], row["common_factor_metrics"]["synthetic"]
    return {"mean": s["mean"], "volatility": s["standard_deviation"], "zero_fraction": s["zero_return_fraction"],
            "stock_correlation": c["mean_pairwise_stock_return_correlation"], "portfolio_volatility": c["equal_weight_daily_return_std"]}


def resource_summary(variants):
    seeds = list(dict.fromkeys(v["seed_id"] for v in variants))
    index = {(v["seed_id"], v["background_anchor"], v["feedback_scale"], v["cash_name"]): v for v in variants}
    grid = {(s, a, f, c) for s in seeds for a in ("initial_inventory", "current_inventory")
            for f in (1.0, 0.0) for c in ("less_background", "original_cash", "more_background")}
    if set(index) != grid or len(variants) != len(grid):
        raise ValueError("cash summary requires all seeds, anchors, feedbacks and cash odds")
    result, paired = {}, {}
    for a in ("initial_inventory", "current_inventory"):
        paired[a] = []
        for seed in seeds:
            changes = {}
            for cash in ("less_background", "more_background"):
                for f in (1.0, 0.0):
                    base, current = [measures(index[seed, a, f, c]) for c in ("original_cash", cash)]
                    changes[cash + "|" + str(f)] = {k: current[k] - base[k] if base[k] is not None and current[k] is not None else None for k in FIELDS}
                off, full = changes[cash + "|0.0"], changes[cash + "|1.0"]
                changes[cash + "|interaction"] = {k: off[k] - full[k] if off[k] is not None and full[k] is not None else None for k in FIELDS}
            paired[a].append({"seed_id": seed, **changes})
    for name in dict.fromkeys(v["name"] for v in variants):
        rows = [v for v in variants if v["name"] == name]
        deltas = []
        for row in rows:
            reference = index[row["seed_id"], row["background_anchor"], row["feedback_scale"], "original_cash"]
            a, b = row["joint_checks"]["values"], reference["joint_checks"]["values"]
            def distance(v):
                return {"mean": v["absolute_mean_gap"], "volatility": abs(v["volatility_ratio"] - 1),
                        "zero_fraction": v["zero_fraction_gap"], "stock_correlation": v["stock_correlation_gap"],
                        "portfolio_volatility": abs(v["portfolio_volatility_ratio"] - 1)}
            current, original = distance(a), distance(b)
            gap = {k: current[k] - original[k] if current[k] is not None and original[k] is not None else None for k in FIELDS}
            deltas.append({"seed_id": row["seed_id"], "gap_changes": gap,
                           "all_five_nonworse": all(v is not None and v <= 1e-12 for v in gap.values())})
        result[name] = {"seed_count": len(rows), "joint_pass_seeds": sum(r["joint_checks"]["all_five_pass"] for r in rows),
            "metric_medians": {k: statistics.median(v) if v else None for k in FIELDS
                for v in [[measures(r)[k] for r in rows if measures(r)[k] is not None]]},
            "paired_gap_changes": deltas, "all_five_nonworse_seeds": sum(r["all_five_nonworse"] for r in deltas)}
    return {"seed_summary": result, "paired_cash_effects_and_feedback_interaction": paired}


def funding_totals(payload, specs):
    result = {}
    for rows in payload["orders"].values():
        for o in rows:
            role = "background" if specs[o["owner"]]["kind"] == "background" else specs[o["owner"]]["parameters"]["role"]
            totals = result.setdefault(role, {})
            for key, field in (("requested", "quantity"), ("accepted", "accepted_quantity")):
                k = key + "_" + o["side"]
                totals[k] = totals.get(k, 0) + o[field]
    return result
