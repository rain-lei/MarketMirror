"""Paired risk diagnostics with source-dated financial states and a frozen fit."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal


MARKET_FEATURES = ["stock_volatility_20", "benchmark_volatility_20",
                   "absolute_benchmark_momentum_5", "log_amount_surprise_20"]
FINANCIAL_FEATURES = ["log_reported_assets_yuan", "total_liabilities_to_assets", "money_funds_to_assets"]


def aware(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("information clocks require timezone-aware timestamps")
    return result


def split_dates(calendar: list[str], config: dict) -> dict:
    if calendar != sorted(set(calendar)):
        raise ValueError("exchange calendar must be unique and ordered")
    for day in calendar:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("calendar dates must be canonical")
    dates = [d for d in calendar if config["period_start"] <= d <= config["period_end"]]
    n = config["nominal_training_sessions"]
    lag = config["market_lag_sessions"]
    if (type(n) is not int or type(lag) is not int or lag < 1 or
            len(dates) != config["expected_sessions"] or
            len(dates) - n != config["evaluation_sessions"] or not 0 < n < len(dates)):
        raise ValueError("diagnostic calendar or split coverage differs")
    first_check_index = calendar.index(dates[n])
    if first_check_index < lag:
        raise ValueError("first evaluation date lacks information history")
    fit_cutoff = calendar[first_check_index - lag] + "T" + config["signal_cutoff_time"]
    moment = aware(fit_cutoff)
    train = [d for d in dates[:n] if aware(d + "T15:00:00+08:00") <= moment]
    purged = [d for d in dates[:n] if d not in train]
    if not train:
        raise ValueError("no training outcomes visible at the frozen fit cutoff")
    return {"dates": dates, "training_dates": train, "purged_dates": purged,
            "evaluation_dates": dates[n:], "model_fit_cutoff_at": fit_cutoff}


def build_panel(calendar: list[str], codes: list[str], market: dict, amounts: dict,
                states: dict, industries: dict, config: dict) -> tuple[list[dict], dict]:
    split = split_dates(calendar, config)
    if codes != sorted(set(codes)) or set(states) != set(codes) or set(industries) != set(codes):
        raise ValueError("financial, industry and parent-cohort identities must agree")
    window, amount_window = config["return_lookback_sessions"], config["activity_lookback_sessions"]
    momentum, lag = config["market_momentum_sessions"], config["market_lag_sessions"]
    if (any(type(n) is not int or n < 2 for n in (window, amount_window, momentum)) or momentum > window):
        raise ValueError("invalid fixed feature windows")
    rows = []
    for day in split["dates"]:
        index = calendar.index(day)
        cutoff_index = index - lag
        if cutoff_index < max(window - 1, amount_window):
            raise ValueError("insufficient exchange sessions before the signal cutoff")
        cutoff_day = calendar[cutoff_index]
        cutoff_at = cutoff_day + "T" + config["signal_cutoff_time"]
        moment = aware(cutoff_at)
        partition = ("train" if day in split["training_dates"] else
                     "purged" if day in split["purged_dates"] else "evaluation")
        for code in codes:
            state, industry = states[code], industries[code]
            if (state["stock_code"] != code or industry["stock_code"] != code or
                    state["status"] != "INDEPENDENT_EXTRACTION_AGREEMENT" or
                    state["industry_code"] != industry["industry_code"] or
                    industry["status"] != "verified_table_label" or
                    state["agent_signal_enabled"] is not False):
                raise ValueError("financial or historical industry record differs")
            row = {"stock_code": code, "trade_date": day, "partition": partition,
                   "signal_cutoff_date": cutoff_day, "signal_cutoff_at": cutoff_at,
                   "execution_reference_date": calendar[index - 1],
                   "financial_available_at_proxy": state["available_at_proxy"],
                   "industry_available_at_proxy": industry["available_at_proxy"],
                   "industry_code": industry["industry_code"], "industry_category": industry["category_code"],
                   "paired_exclusion_reason": None, "features": None,
                   "financial_decimal_inputs": None, "target_absolute_return": None}
            reason = None
            if not state["generic_nonfinancial_ratios_applicable"]:
                reason = "FINANCIAL_INSTITUTION_OR_UNVERIFIED_SCOPE"
            elif aware(state["available_at_proxy"]) > moment:
                reason = "FINANCIAL_SOURCE_NOT_YET_AVAILABLE"
            elif aware(industry["available_at_proxy"]) > moment:
                reason = "INDUSTRY_SOURCE_NOT_YET_AVAILABLE"
            elif (state["amounts"]["assets"]["status"] != "NUMERIC" or
                  any(state["ratios"][name]["status"] != "OBSERVED_RATIO"
                      for name in FINANCIAL_FEATURES[1:])):
                reason = "MISSING_FIXED_FINANCIAL_SOURCE_VALUE"
            if reason:
                row["paired_exclusion_reason"] = reason
                rows.append(row)
                continue
            assets = Decimal(state["amounts"]["assets"]["value_yuan"])
            ratios = {name: Decimal(state["ratios"][name]["value"]) for name in FINANCIAL_FEATURES[1:]}
            if (not assets.is_finite() or assets <= 0 or
                    any(not v.is_finite() or v < 0 for v in ratios.values())):
                raise ValueError("fixed financial inputs must be finite with positive assets")
            history_days = calendar[cutoff_index - window + 1:cutoff_index + 1]
            prior_amount_days = calendar[cutoff_index - amount_window:cutoff_index]
            if any((code, d) not in market for d in [*history_days, day]) or any(
                    (code, d) not in amounts for d in [*prior_amount_days, cutoff_day]):
                raise ValueError("selected company lacks a complete exchange-session history")
            history = [market[(code, d)] for d in history_days]
            stock = [r[0] for r in history]
            benchmark = [r[1] for r in history]
            target = market[(code, day)][0]
            values = [amounts[(code, d)] for d in prior_amount_days]
            current = amounts[(code, cutoff_day)]
            if (any(not math.isfinite(v) or v <= -1 for v in [*stock, *benchmark, target]) or
                    any(not math.isfinite(v) or v < 0 for v in [*values, current])):
                raise ValueError("market feature inputs are nonfinite or invalid")
            benchmark_vol = statistics.stdev(benchmark)
            features = {
                "stock_volatility_20": statistics.stdev(stock),
                "benchmark_volatility_20": benchmark_vol,
                "absolute_benchmark_momentum_5": abs(math.tanh(
                    sum(benchmark[-momentum:]) / (max(1e-6, benchmark_vol) * math.sqrt(momentum)))),
                "log_amount_surprise_20": math.log1p(current) - statistics.median(math.log1p(v) for v in values),
                "log_reported_assets_yuan": math.log(float(assets)),
                **{name: float(value) for name, value in ratios.items()},
            }
            if any(not math.isfinite(value) for value in features.values()):
                raise ValueError("feature conversion is nonfinite")
            row.update(features=features, target_absolute_return=abs(target),
                       financial_decimal_inputs={"assets_value_yuan": str(assets),
                                                 **{name: str(value) for name, value in ratios.items()}})
            rows.append(row)
    return rows, split


def solve(matrix: list[list[float]], vector: list[float]) -> tuple[list[float], float]:
    """Small normal equations, no ridge to conceal a singular design."""
    n = len(vector)
    if not n or len(matrix) != n or any(len(row) != n for row in matrix):
        raise ValueError("linear system dimensions differ")
    rows = [list(matrix[i]) + [vector[i]] for i in range(n)]
    threshold = max(abs(v) for row in matrix for v in row) * 1e-12
    pivots = []
    for col in range(n):
        pivot = max(range(col, n), key=lambda i: abs(rows[i][col]))
        value = abs(rows[pivot][col])
        if not math.isfinite(value) or value <= threshold:
            raise ValueError("rank-deficient unpenalized training design")
        pivots.append(value)
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        rows[col] = [v / scale for v in rows[col]]
        for i in range(n):
            if i != col:
                factor = rows[i][col]
                rows[i] = [a - factor * b for a, b in zip(rows[i], rows[col], strict=True)]
    answer = [row[-1] for row in rows]
    if any(not math.isfinite(v) for v in answer):
        raise ValueError("least-squares coefficients are nonfinite")
    return answer, min(pivots)


def fit_linear(train: list[dict], features: list[str], *, intercept: bool = True) -> dict:
    if len(train) < 2 or not features or len(set(features)) != len(features):
        raise ValueError("linear fit needs training rows and unique features")
    means = {name: statistics.fmean(r["features"][name] for r in train) if intercept else 0.0 for name in features}
    scales = {name: statistics.stdev(r["features"][name] for r in train) for name in features}
    if any(not math.isfinite(v) or v <= 1e-12 for v in scales.values()):
        raise ValueError("training feature lacks finite variation")
    width = len(features) + int(intercept)
    gram, rhs = [[0.0] * width for _ in range(width)], [0.0] * width
    for row in train:
        x = ([1.0] if intercept else []) + [(row["features"][f] - means[f]) / scales[f] for f in features]
        y = row["target_absolute_return"]
        if not math.isfinite(y) or any(not math.isfinite(v) for v in x):
            raise ValueError("training design or target is nonfinite")
        for i, left in enumerate(x):
            rhs[i] += left * y
            for j, right in enumerate(x):
                gram[i][j] += left * right
    coefficients, pivot = solve(gram, rhs)
    return {"features": features, "intercept": intercept, "training_rows": len(train),
            "training_means": means, "training_sample_scales": scales,
            "standardized_coefficients": coefficients, "minimum_normal_equation_pivot": pivot,
            "regularization": "none", "full_rank_solver_completed": True}


def predict(fit: dict, rows: list[dict]) -> list[float]:
    results = []
    for row in rows:
        x = ([1.0] if fit["intercept"] else []) + [
            (row["features"][f] - fit["training_means"][f]) / fit["training_sample_scales"][f]
            for f in fit["features"]]
        value = sum(a * b for a, b in zip(x, fit["standardized_coefficients"], strict=True))
        if not math.isfinite(value):
            raise ValueError("prediction is nonfinite")
        results.append(value)
    return results


def metrics(rows: list[dict], predicted: list[float]) -> dict:
    if not rows or len(rows) != len(predicted) or any(not math.isfinite(v) for v in predicted):
        raise ValueError("metrics require nonempty finite paired predictions")
    grouped = defaultdict(list)
    for row, p in zip(rows, predicted, strict=True):
        grouped[row["trade_date"]].append((row["target_absolute_return"], p))
    daily = []
    for day, pairs in sorted(grouped.items()):
        errors = [p - y for y, p in pairs]
        daily.append({"trade_date": day, "company_rows": len(pairs),
                      "mae": statistics.fmean(abs(v) for v in errors),
                      "mse": statistics.fmean(v * v for v in errors),
                      "mean_target": statistics.fmean(y for y, _ in pairs),
                      "mean_prediction": statistics.fmean(p for _, p in pairs)})
    all_errors = [p - row["target_absolute_return"] for row, p in zip(rows, predicted, strict=True)]
    daily_errors = [r["mean_prediction"] - r["mean_target"] for r in daily]
    return {"rows": len(rows), "sessions": len(daily),
            "pooled_mae": statistics.fmean(abs(v) for v in all_errors),
            "pooled_rmse": math.sqrt(statistics.fmean(v * v for v in all_errors)),
            "equal_session_mae": statistics.fmean(r["mae"] for r in daily),
            "daily_mean_target_mae": statistics.fmean(abs(v) for v in daily_errors),
            "daily_mean_target_rmse": math.sqrt(statistics.fmean(v * v for v in daily_errors)),
            "negative_prediction_count": sum(p < 0 for p in predicted), "daily": daily}


def comparison(base: dict, extra: dict) -> dict:
    left, right = base["metrics"], extra["metrics"]
    if [(r["trade_date"], r["company_rows"]) for r in left["daily"]] != [
            (r["trade_date"], r["company_rows"]) for r in right["daily"]]:
        raise ValueError("incremental models use different evaluation samples")
    keys = ["pooled_mae", "pooled_rmse", "equal_session_mae", "daily_mean_target_mae", "daily_mean_target_rmse"]
    improvements = {key: 1 - right[key] / left[key] if left[key] else None for key in keys}
    deletions = []
    for day in [r["trade_date"] for r in left["daily"]]:
        a = [r for r in left["daily"] if r["trade_date"] != day]
        b = [r for r in right["daily"] if r["trade_date"] != day]
        n = sum(r["company_rows"] for r in a)
        base_mae = sum(r["company_rows"] * r["mae"] for r in a) / n
        plus_mae = sum(r["company_rows"] * r["mae"] for r in b) / n
        base_mse = sum(r["company_rows"] * r["mse"] for r in a) / n
        plus_mse = sum(r["company_rows"] * r["mse"] for r in b) / n
        deletions.append({"deleted_trade_date": day, "pooled_mae_improvement": 1 - plus_mae / base_mae,
                          "pooled_rmse_improvement": 1 - math.sqrt(plus_mse / base_mse)})
    return {"relative_error_improvement": improvements,
            "evaluation_dates_with_lower_mae": sum(b["mae"] < a["mae"] for a, b in zip(left["daily"], right["daily"], strict=True)),
            "delete_one_evaluation_date": deletions,
            "delete_one_date_improvement_range": {key: [min(r[key] for r in deletions), max(r[key] for r in deletions)]
                                                 for key in ("pooled_mae_improvement", "pooled_rmse_improvement")},
            "sensitivity_is_not_a_confidence_interval": True}


def fit_models(panel: list[dict], split: dict) -> dict:
    paired = [r for r in panel if r["paired_exclusion_reason"] is None]
    train, check = ([r for r in paired if r["partition"] == part] for part in ("train", "evaluation"))
    if not train or not check or any(aware(r["trade_date"] + "T15:00:00+08:00") > aware(
            split["model_fit_cutoff_at"]) for r in train):
        raise ValueError("fit uses outcomes later than the first evaluation information cutoff")
    if len({(r["stock_code"], r["trade_date"]) for r in panel}) != len(panel):
        raise ValueError("panel has duplicate company-date identities")
    if (sorted({r["trade_date"] for r in train}) != split["training_dates"] or
            sorted({r["trade_date"] for r in check}) != split["evaluation_dates"]):
        raise ValueError("paired panel does not cover the frozen date split")
    categories = sorted({r["industry_category"] for r in train})
    if any(r["industry_category"] not in categories for r in check):
        raise ValueError("evaluation category was unseen in training")
    indicators = ["industry_category_" + category for category in categories[1:]]
    def decorate(rows):
        return [{**r, "features": {**r["features"], **{name: float(r["industry_category"] == name[-1]) for name in indicators}}}
                for r in rows]
    a, b = decorate(train), decorate(check)
    models, predictions = {}, {}
    for name, features in (
            ("market", MARKET_FEATURES), ("market_financial", [*MARKET_FEATURES, *FINANCIAL_FEATURES]),
            ("market_industry", [*MARKET_FEATURES, *indicators]),
            ("market_industry_financial", [*MARKET_FEATURES, *indicators, *FINANCIAL_FEATURES])):
        fit = fit_linear(a, features)
        pred = predict(fit, b)
        predictions[name] = pred
        models[name] = {"fit": fit, "metrics": metrics(check, pred)}
    overall_mean = statistics.fmean(r["target_absolute_return"] for r in train)
    by_code = defaultdict(list)
    for row in train:
        by_code[row["stock_code"]].append(row)
    if set(r["stock_code"] for r in check) - set(by_code):
        raise ValueError("a company reference has no pre-cutoff training outcomes")
    company_means = {code: {"target_mean": statistics.fmean(r["target_absolute_return"] for r in group),
                            "training_rows": len(group),
                            "feature_means": {f: statistics.fmean(r["features"][f] for r in group) for f in MARKET_FEATURES}}
                     for code, group in sorted(by_code.items())}
    for name, pred, fit in (
            ("training_mean", [overall_mean] * len(check), {"training_target_mean": overall_mean}),
            ("company_training_mean", [company_means[r["stock_code"]]["target_mean"] for r in check], {"companies": company_means})):
        predictions[name] = pred
        models[name] = {"fit": fit, "metrics": metrics(check, pred)}
    def demean(rows, targets):
        return [{**r, "features": {f: r["features"][f] - company_means[r["stock_code"]]["feature_means"][f] for f in MARKET_FEATURES},
                 "target_absolute_return": r["target_absolute_return"] - company_means[r["stock_code"]]["target_mean"] if targets else r["target_absolute_return"]}
                for r in rows]
    within = fit_linear(demean(train, True), MARKET_FEATURES, intercept=False)
    deviation = predict(within, demean(check, False))
    pred = [p + company_means[r["stock_code"]]["target_mean"] for r, p in zip(check, deviation, strict=True)]
    predictions["company_fixed_market"] = pred
    models["company_fixed_market"] = {"fit": {"within": within, "companies": company_means,
                                                "static_financial_block_absorbed_by_company_effects": True},
                                      "metrics": metrics(check, pred)}
    forecasts = [{"stock_code": r["stock_code"], "trade_date": r["trade_date"],
                  "target_absolute_return": r["target_absolute_return"],
                  "predictions": {name: values[i] for name, values in predictions.items()}}
                 for i, r in enumerate(check)]
    return {"split": split, "training_rows": len(train), "evaluation_rows": len(check),
            "training_companies": len(by_code), "industry_reference_category": categories[0],
            "industry_control_categories": categories, "company_training_row_counts": {code: len(group) for code, group in sorted(by_code.items())},
            "models": models, "evaluation_predictions": forecasts,
            "comparisons": {"primary_industry_controlled": comparison(models["market_industry"], models["market_industry_financial"]),
                            "secondary_market_only": comparison(models["market"], models["market_financial"])},
            "excluded_slot_counts": dict(Counter(r["paired_exclusion_reason"] for r in panel if r["paired_exclusion_reason"])),
            "agent_signal_enabled": False}
