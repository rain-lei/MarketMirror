"""Fixed observation masks, complete model paths and missing-aware comparisons."""
from __future__ import annotations
from itertools import combinations
import math
import statistics

from .audit_synthetic_observed_returns import compare_pairs, describe
from .semantic_memory_sensitivity import canonical_hash


def require(value, message):
    if not value:
        raise ValueError(message)


def correlation(left, right):
    if len(left) < 2:
        return None
    ml, mr = statistics.fmean(left), statistics.fmean(right)
    dl, dr = [v - ml for v in left], [v - mr for v in right]
    denominator = math.sqrt(sum(v * v for v in dl) * sum(v * v for v in dr))
    return sum(a * b for a, b in zip(dl, dr, strict=True)) / denominator if denominator else None


def freeze_mask(joined, stocks, dates):
    require(stocks == sorted(set(stocks)) and dates == sorted(set(dates)) and stocks and dates,
        "complete sorted evaluation scope required")
    require(set(joined) == set(stocks), "evaluation stock scope differs")
    known, targets, unknown = {}, {}, []
    for stock in stocks:
        rows = joined[stock]
        require([r["trade_date"] for r in rows] == dates, "evaluation calendar differs")
        known[stock], targets[stock] = [], []
        for row in rows:
            target = row["observed_return"]
            require(target is None or (type(target) in (int, float) and math.isfinite(target) and target > -1),
                "invalid source target")
            targets[stock].append(target)
            if target is None:
                unknown.append([stock, row["trade_date"]])
            else:
                known[stock].append(row["trade_date"])
    common = [d for d in dates if all(d in known[s] for s in stocks)]
    target_index = {(s, d): v for s in stocks for d, v in zip(dates, targets[s], strict=True)}
    pairs = []
    for left, right in combinations(stocks, 2):
        intersection = [d for d in dates if d in known[left] and d in known[right]]
        corr = correlation([target_index[left, d] for d in intersection], [target_index[right, d] for d in intersection])
        pairs.append({"left": left, "right": right, "known_dates": intersection,
            "observed_correlation": corr, "primary_eligible": corr is not None})
    return {"version": "full-cohort-observed-return-mask-v1", "stocks": stocks, "dates": dates,
        "targets_by_stock": targets, "known_dates_by_stock": known, "unknown_positions": unknown,
        "full_cohort_portfolio_common_dates": common, "correlation_pairs": pairs,
        "model_paths_require_full_grid": True, "missing_targets_filled": False,
        "correlation_rule": "Same observed-known dates per stock pair in all conditions; fixed observed-defined pair universe. Any undefined synthetic correlation makes the primary aggregate null.",
        "portfolio_rule": "Equal weight over all declared stocks; evaluate only predeclared full-cohort common dates. No varying-weight renormalization."}


def joint_checks(comparison, common, limits):
    a, o = comparison["synthetic"], comparison["observed"]
    ac, oc = common["synthetic"], common["observed"]
    def ratio(left, right):
        return left / right if left is not None and right is not None and right > 0 else None
    def gap(left, right):
        return abs(left - right) if left is not None and right is not None else None
    values = {"absolute_mean_gap": gap(a["mean"], o["mean"]),
        "volatility_ratio": ratio(a["standard_deviation"], o["standard_deviation"]),
        "zero_fraction_gap": gap(a["zero_return_fraction"], o["zero_return_fraction"]),
        "stock_correlation_gap": gap(ac["mean_pairwise_stock_return_correlation"], oc["mean_pairwise_stock_return_correlation"]),
        "portfolio_volatility_ratio": ratio(ac["equal_weight_daily_return_std"], oc["equal_weight_daily_return_std"])}
    flags = {"mean": values["absolute_mean_gap"] is not None and values["absolute_mean_gap"] <= limits["mean_return_gap_max"],
        "volatility": values["volatility_ratio"] is not None and limits["volatility_ratio_min"] <= values["volatility_ratio"] <= limits["volatility_ratio_max"],
        "zero_fraction": values["zero_fraction_gap"] is not None and values["zero_fraction_gap"] <= limits["zero_return_fraction_gap_max"],
        "stock_correlation": values["stock_correlation_gap"] is not None and values["stock_correlation_gap"] <= limits["stock_correlation_gap_max"],
        "portfolio_volatility": values["portfolio_volatility_ratio"] is not None and limits["portfolio_volatility_ratio_min"] <= values["portfolio_volatility_ratio"] <= limits["portfolio_volatility_ratio_max"]}
    return {"values": values, "criteria": flags, "all_five_pass": all(flags.values()),
        "interpretation": "Prespecified descriptive development tolerances; conditional historical simulation, not high-fidelity validation or a forecast release."}


