"""Explicit available-observation clock; never move or fabricate source dates.

Risk arithmetic follows the frozen 2019 functions, including their declared
float conversion. Only the observation clock differs: the last real return
may precede t-2. Full source fractions and staleness remain in the input ledger.
"""
from __future__ import annotations

from datetime import date
from fractions import Fraction
import math
import statistics

from .agents import finite_range
from .issuer_valuation import history_hash
from .public_market_factor import digest, packed


def iso(value):
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError("temporal date must be canonical ISO")
    return value


def validate_risk_row(row, day, cutoff, window):
    iso(day)
    iso(cutoff)
    if row["trade_date"] != day or row["signal_cutoff_date"] != cutoff or not cutoff < day:
        raise ValueError("temporal risk differs from decision clock")
    history = row["history"]
    if type(window) is not int or not 2 <= window <= 252 or not isinstance(history, list) or len(history) != window:
        raise ValueError("complete actual observation window required")
    dates, returns = [], []
    for item in history:
        if not isinstance(item, dict) or set(item) != {"trade_date", "stock_return"}:
            raise ValueError("risk history allows actual date and return only")
        dates.append(iso(item["trade_date"]))
        if dates[-1] > cutoff:
            raise ValueError("future return entered risk history")
        finite_range(item["stock_return"], -1, 10, "actual lagged stock return")
        returns.append(item["stock_return"])
    if dates != sorted(set(dates)):
        raise ValueError("actual observation dates must be unique and ordered")
    if row["history_sha256"] != history_hash(history):
        raise ValueError("actual history hash differs")
    if type(row["lagged_stock_volatility"]) not in (int, float) or row["lagged_stock_volatility"] != statistics.stdev(returns):
        raise ValueError("risk differs from actual observations")


def feature(history, day, cutoff, window, benchmark):
    iso(day)
    iso(cutoff)
    if type(window) is not int or not 2 <= window <= 252 or not cutoff < day or not isinstance(history, list) or len(history) != window:
        raise ValueError("complete lagged factor observation window required")
    if benchmark != "sh.000300":
        raise ValueError("explicit benchmark identity required")
    dates, x, y = [], [], []
    for row in history:
        if (not isinstance(row, dict) or set(row) != {"trade_date", "stock_return", "market_return", "benchmark_id"}
            or row["benchmark_id"] != benchmark or iso(row["trade_date"]) > cutoff
            or any(type(row[k]) not in (int, float) or not math.isfinite(row[k]) or not -1 <= row[k] <= 10
                   for k in ("stock_return", "market_return"))):
            raise ValueError("invalid or future factor observation")
        dates.append(row["trade_date"])
        x.append(Fraction(str(row["market_return"])))
        y.append(Fraction(str(row["stock_return"])))
    if dates != sorted(set(dates)):
        raise ValueError("factor observation dates must be unique and ordered")
    mx, my = sum(x) / window, sum(y) / window
    vx = sum((v - mx) ** 2 for v in x) / (window - 1)
    vy = sum((v - my) ** 2 for v in y) / (window - 1)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (window - 1)
    if vx <= 0:
        raise ValueError("undefined beta; no zero substitution")
    return {"trade_date": day, "signal_cutoff_date": cutoff, "history": history,
        "history_sha256": digest(history), "beta_fraction": packed(cov / vx),
        "market_variance_fraction": packed(vx), "stock_variance_fraction": packed(vy),
        "covariance_fraction": packed(cov), "market_volatility": math.sqrt(float(vx)),
        "source_stock_volatility": math.sqrt(float(vy))}


def compile_positions(positions, stocks, dates, window=20):
    """Whitelist past fields for messages; target and raw price remain separate."""
    lookup = {(r["secid"].split(".")[1], r["trade_date"]): r for r in positions}
    expected = {(s, d) for s in stocks for d in dates}
    if len(positions) != len(lookup) or set(lookup) != expected:
        raise ValueError("temporal input must retain entire declared stock-date grid")
    risk, factors, joined = {}, {}, {}
    for stock in stocks:
        risk[stock], factors[stock], joined[stock] = [], [], []
        for day in dates:
            row = lookup[stock, day]
            cutoff, reference = row["signal_cutoff_date"], row["execution_reference_date"]
            profile = row["available_observation_profile"]
            if (not profile["complete_observed_history"] or not profile["public_factor_estimate_defined"]
                or profile["information_cutoff_date"] != cutoff or profile["latest_history_date"] > cutoff
                or row["source_values_filled"] is not False or row["daily_gap_return_imputed"] is not False):
                raise ValueError("unusable or filled temporal candidate profile")
            actual = [{"trade_date": h["trade_date"],
                "stock_return": float(Fraction(*h["stock_return_fraction"])),
                "market_return": float(Fraction(*h["market_return_fraction"])), "benchmark_id": "sh.000300"}
                for h in profile["history"]]
            if len(actual) != window or actual[-1]["trade_date"] != profile["latest_history_date"]:
                raise ValueError("actual available observation window differs")
            past = [{k: h[k] for k in ("trade_date", "stock_return")} for h in actual]
            r = {"trade_date": day, "signal_cutoff_date": cutoff, "history": past,
                "history_sha256": history_hash(past), "lagged_stock_volatility": statistics.stdev(h["stock_return"] for h in past)}
            validate_risk_row(r, day, cutoff, window)
            risk[stock].append(r)
            factors[stock].append(feature(actual, day, cutoff, window, "sh.000300"))
            target = row["observed_adjacent_vendor_return_fraction"]
            joined[stock].append({"trade_date": day, "signal_cutoff_date": cutoff,
                "execution_reference_date": reference, "observed_return": None if target is None else float(Fraction(*target)),
                "execution_available": row["execution_available_for_conditional_replay"],
                "text_signal": 0.0, "text_uncertainty": 0.0, "text_evidence": "text:disabled"})
        check_steps(joined[stock])
    return risk, factors, joined


def check_steps(steps):
    """Targets are nullable evaluator fields; status is an explicit replay condition."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("nonempty temporal steps required")
    previous = None
    required = {"trade_date", "signal_cutoff_date", "execution_reference_date", "observed_return",
                "execution_available", "text_signal", "text_uncertainty", "text_evidence"}
    for step in steps:
        if not isinstance(step, dict) or not required <= set(step):
            raise ValueError("temporal step lacks explicit contract fields")
        day, cutoff, reference = map(iso, (step["trade_date"], step["signal_cutoff_date"], step["execution_reference_date"]))
        if not cutoff < reference < day or (previous is not None and day <= previous):
            raise ValueError("invalid temporal step clock")
        previous = day
        target = step["observed_return"]
        if target is not None and (type(target) not in (int, float) or not math.isfinite(target) or target <= -1):
            raise ValueError("target must be null or actual finite return greater than -1")
        if type(step["execution_available"]) is not bool:
            raise ValueError("unknown venue status must not be silently coerced")
        finite_range(step["text_signal"], -1, 1, "text_signal")
        finite_range(step["text_uncertainty"], 0, 1, "text_uncertainty")
        if type(step["text_evidence"]) is not str or not step["text_evidence"].strip():
            raise ValueError("explicit disabled-text evidence required")
