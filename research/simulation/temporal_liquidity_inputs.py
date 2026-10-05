"""Actual dated lagged liquidity observations and an explicit capacity sensitivity."""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import statistics

VERSION = "temporal-lagged-liquidity-capacity-candidate-2021-v1"
FIELDS = ("f56", "f57", "f61")


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def packed(v):
    return [v.numerator, v.denominator]


def capacity_lots(turnover_mean, reference, base_lots=4):
    """One declared turnover-to-capacity hypothesis; not empirical investor training."""
    require(reference > 0 and turnover_mean >= 0 and type(base_lots) is int and base_lots > 0,
            "invalid capacity inputs")
    ratio = turnover_mean / reference
    clipped = max(Fraction(1, 2), min(Fraction(2), ratio))
    scaled = clipped * base_lots
    rounded = (2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)
    return rounded, ratio, clipped


def compile_liquidity(pairs, stocks, dates, calendar, window=20, base_lots=4):
    """Retain original strings/dates; missing days never become zero observations."""
    require(stocks == sorted(set(stocks)) and dates == sorted(set(dates))
            and calendar == sorted(set(calendar)) and set(dates) <= set(calendar)
            and type(window) is int and window >= 2, "invalid liquidity scope")
    index = {d: i for i, d in enumerate(calendar)}
    require(all(index[d] >= window + 1 for d in dates), "insufficient preperiod liquidity history")
    lookup = {(r["secid"].split(".")[1], r["trade_date"]): r["unadjusted_source"] for r in pairs}
    require(len(lookup) == len(pairs) == len(stocks) * len(calendar)
            and set(lookup) == {(s, d) for s in stocks for d in calendar}, "full raw liquidity scope required")
    observations = {}
    for stock in stocks:
        observations[stock] = []
        for date in calendar:
            raw = lookup[stock, date]
            require(raw["secid"].split(".")[1] == stock and raw["trade_date"] == date and raw["fqt"] == 0,
                    "liquidity source identity differs")
            if raw["raw_line"] is None:
                require(all(raw["provider_fields"][k] is None for k in FIELDS), "absent liquidity source was filled")
                continue
            fields = raw["provider_fields"]
            require(all(type(fields[k]) is str and Fraction(fields[k]) >= 0 for k in FIELDS), "invalid liquidity strings")
            require(raw["raw_line"] == ",".join(fields[f"f{i}"] for i in range(51, 62)), "liquidity raw line changed")
            observations[stock].append({"trade_date": date, "source_volume_raw": fields["f56"],
                "source_amount_raw": fields["f57"], "source_turnover_percent_raw": fields["f61"]})
    reference_cutoff = calendar[index[dates[0]] - 2]
    reference_windows = {s: [r for r in observations[s] if r["trade_date"] <= reference_cutoff][-window:] for s in stocks}
    require(all(len(v) == window for v in reference_windows.values()), "complete initial actual liquidity history required")
    reference_means = {s: sum(Fraction(r["source_turnover_percent_raw"]) / 100 for r in reference_windows[s]) / window for s in stocks}
    reference = statistics.median(reference_means.values())
    require(reference > 0, "undefined preperiod cohort liquidity reference")
    by_stock = {}
    for stock in stocks:
        by_stock[stock] = []
        for date in dates:
            cutoff = calendar[index[date] - 2]
            history = [r for r in observations[stock] if r["trade_date"] <= cutoff][-window:]
            require(len(history) == window and all(r["trade_date"] <= cutoff for r in history), "incomplete/future liquidity window")
            volume = sum(Fraction(r["source_volume_raw"]) for r in history) / window
            amount = sum(Fraction(r["source_amount_raw"]) for r in history) / window
            turnover = sum(Fraction(r["source_turnover_percent_raw"]) / 100 for r in history) / window
            lots, ratio, clipped = capacity_lots(turnover, reference, base_lots)
            by_stock[stock].append({"stock_code": stock, "trade_date": date, "signal_cutoff_date": cutoff,
                "latest_history_date": history[-1]["trade_date"],
                "stale_calendar_sessions": index[cutoff] - index[history[-1]["trade_date"]],
                "history": history, "history_sha256": digest(history),
                "mean_source_volume_units_fraction": packed(volume), "mean_source_amount_units_fraction": packed(amount),
                "mean_turnover_fraction": packed(turnover), "turnover_reference_fraction": packed(reference),
                "capacity_ratio_fraction": packed(ratio), "clipped_capacity_ratio_fraction": packed(clipped),
                "base_max_order_lots": base_lots, "applied_max_order_lots": lots,
                "source_unknowns_filled": False, "point_in_time_feed_certified": False})
    return {"version": VERSION, "stocks": stocks, "dates": dates, "history_window": window,
        "reference_cutoff_date": reference_cutoff, "reference_mean_turnover_by_stock": {s: packed(v) for s, v in reference_means.items()},
        "reference_windows_sha256": digest(reference_windows), "turnover_reference_fraction": packed(reference),
        "parameters": {"base_max_order_lots": base_lots, "minimum_ratio_fraction": [1, 2],
            "maximum_ratio_fraction": [2, 1], "rounding": "nearest integer lots, half up",
            "capacity_driver": "20 actual dated raw turnover observations through t-2 divided by fixed preperiod cohort median"},
        "by_stock": by_stock, "missing_values_filled": False, "new_default_selected": False,
        "interpretation": "Current-vintage raw source with actual historical timestamps, not a certified point-in-time feed. Volume and amount remain original source units; capacity uses dimensionless reported turnover only. Only the per-background-owner maximum order lots changes; cash, shares, participant count, target range, quotes, risk, information and price rules remain frozen. A bounded mechanism hypothesis, not actual investor or market-capacity calibration."}