def metrics(rows, mask, limits):
    stocks, dates = mask["stocks"], mask["dates"]
    panel = {(r["stock_code"], r["trade_date"]): r for r in rows}
    expected = {(s, d) for s in stocks for d in dates}
    require(len(panel) == len(rows) == len(expected) and set(panel) == expected,
        "all simulation paths must remain in the evaluation panel")
    targets = {(s, d): v for s in stocks for d, v in zip(dates, mask["targets_by_stock"][s], strict=True)}
    synthetic = {}
    for key, row in panel.items():
        require(row["observed_return"] == targets[key], "evaluation target or null differs from frozen mask")
        before, after = row["price_before_minor"], row["price_after_minor"]
        require(type(before) is int and type(after) is int and min(before, after) > 0, "invalid model price")
        synthetic[key] = after / before - 1
    paired_keys = [(s, d) for s in stocks for d in mask["known_dates_by_stock"][s]]
    require(paired_keys, "paired source observations required")
    comparison = compare_pairs([(synthetic[k], targets[k]) for k in paired_keys])
    correlations = {"observed": [], "synthetic": []}
    undefined_synthetic_pairs = []
    eligible = [p for p in mask["correlation_pairs"] if p["primary_eligible"]]
    for pair in eligible:
        left, right, ds = pair["left"], pair["right"], pair["known_dates"]
        actual = correlation([synthetic[left, d] for d in ds], [synthetic[right, d] for d in ds])
        correlations["observed"].append(pair["observed_correlation"])
        if actual is None:
            undefined_synthetic_pairs.append([left, right])
        else:
            correlations["synthetic"].append(actual)
    common_dates = mask["full_cohort_portfolio_common_dates"]
    common = {}
    for label, values in (("observed", targets), ("synthetic", synthetic)):
        portfolio = [statistics.fmean(values[s, d] for s in stocks) for d in common_dates]
        common[label] = {"stock_count": len(stocks), "sessions": len(common_dates),
            "equal_weight_daily_return_std": statistics.pstdev(portfolio) if len(portfolio) >= 2 else None,
            "mean_pairwise_stock_return_correlation": statistics.fmean(correlations[label])
                if eligible and (label == "observed" or not undefined_synthetic_pairs) else None,
            "pairwise_correlations_defined": len(correlations[label]),
            "fixed_primary_pair_count": len(eligible)}
    coverage = {"full_simulated_company_days": len(rows), "paired_company_days": len(paired_keys),
        "unknown_target_company_days": len(mask["unknown_positions"]), "full_calendar_sessions": len(dates),
        "full_cohort_portfolio_common_sessions": len(common_dates),
        "portfolio_excluded_dates": [d for d in dates if d not in common_dates],
        "possible_stock_pairs": len(mask["correlation_pairs"]), "frozen_observed_defined_stock_pairs": len(eligible),
        "undefined_synthetic_primary_pairs": undefined_synthetic_pairs,
        "mask_sha256": canonical_hash(mask), "missing_targets_filled": False}
    return {"comparison": comparison, "common_factor_metrics": common,
        "joint_checks": joint_checks(comparison, common, limits), "evaluation_coverage": coverage,
        "all_model_return_distribution": describe(list(synthetic.values()))}
