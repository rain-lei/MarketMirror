"""Audit temporal inputs without inventing daily returns or opening a model gate."""
from __future__ import annotations

from collections import Counter
from datetime import date
from fractions import Fraction
import re

PRESENT = "SOURCE_ROW_PRESENT_QC_PENDING"
ABSENT = "SOURCE_ROW_ABSENT_NOT_CERTIFIED_SUSPENSION"
FAILED = "SOURCE_REQUEST_FAILED"
SUSPENDED = "SUSPENDED_FULL_SESSION"


def require(value, message):
    if not value:
        raise ValueError(message)


def iso(value):
    require(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        and date.fromisoformat(value).isoformat() == value, "noncanonical temporal date")
    return value


def packed(value):
    return None if value is None else [value.numerator, value.denominator]


def quoted(source, secid, day, fqt, kind):
    require(source["secid"] == secid and source["trade_date"] == day
        and type(source["fqt"]) is int and source["fqt"] == fqt and source["kind"] == kind,
        "temporal source identity or parameter differs")
    status = source["source_observation_status"]
    require(status in {PRESENT, ABSENT, FAILED}, "unknown temporal source status")
    fields = source["provider_fields"]
    require(set(fields) == {f"f{i}" for i in range(51, 62)}, "temporal quote field scope differs")
    if status != PRESENT:
        require(source["raw_line"] is None and all(v is None for v in fields.values()), "unknown source was filled")
        return None
    require(type(source["raw_line"]) is str and all(type(v) is str for v in fields.values()) and fields["f51"] == day
        and source["raw_line"] == ",".join(fields[f"f{i}"] for i in range(51, 62)), "temporal raw field strings changed")
    values = {k: Fraction(fields[k]) for k in ("f52", "f53", "f54", "f55", "f56", "f57")}
    require(min(values[k] for k in ("f52", "f53", "f54", "f55")) > 0
        and values["f55"] <= values["f52"] <= values["f54"]
        and values["f55"] <= values["f53"] <= values["f54"]
        and values["f56"] >= 0 and values["f57"] >= 0, "nonpositive or inconsistent temporal quote")
    return values


def moments(history):
    if not history or any(r["stock_return"] is None or r["market_return"] is None for r in history):
        return None
    n = len(history)
    require(n >= 2, "at least two past returns required")
    x = [r["market_return"] for r in history]
    y = [r["stock_return"] for r in history]
    mx, my = sum(x) / n, sum(y) / n
    vx = sum((v - mx) ** 2 for v in x) / (n - 1)
    vy = sum((v - my) ** 2 for v in y) / (n - 1)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (n - 1)
    return {"market_variance_fraction": packed(vx), "stock_variance_fraction": packed(vy),
        "covariance_fraction": packed(cov), "beta_fraction": packed(cov / vx) if vx > 0 else None,
        "benchmark_variance_positive": vx > 0}


def profile(history, day, cutoff, window, indices):
    require(all(r["trade_date"] <= cutoff < day for r in history), "future information entered a temporal profile")
    complete = len(history) == window and all(r["stock_return"] is not None and r["market_return"] is not None for r in history)
    values = moments(history) if complete else None
    last_slot = history[-1]["trade_date"] if history else None
    end = next((r["trade_date"] for r in reversed(history)
        if r["stock_return"] is not None and r["market_return"] is not None), None)
    return {"history": [{"trade_date": r["trade_date"], "stock_return_fraction": packed(r["stock_return"]),
            "market_return_fraction": packed(r["market_return"])} for r in history],
        "information_cutoff_date": cutoff, "complete_observed_history": complete,
        "history_last_slot_date": last_slot, "latest_history_date": end,
        "stale_calendar_sessions": indices[cutoff] - indices[end] if end else None,
        "moments": values, "public_factor_estimate_defined": complete and values["benchmark_variance_positive"]}


