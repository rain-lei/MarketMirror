"""Fixed joint descriptive checks and paired seed summaries, without ranking."""
from __future__ import annotations

import statistics

from .audit_pre_wuhan_common_factor_2019 import common_factor_metrics
from .audit_synthetic_observed_returns import compare_pairs


DEFAULT_LIMITS = {"mean_return_gap_max": 0.005, "volatility_ratio_min": 0.5, "volatility_ratio_max": 2.0,
                  "zero_return_fraction_gap_max": 0.1, "stock_correlation_gap_max": 0.1,
                  "portfolio_volatility_ratio_min": 0.5, "portfolio_volatility_ratio_max": 2.0}


def joint_checks(comparison, common, limits):
    actual, observed = comparison["synthetic"], comparison["observed"]
    a, o = common["synthetic"], common["observed"]
    vol_ratio = actual["standard_deviation"] / observed["standard_deviation"]
    portfolio_ratio = a["equal_weight_daily_return_std"] / o["equal_weight_daily_return_std"]
    corr_gap = (abs(a["mean_pairwise_stock_return_correlation"] - o["mean_pairwise_stock_return_correlation"])
                if a["mean_pairwise_stock_return_correlation"] is not None else None)
    values = {"absolute_mean_gap": abs(actual["mean"] - observed["mean"]),
              "volatility_ratio": vol_ratio, "zero_fraction_gap": abs(actual["zero_return_fraction"] - observed["zero_return_fraction"]),
              "stock_correlation_gap": corr_gap, "portfolio_volatility_ratio": portfolio_ratio}
    flags = {"mean": values["absolute_mean_gap"] <= limits["mean_return_gap_max"],
             "volatility": limits["volatility_ratio_min"] <= vol_ratio <= limits["volatility_ratio_max"],
             "zero_fraction": values["zero_fraction_gap"] <= limits["zero_return_fraction_gap_max"],
             "stock_correlation": corr_gap is not None and corr_gap <= limits["stock_correlation_gap_max"],
             "portfolio_volatility": limits["portfolio_volatility_ratio_min"] <= portfolio_ratio <= limits["portfolio_volatility_ratio_max"]}
    return {"values": values, "criteria": flags, "all_five_pass": all(flags.values()),
            "interpretation": "Prespecified development tolerances, not empirical investor validation or a prediction release gate."}


def metrics(rows, limits):
    comparison = compare_pairs([(row["price_after_minor"] / row["price_before_minor"] - 1, row["observed_return"]) for row in rows])
    common = common_factor_metrics(rows)
    return {"comparison": comparison, "common_factor_metrics": common, "joint_checks": joint_checks(comparison, common, limits)}


def seed_summary(variants):
    """Compare each intervention with its same-seed, same-anchor full baseline."""
    index = {(row["seed_id"], row["name"]): row for row in variants}
    seeds = list(dict.fromkeys(row["seed_id"] for row in variants))
    output = {}
    for name in dict.fromkeys(row["name"] for row in variants):
        rows = [index[seed, name] for seed in seeds]
        fields = {"mean": [row["comparison"]["synthetic"]["mean"] for row in rows],
                  "volatility": [row["comparison"]["synthetic"]["standard_deviation"] for row in rows],
                  "zero_fraction": [row["comparison"]["synthetic"]["zero_return_fraction"] for row in rows],
                  "stock_correlation": [row["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"] for row in rows],
                  "portfolio_volatility": [row["common_factor_metrics"]["synthetic"]["equal_weight_daily_return_std"] for row in rows]}
        deltas = []
        for row in rows:
            base_name = row["background_anchor"] + "_feedback_full"
            base = index[row["seed_id"], base_name]
            current, reference = row["joint_checks"]["values"], base["joint_checks"]["values"]
            def gaps(value):
                return {"mean": value["absolute_mean_gap"], "volatility": abs(value["volatility_ratio"] - 1),
                        "zero_fraction": value["zero_fraction_gap"], "stock_correlation": value["stock_correlation_gap"],
                        "portfolio_volatility": abs(value["portfolio_volatility_ratio"] - 1)}
            a, b = gaps(current), gaps(reference)
            change = {key: a[key] - b[key] if a[key] is not None and b[key] is not None else None for key in a}
            deltas.append({"seed_id": row["seed_id"], "gap_changes": change,
                           "all_five_nonworse": all(v is not None and v <= 1e-12 for v in change.values())})
        output[name] = {"seed_count": len(rows), "all_five_pass_seeds": sum(row["joint_checks"]["all_five_pass"] for row in rows),
                        "metrics": {key: {"min": min(v for v in values if v is not None),
                                           "median": statistics.median(v for v in values if v is not None),
                                           "max": max(v for v in values if v is not None)}
                                    if any(v is not None for v in values) else {"min": None, "median": None, "max": None}
                                    for key, values in fields.items()},
                        "paired_changes": deltas, "all_five_nonworse_seeds": sum(row["all_five_nonworse"] for row in deltas)}
    return output
