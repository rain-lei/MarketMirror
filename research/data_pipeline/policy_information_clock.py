"""Crossed, strictly prior-day policy/market information-clock contrasts."""

from __future__ import annotations

import math
import statistics
from datetime import date


def prior_clocks(days: list[str], first: str, last: str, lag: int) -> list[dict]:
    if type(lag) is not int or lag not in {1, 2}:
        raise ValueError("clock contrast only supports one or two prior exchange sessions")
    if not days or days != sorted(set(days)) or len(days) <= lag:
        raise ValueError("clock contrast exchange calendar is empty, duplicated or too short")
    for day in days:
        date.fromisoformat(day)
    if first > last or first < days[lag] or last > days[-1]:
        raise ValueError("clock contrast lacks the requested prior-session coverage")
    return [{"trade_date": day, "signal_cutoff_date": days[index - lag],
             "execution_reference_date": days[index - 1]}
            for index, day in enumerate(days) if first <= day <= last]


def clock_model_rows(days: list[str], closes: dict[str, float], calendar: list[dict], panel: list[dict], market_lag: int, policy_lag: int) -> list[dict]:
    if (type(market_lag) is not int or type(policy_lag) is not int
            or market_lag not in {1, 2} or policy_lag not in {1, 2}):
        raise ValueError("clock contrast forbids target-day market or policy inputs")
    if (set(closes) != set(days) or days != sorted(set(days))
            or any(not math.isfinite(value) or value <= 0 for value in closes.values())):
        raise ValueError("clock contrast prices require unique calendar and positive finite levels")
    if len(calendar) != len(panel):
        raise ValueError("clock contrast policy and market rows are unpaired")
    positions = {day: index for index, day in enumerate(days)}
    returns = {day: closes[day] / closes[days[index - 1]] - 1 for index, day in enumerate(days) if index}
    result = []
    for clock, policy in zip(calendar, panel):
        index = positions[clock["trade_date"]]
        if (index < max(market_lag, policy_lag) or clock["signal_cutoff_date"] != days[index - policy_lag]
                or clock["execution_reference_date"] != days[index - 1]
                or clock["cutoff_timestamp"] != days[index - policy_lag] + "T23:59:59+08:00"
                or policy["trade_date"] != clock["trade_date"]):
            raise ValueError("clock contrast policy cutoff/reference day is inconsistent")
        cutoff = index - market_lag
        reason = "insufficient_twenty_return_history" if cutoff < 20 else "unknown_new_policy_predecessor" if any(value is None for value in policy["change_bps_by_channel"]) else None
        features = None
        if cutoff >= 20:
            history = [returns[days[i]] for i in range(cutoff - 19, cutoff + 1)]
            features = [1.0, closes[days[cutoff]] / closes[days[cutoff - 5]] - 1,
                        statistics.stdev(history), sum(abs(value) for value in history) / 20]
        target = returns[clock["trade_date"]]
        result.append({"trade_date": clock["trade_date"], "signal_cutoff_date": clock["signal_cutoff_date"],
                       "policy_cutoff_timestamp": clock["cutoff_timestamp"], "market_feature_cutoff_date": days[cutoff],
                       "market_feature_cutoff_timestamp": days[cutoff] + "T23:59:59+08:00",
                       "execution_reference_date": clock["execution_reference_date"], "baseline_features": features,
                       "policy_features": policy["change_bps_by_channel"], "target_return": target,
                       "target_absolute_return": abs(target), "intrinsic_exclusion_reason": reason,
                       "paired_exclusion_reason": reason})
    return result


def shared_pairing(panels: dict[str, list[dict]]) -> list[dict]:
    """Apply the union of unavailable dates to every variant without target use."""
    if not panels or any(not rows for rows in panels.values()):
        raise ValueError("clock contrast pairing panels are empty")
    names = list(panels)
    first = panels[names[0]]
    dates = [row["trade_date"] for row in first]
    if dates != sorted(set(dates)):
        raise ValueError("clock contrast target dates are unordered or duplicated")
    for rows in panels.values():
        if [row["trade_date"] for row in rows] != dates:
            raise ValueError("clock contrast variants do not have the same dates")
        if any((row["target_return"], row["target_absolute_return"]) != (ref["target_return"], ref["target_absolute_return"]) for row, ref in zip(rows, first)):
            raise ValueError("clock contrast variants do not have the same targets")
    exclusions = []
    for index, day in enumerate(dates):
        reasons = {name: rows[index]["intrinsic_exclusion_reason"] for name, rows in panels.items() if rows[index]["intrinsic_exclusion_reason"]}
        if reasons:
            exclusions.append({"trade_date": day, "unavailable_variants": reasons})
        for rows in panels.values():
            rows[index]["paired_exclusion_reason"] = "shared_clock_pairing_exclusion" if reasons else None
    return exclusions


def summarize_contrasts(fitted: dict[str, dict]) -> list[dict]:
    if set(fitted) != {"market2_policy2", "market2_policy1", "market1_policy2", "market1_policy1"}:
        raise ValueError("clock contrast requires all four prespecified variants")
    if not all(result["coefficients_fitted"] for result in fitted.values()):
        return []
    result = []
    pairs = [("policy", "market2_policy2", "market2_policy1"), ("policy", "market1_policy2", "market1_policy1"),
             ("market", "market2_policy2", "market1_policy2"), ("market", "market2_policy1", "market1_policy1")]
    for factor, earlier, newer in pairs:
        for target in ("target_return", "target_absolute_return"):
            for model in ("market_state", "market_state_plus_rates"):
                before = fitted[earlier]["targets"][target]["models"][model]["metrics"]
                after = fitted[newer]["targets"][target]["models"][model]["metrics"]
                result.append({"updated_factor": factor, "two_session_variant": earlier, "one_session_variant": newer,
                               "target": target, "model": model, "evaluation_rows": before["rows"],
                               "mae_relative_change_percent": (after["mae"] / before["mae"] - 1) * 100,
                               "rmse_relative_change_percent": (after["rmse"] / before["rmse"] - 1) * 100,
                               "direction_accuracy_difference": after["return_direction_accuracy"] - before["return_direction_accuracy"] if before["return_direction_accuracy"] is not None else None})
    return result
