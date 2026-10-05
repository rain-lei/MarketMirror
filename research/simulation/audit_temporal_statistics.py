"""Independent paired and masked panel statistics using centered fsum arithmetic."""
from __future__ import annotations
import math
import statistics


def average(values):
    return math.fsum(values) / len(values)


def variance(values):
    mean = average(values)
    return math.fsum((v - mean) ** 2 for v in values) / len(values)


def correlation(left, right):
    if len(left) < 2:
        return None
    a, b = average(left), average(right)
    x, y = [v - a for v in left], [v - b for v in right]
    denominator = math.sqrt(math.fsum(v * v for v in x) * math.fsum(v * v for v in y))
    return math.fsum(a * b for a, b in zip(x, y, strict=True)) / denominator if denominator else None


def describe(values):
    ordered = sorted(abs(v) for v in values)
    return {"mean": average(values), "standard_deviation": math.sqrt(variance(values)),
        "mean_absolute_return": average(ordered), "p95_absolute_return": ordered[math.ceil(.95 * len(ordered)) - 1],
        "zero_return_fraction": sum(v == 0 for v in values) / len(values)}


def independent_metrics(rows, mask, limits):
    stocks, dates = mask["stocks"], mask["dates"]
    observed = {(s, d): v for s in stocks for d, v in zip(dates, mask["targets_by_stock"][s], strict=True)}
    panel = {(r["stock_code"], r["trade_date"]): r for r in rows}
    if len(panel) != len(rows) or set(panel) != set(observed) or any(r["observed_return"] != observed[k] for k, r in panel.items()):
        raise ValueError("independent statistics require entire declared model panel and fixed targets")
    synthetic = {k: r["price_after_minor"] / r["price_before_minor"] - 1 for k, r in panel.items()}
    keys = [(s, d) for s in stocks for d in dates if observed[s, d] is not None]
    ss, os = [synthetic[k] for k in keys], [observed[k] for k in keys]
    nonzero = [(a, b) for a, b in zip(ss, os, strict=True) if a != 0 and b != 0]
    comparison = {"pairs": len(keys), "synthetic": describe(ss), "observed": describe(os),
        "return_correlation": correlation(ss, os), "both_nonzero_pairs": len(nonzero),
        "same_sign_nonzero_fraction": sum((a > 0) == (b > 0) for a, b in nonzero) / len(nonzero) if nonzero else None}
    common = {}
    eligible = [p for p in mask["correlation_pairs"] if p["primary_eligible"]]
    undefined = []
    for label, values in (("observed", observed), ("synthetic", synthetic)):
        correlations = []
        for p in eligible:
            ds, a, b = p["known_dates"], p["left"], p["right"]
            corr = correlation([values[a, d] for d in ds], [values[b, d] for d in ds])
            if corr is None:
                if label == "synthetic":
                    undefined.append([a, b])
            else:
                correlations.append(corr)
        portfolio = [average([values[s, d] for s in stocks]) for d in mask["full_cohort_portfolio_common_dates"]]
        common[label] = {"stock_count": len(stocks), "sessions": len(portfolio),
            "equal_weight_daily_return_std": math.sqrt(variance(portfolio)) if len(portfolio) >= 2 else None,
            "mean_pairwise_stock_return_correlation": average(correlations) if len(correlations) == len(eligible) and eligible else None,
            "pairwise_correlations_defined": len(correlations), "fixed_primary_pair_count": len(eligible)}
    a, o, ac, oc = comparison["synthetic"], comparison["observed"], common["synthetic"], common["observed"]
    values = {"absolute_mean_gap": abs(a["mean"] - o["mean"]),
        "volatility_ratio": a["standard_deviation"] / o["standard_deviation"] if o["standard_deviation"] else None,
        "zero_fraction_gap": abs(a["zero_return_fraction"] - o["zero_return_fraction"]),
        "stock_correlation_gap": abs(ac["mean_pairwise_stock_return_correlation"] - oc["mean_pairwise_stock_return_correlation"])
            if ac["mean_pairwise_stock_return_correlation"] is not None and oc["mean_pairwise_stock_return_correlation"] is not None else None,
        "portfolio_volatility_ratio": ac["equal_weight_daily_return_std"] / oc["equal_weight_daily_return_std"]
            if ac["equal_weight_daily_return_std"] is not None and oc["equal_weight_daily_return_std"] else None}
    flags = {"mean": values["absolute_mean_gap"] <= limits["mean_return_gap_max"],
        "volatility": values["volatility_ratio"] is not None and limits["volatility_ratio_min"] <= values["volatility_ratio"] <= limits["volatility_ratio_max"],
        "zero_fraction": values["zero_fraction_gap"] <= limits["zero_return_fraction_gap_max"],
        "stock_correlation": values["stock_correlation_gap"] is not None and values["stock_correlation_gap"] <= limits["stock_correlation_gap_max"],
        "portfolio_volatility": values["portfolio_volatility_ratio"] is not None and limits["portfolio_volatility_ratio_min"] <= values["portfolio_volatility_ratio"] <= limits["portfolio_volatility_ratio_max"]}
    return {"comparison": comparison, "common_factor_metrics": common, "joint_values": values, "joint_criteria": flags,
        "all_five_pass": all(flags.values()), "undefined_synthetic_primary_pairs": undefined,
        "all_model_return_distribution": describe(list(synthetic.values()))}