def validate_capacity_path(path, assets, calendar):
    require(path["version"] == VERSION and path["stocks"] == assets and path["dates"] == [d for d, _, _ in calendar]
            and set(path["by_stock"]) == set(assets), "liquidity path scope differs")
    base = path["parameters"]["base_max_order_lots"]
    reference = Fraction(*path["turnover_reference_fraction"])
    for stock in assets:
        require(len(path["by_stock"][stock]) == len(calendar), "liquidity path omitted sessions")
        for row, (day, cutoff, _) in zip(path["by_stock"][stock], calendar, strict=True):
            require((row["stock_code"], row["trade_date"], row["signal_cutoff_date"]) == (stock, day, cutoff)
                    and row["latest_history_date"] <= cutoff and row["source_unknowns_filled"] is False,
                    "liquidity clock, stock or missingness differs")
            history = row["history"]
            require(len(history) == path["history_window"] and [r["trade_date"] for r in history] == sorted({r["trade_date"] for r in history})
                    and all(r["trade_date"] <= cutoff for r in history) and history[-1]["trade_date"] == row["latest_history_date"]
                    and digest(history) == row["history_sha256"], "liquidity actual history differs")
            turnover = sum(Fraction(r["source_turnover_percent_raw"]) / 100 for r in history) / len(history)
            lots, ratio, clipped = capacity_lots(turnover, reference, base)
            require(row["mean_turnover_fraction"] == packed(turnover)
                    and row["turnover_reference_fraction"] == packed(reference)
                    and row["base_max_order_lots"] == base and type(row["applied_max_order_lots"]) is int
                    and row["applied_max_order_lots"] == lots and row["capacity_ratio_fraction"] == packed(ratio)
                    and row["clipped_capacity_ratio_fraction"] == packed(clipped), "liquidity capacity calculation differs")
    return path


def subset(path, assets):
    return {**path, "stocks": assets, "by_stock": {s: path["by_stock"][s] for s in assets},
        "reference_mean_turnover_by_stock": {s: path["reference_mean_turnover_by_stock"][s] for s in assets}}


def background_settings(background, row):
    require(type(row["applied_max_order_lots"]) is int and row["applied_max_order_lots"] > 0
            and row["base_max_order_lots"] == background["max_order_lots"], "background capacity base differs")
    return {**background, "max_order_lots": row["applied_max_order_lots"]}