def assess_temporal_inputs(pairs, benchmark_rows, stock_specs, trade_dates, analysis_dates, history_window=20):
    """Compare complete calendar windows with last available observed daily windows.

    Both profiles use real adjacent-calendar quote ratios only. Source absences,
    resumed cumulative spans, failures and zeros never manufacture a daily value.
    The available-observation profile reports staleness and is not a new default.
    """
    require(type(history_window) is int and history_window >= 2, "invalid temporal history window")
    require(trade_dates == sorted(set(trade_dates)) and analysis_dates == sorted(set(analysis_dates))
        and trade_dates and analysis_dates, "temporal calendars must be complete explicit sorted lists")
    for day in trade_dates:
        iso(day)
    require(set(analysis_dates) <= set(trade_dates), "analysis escaped the full source calendar")
    require(all(type(s) is dict and set(s) == {"secid", "fqt", "kind"} and s["kind"] == "stock"
        and type(s["fqt"]) is int and s["fqt"] == 2 and type(s["secid"]) is str
        and re.fullmatch(r"[01]\.\d{6}", s["secid"]) for s in stock_specs),
        "temporal stock identity scope differs")
    stocks = [s["secid"] for s in stock_specs]
    require(stocks and len(stocks) == len(set(stocks)), "duplicate temporal stock identity")
    indices = {day: i for i, day in enumerate(trade_dates)}
    require(all(indices[day] >= history_window + 2 for day in analysis_dates), "insufficient warmup before t-2")
    expected = {(stock, day) for stock in stocks for day in trade_dates}
    lookup = {(r["secid"], r["trade_date"]): r for r in pairs}
    require(len(lookup) == len(pairs) == len(expected) and set(lookup) == expected,
        "paired temporal source omitted, duplicated or escaped the full grid")
    benchmark = {r["trade_date"]: r for r in benchmark_rows}
    require(len(benchmark) == len(benchmark_rows) == len(trade_dates) and set(benchmark) == set(trade_dates),
        "benchmark full calendar differs")
    index_quotes = {day: quoted(benchmark[day], "1.000300", day, 0, "benchmark") for day in trade_dates}
    market_returns = {}
    for i, day in enumerate(trade_dates):
        previous = index_quotes[trade_dates[i - 1]] if i else None
        current = index_quotes[day]
        market_returns[day] = current["f53"] / previous["f53"] - 1 if current and previous else None
    stock_returns, raw_quotes, source_counts = {}, {}, Counter()
    for stock in stocks:
        adjusted = {}
        for day in trade_dates:
            row = lookup[stock, day]
            raw = row["unadjusted_source"]
            hfq = row["back_adjusted_source"]
            raw_quotes[stock, day] = quoted(raw, stock, day, 0, "stock")
            adjusted[day] = quoted(hfq, stock, day, 2, "stock")
            source_counts[hfq["source_observation_status"]] += 1
            documented = raw.get("documented_trade_status")
            require(documented in {None, SUSPENDED}, "unexpected temporal documented trade status")
            require(not (documented == SUSPENDED and raw_quotes[stock, day] is not None), "quote contradicts full-day suspension")
            require(not (documented == SUSPENDED and adjusted[day] is not None), "adjusted quote contradicts full-day suspension")
            require(not (documented == SUSPENDED and raw["source_observation_status"] == FAILED),
                "request failure cannot be explained as a source-absent suspension")
        for i, day in enumerate(trade_dates):
            previous = adjusted[trade_dates[i - 1]] if i else None
            current = adjusted[day]
            stock_returns[stock, day] = current["f53"] / previous["f53"] - 1 if current and previous else None
    rows, totals = [], Counter()
    for stock in stocks:
        for day in analysis_dates:
            i = indices[day]
            cutoff, reference = trade_dates[i - 2], trade_dates[i - 1]
            strict_days = trade_dates[i - 1 - history_window:i - 1]
            valid_past_days = [d for d in trade_dates[:i - 1] if stock_returns[stock, d] is not None]
            available_days = valid_past_days[-history_window:]

            def history(days):
                return [{"trade_date": d, "stock_return": stock_returns[stock, d],
                    "market_return": market_returns[d]} for d in days]

            strict = profile(history(strict_days), day, cutoff, history_window, indices)
            available = profile(history(available_days), day, cutoff, history_window, indices)
            source = lookup[stock, day]["unadjusted_source"]
            q = raw_quotes[stock, day]
            if q is not None and q["f56"] > 0:
                execution, status_basis = True, "SOURCE_QUOTE_WITH_POSITIVE_VOLUME_RETROSPECTIVE"
            elif q is None and source.get("documented_trade_status") == SUSPENDED:
                execution, status_basis = False, "RETROSPECTIVE_DOCUMENTED_FULL_SESSION_SUSPENSION"
            else:
                execution, status_basis = None, "TRADE_STATUS_UNKNOWN"
            reference_quote = raw_quotes[stock, reference]
            target = stock_returns[stock, day]
            row = {"secid": stock, "trade_date": day, "signal_cutoff_date": cutoff,
                "execution_reference_date": reference,
                "strict_calendar_profile": strict, "available_observation_profile": available,
                "raw_reference_close": lookup[stock, reference]["unadjusted_source"]["provider_fields"]["f53"]
                    if reference_quote is not None else None,
                "execution_available_for_conditional_replay": execution, "execution_status_basis": status_basis,
                "observed_adjacent_vendor_return_fraction": packed(target),
                "is_observed_adjacent_daily_return": target is not None,
                "strict_history_unknown_dates": [d for d in strict_days if stock_returns[stock, d] is None or market_returns[d] is None],
                "latest_history_is_decision_cutoff": available["latest_history_date"] == cutoff,
                "source_values_filled": False, "daily_gap_return_imputed": False,
                "point_in_time_feed_certified": False, "model_eligible": False}
            rows.append(row)
            totals["company_analysis_dates"] += 1
            totals["strict_calendar_history_complete" if strict["complete_observed_history"] else "strict_calendar_history_incomplete"] += 1
            totals["available_observation_history_complete" if available["complete_observed_history"] else "available_observation_history_incomplete"] += 1
            totals["target_adjacent_ratio_known" if target is not None else "target_adjacent_ratio_unknown"] += 1
            totals["reference_raw_quote_known" if reference_quote is not None else "reference_raw_quote_unknown"] += 1
            totals["retrospective_execution_open" if execution is True else "retrospective_execution_suspended" if execution is False else "retrospective_execution_unknown"] += 1
            totals["available_observation_stale"] += available["stale_calendar_sessions"] > 0 if available["stale_calendar_sessions"] is not None else 0
            totals["strict_public_factor_defined"] += strict["public_factor_estimate_defined"]
            totals["available_public_factor_defined"] += available["public_factor_estimate_defined"]
    return {"version": "temporal-source-clock-and-observed-history-preflight-v1",
        "stock_specs": stock_specs, "trade_dates": trade_dates, "analysis_dates": analysis_dates,
        "history_window": history_window, "by_position": rows, "counts": dict(totals),
        "source_status_counts": dict(source_counts), "new_period_model_effects_evaluated": False,
        "available_observation_profile_adopted_as_default": False, "missing_values_filled": False,
        "model_eligible": False,
        "interpretation": "Full cohort preflight. Distinguish fixed calendar and real available daily observations; record stale histories, null returns and historical replay conditions. This is not a model effect, blind forecast or a gate release."}
