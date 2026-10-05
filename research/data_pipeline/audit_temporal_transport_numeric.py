"""Rebuild numeric input contracts from full raw-source strings independently.

No imports of the numeric producer, compile_positions or source_windows. Exact
source fractions precede the same explicit float conversion as the frozen
legacy model. Targets and retrospective venue state remain separate.
"""
from collections import Counter
from fractions import Fraction
import hashlib
from itertools import combinations
import json
import math
import statistics

from .audit_eastmoney_carrier import require
from .temporal_input_contract import quoted


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def packed(value):
    return [value.numerator, value.denominator]


def correlation(left, right):
    if len(left) < 2:
        return None
    ml, mr = statistics.fmean(left), statistics.fmean(right)
    dl, dr = [value - ml for value in left], [value - mr for value in right]
    denominator = math.sqrt(sum(value * value for value in dl) * sum(value * value for value in dr))
    return sum(a * b for a, b in zip(dl, dr, strict=True)) / denominator if denominator else None


def audit_numeric_contract(source_rows, benchmark_rows, stocks, calendar, dates, inputs, windows, mask, window=20):
    expected_sources = {(stock, fqt, day) for stock in stocks for fqt in (0, 1, 2) for day in calendar}
    lookup = {(row["secid"][2:], row["fqt"], row["trade_date"]): row for row in source_rows}
    require(len(source_rows) == len(lookup) == len(expected_sources) and set(lookup) == expected_sources,
        "Independent numeric source grid omitted, duplicated or escaped scope")
    indices = {day: i for i, day in enumerate(calendar)}
    benchmark = {row["trade_date"]: row for row in benchmark_rows}
    require(len(benchmark) == len(benchmark_rows) == len(calendar) and set(benchmark) == set(calendar),
        "Independent benchmark source grid differs")
    market_close = {day: quoted(benchmark[day], "1.000300", day, 0, "benchmark") for day in calendar}
    market_returns = {day: market_close[day]["f53"] / market_close[calendar[i - 1]]["f53"] - 1
        if i and market_close[day] is not None and market_close[calendar[i - 1]] is not None else None
        for i, day in enumerate(calendar)}
    stock_returns, raw_quotes, expected_features = {}, {}, {}
    for stock in stocks:
        secid = ("1." if stock.startswith(("5", "6", "9")) else "0.") + stock
        hfq = {day: quoted(lookup[stock, 2, day], secid, day, 2, "stock") for day in calendar}
        for i, day in enumerate(calendar):
            previous = hfq[calendar[i - 1]] if i else None
            stock_returns[stock, day] = hfq[day]["f53"] / previous["f53"] - 1 if hfq[day] and previous else None
            raw_quotes[stock, day] = quoted(lookup[stock, 0, day], secid, day, 0, "stock")
    require(set(inputs) == {"risk", "features", "joined"}
        and all(set(value) == set(stocks) for value in inputs.values()), "Numeric input channel/stock scope changed")
    checked_histories, targets = 0, {}
    for stock in stocks:
        require(all([row["trade_date"] for row in inputs[channel][stock]] == dates for channel in inputs),
            "Numeric channel omitted or changed evaluation dates")
        expected_features[stock] = []
        for i, day in enumerate(dates):
            position = indices[day]
            cutoff, reference = calendar[position - 2], calendar[position - 1]
            observations = [past for past in calendar[:position - 1] if stock_returns[stock, past] is not None][-window:]
            require(len(observations) == window and all(market_returns[past] is not None for past in observations),
                "No complete genuine independent past observation window")
            history = [{"trade_date": past, "stock_return": float(stock_returns[stock, past]),
                "market_return": float(market_returns[past]), "benchmark_id": "sh.000300"} for past in observations]
            risk_history = [{key: row[key] for key in ("trade_date", "stock_return")} for row in history]
            risk = {"trade_date": day, "signal_cutoff_date": cutoff, "history": risk_history,
                "history_sha256": digest(risk_history), "lagged_stock_volatility": statistics.stdev(row["stock_return"] for row in risk_history)}
            xs = [Fraction(str(row["market_return"])) for row in history]
            ys = [Fraction(str(row["stock_return"])) for row in history]
            mx, my = sum(xs) / window, sum(ys) / window
            vx, vy = sum((value - mx) ** 2 for value in xs) / (window - 1), sum((value - my) ** 2 for value in ys) / (window - 1)
            covariance = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (window - 1)
            require(vx > 0, "Undefined independent market beta")
            feature = {"trade_date": day, "signal_cutoff_date": cutoff, "history": history,
                "history_sha256": digest(history), "beta_fraction": packed(covariance / vx),
                "market_variance_fraction": packed(vx), "stock_variance_fraction": packed(vy),
                "covariance_fraction": packed(covariance), "market_volatility": math.sqrt(float(vx)),
                "source_stock_volatility": math.sqrt(float(vy))}
            require(inputs["risk"][stock][i] == risk and inputs["features"][stock][i] == feature,
                "Past numeric risk/factor differs from independently rebuilt raw source window")
            target = stock_returns[stock, day]
            raw = raw_quotes[stock, day]
            documented = lookup[stock, 0, day].get("documented_trade_status")
            execution = True if raw is not None and raw["f56"] > 0 else False if documented == "SUSPENDED_FULL_SESSION" else None
            step = {"trade_date": day, "signal_cutoff_date": cutoff, "execution_reference_date": reference,
                "observed_return": float(target) if target is not None else None,
                "execution_available": execution, "text_signal": 0.0, "text_uncertainty": 0.0, "text_evidence": "text:disabled"}
            require(type(execution) is bool and inputs["joined"][stock][i] == step,
                "Target/venue/text channel differs from source-defined separate fields")
            expected_features[stock].append(feature)
            targets[stock, day] = step["observed_return"]
            checked_histories += 1
    expected_windows = []
    for i, day in enumerate(dates):
        common = sorted(set.intersection(*(set(row["trade_date"] for row in expected_features[stock][i]["history"]) for stock in stocks)))
        require(len(common) >= 2, "Independent residual source has fewer than two common past dates")
        ranks = {}
        for stock in stocks:
            factor = expected_features[stock][i]
            history, beta = factor["history"], Fraction(*factor["beta_fraction"])
            xs = [Fraction(str(row["market_return"])) for row in history]
            ys = [Fraction(str(row["stock_return"])) for row in history]
            alpha = sum(ys) / window - beta * sum(xs) / window
            residuals = {row["trade_date"]: y - alpha - beta * x for row, x, y in zip(history, xs, ys, strict=True)}
            values = [residuals[past] for past in common]
            ranks[stock] = [[sum(other < value for other in values), sum(other == value for other in values)] for value in values]
        expected_windows.append({"trade_date": day, "signal_cutoff_date": calendar[indices[day] - 2],
            "common_source_dates": common, "shared_source_date_count": len(common), "rank_intervals_by_stock": ranks})
    require(windows == expected_windows, "Sealed residual rank windows differ from independent raw-source reconstruction")
    known = {stock: [day for day in dates if targets[stock, day] is not None] for stock in stocks}
    unknown = [[stock, day] for stock in stocks for day in dates if targets[stock, day] is None]
    common = [day for day in dates if all(targets[stock, day] is not None for stock in stocks)]
    pairs = []
    for left, right in combinations(stocks, 2):
        ds = [day for day in dates if targets[left, day] is not None and targets[right, day] is not None]
        value = correlation([targets[left, day] for day in ds], [targets[right, day] for day in ds])
        pairs.append({"left": left, "right": right, "known_dates": ds,
            "observed_correlation": value, "primary_eligible": value is not None})
    require(mask["stocks"] == stocks and mask["dates"] == dates and mask["unknown_positions"] == unknown
        and mask["known_dates_by_stock"] == known and mask["correlation_pairs"] == pairs
        and mask["targets_by_stock"] == {stock: [targets[stock, day] for day in dates] for stock in stocks}
        and mask["full_cohort_portfolio_common_dates"] == common and mask["model_paths_require_full_grid"] is True
        and mask["missing_targets_filled"] is False, "Sealed evaluation mask differs from all independent source targets")
    return {"status": "PASS_INDEPENDENT_FULL_RAW_SOURCE_NUMERIC_CLOCK_RANK_AND_MASK_REBUILD",
        "company_evaluation_histories_checked": checked_histories, "actual_past_return_observations_checked": checked_histories * window,
        "residual_windows_checked": len(expected_windows), "company_rank_windows_checked": len(stocks) * len(dates),
        "shared_source_date_count_distribution": dict(sorted(Counter(row["shared_source_date_count"] for row in windows).items())),
        "evaluation_correlation_pairs_checked": len(pairs), "unknown_targets": len(unknown),
        "full_cohort_common_evaluation_dates": len(common), "new_period_model_effects_evaluated": False,
        "source_values_filled": False, "model_eligible": False}
