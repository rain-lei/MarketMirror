"""Lagged benchmark exposure and synthetic public valuation, never target returns."""
from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction
from datetime import date

PARAMETER_KEYS = {"history_window", "benchmark_id", "belief_scale_bps", "max_shift_bps", "risk_multiplier", "innovation_seed"}
FEATURE_KEYS = {"trade_date", "signal_cutoff_date", "history", "history_sha256", "beta_fraction",
                "market_variance_fraction", "stock_variance_fraction", "covariance_fraction",
                "market_volatility", "source_stock_volatility"}
DRAW_KEYS = {"innovation_sha256", "standardized_innovation", "raw_shift_bps", "valuation_shift_bps", "was_capped"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_parameters(p):
    if (not isinstance(p, dict) or set(p) != PARAMETER_KEYS or type(p["history_window"]) is not int
            or not 2 <= p["history_window"] <= 252 or p["benchmark_id"] != "sh.000300"
            or type(p["belief_scale_bps"]) is not int or not 1000 <= p["belief_scale_bps"] <= 10000
            or type(p["max_shift_bps"]) is not int or not 1 <= p["max_shift_bps"] <= 500
            or type(p["risk_multiplier"]) not in (int, float) or not math.isfinite(p["risk_multiplier"])
            or not 0 < p["risk_multiplier"] <= 2 or not isinstance(p["innovation_seed"], str) or not p["innovation_seed"]):
        raise ValueError("public factor parameters must be explicit and finite")


def packed(value):
    return [value.numerator, value.denominator]


def load_source_groups(path, manifest):
    """Preserve the benchmark identity validated in every prepared CSV row."""
    from dataclasses import asdict
    from types import SimpleNamespace
    from ..baselines.run_experiments import _load_market
    groups = _load_market(path, manifest)
    return {s: [SimpleNamespace(**asdict(r), benchmark_id=manifest["settings"]["benchmark_id"]) for r in rows]
            for s, rows in groups.items()}


def feature(history, day, cutoff, window, benchmark):
    def iso(value):
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("factor dates must be canonical ISO dates")
    iso(day)
    iso(cutoff)
    if type(window) is not int or not 2 <= window <= 252 or not cutoff < day or not isinstance(history, list) or len(history) != window:
        raise ValueError("public factor history must have a complete lagged window")
    dates, x, y = [], [], []
    for row in history:
        if (not isinstance(row, dict) or set(row) != {"trade_date", "stock_return", "market_return", "benchmark_id"}
                or row["benchmark_id"] != benchmark or row["trade_date"] > cutoff
                or any(type(row[k]) not in (int, float) or not math.isfinite(row[k]) or not -1 <= row[k] <= 10
                       for k in ("stock_return", "market_return"))):
            raise ValueError("invalid or future public factor history")
        iso(row["trade_date"])
        dates.append(row["trade_date"])
        x.append(Fraction(str(row["market_return"])))
        y.append(Fraction(str(row["stock_return"])))
    if dates != sorted(set(dates)) or dates[-1] != cutoff:
        raise ValueError("public factor history must end exactly at its information cutoff")
    mx, my = sum(x) / window, sum(y) / window
    vx = sum((v - mx) ** 2 for v in x) / (window - 1)
    vy = sum((v - my) ** 2 for v in y) / (window - 1)
    c = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (window - 1)
    if vx <= 0:
        raise ValueError("undefined beta: zero benchmark variance; do not fill zero")
    return {"trade_date": day, "signal_cutoff_date": cutoff, "history": history,
            "history_sha256": digest(history), "beta_fraction": packed(c / vx),
            "market_variance_fraction": packed(vx), "stock_variance_fraction": packed(vy),
            "covariance_fraction": packed(c), "market_volatility": math.sqrt(float(vx)),
            "source_stock_volatility": math.sqrt(float(vy))}


def build_features(groups, joined, parameters):
    validate_parameters(parameters)
    output, benchmark_by_date = {}, {}
    for stock, steps in sorted(joined.items()):
        series = sorted(groups[stock], key=lambda r: r.trade_date)
        index = {r.trade_date.isoformat(): i for i, r in enumerate(series)}
        if len(index) != len(series):
            raise ValueError("duplicate source market date")
        rows = []
        for step in steps:
            day, cutoff = step["trade_date"], step["signal_cutoff_date"]
            end, window = index.get(cutoff), parameters["history_window"]
            if end is None or end + 1 < window:
                raise ValueError("public factor lacks source history")
            history = []
            for r in series[end + 1 - window:end + 1]:
                record = {"trade_date": r.trade_date.isoformat(), "stock_return": r.stock_return,
                          "market_return": r.market_return, "benchmark_id": r.benchmark_id}
                old = benchmark_by_date.setdefault(record["trade_date"], (r.benchmark_id, r.market_return))
                if old != (r.benchmark_id, r.market_return):
                    raise ValueError("companies disagree on the common benchmark")
                history.append(record)
            rows.append(feature(history, day, cutoff, window, parameters["benchmark_id"]))
        output[stock] = rows
    if not output:
        raise ValueError("public factor requires a nonempty company panel")
    return output


def draw(row, parameters):
    validate_parameters(parameters)
    if set(row) != FEATURE_KEYS:
        raise ValueError("factor feature contains undeclared fields")
    h = hashlib.sha256(f"public-factor-common-v1:{parameters['innovation_seed']}:{row['trade_date']}".encode()).hexdigest()
    z = math.sqrt(3) * (2 * ((int(h[:16], 16) + 0.5) / 2 ** 64) - 1)
    beta = float(Fraction(*row["beta_fraction"]))
    raw = beta * row["market_volatility"] * parameters["risk_multiplier"] * z * 10000
    shift = max(-parameters["max_shift_bps"], min(parameters["max_shift_bps"], raw))
    return {**row, "innovation_sha256": h, "standardized_innovation": z,
            "raw_shift_bps": raw, "valuation_shift_bps": shift, "was_capped": raw != shift}


def build_path(features, parameters):
    validate_parameters(parameters)
    return {"parameters": dict(parameters), "by_stock": {s: [draw(r, parameters) for r in rows] for s, rows in sorted(features.items())}}


def validate_path(assets, calendar, path):
    if not isinstance(path, dict) or set(path) != {"parameters", "by_stock"} or set(path["by_stock"]) != set(assets):
        raise ValueError("public factor path scope differs")
    p = path["parameters"]
    validate_parameters(p)
    for stock in assets:
        rows = path["by_stock"][stock]
        if len(rows) != len(calendar):
            raise ValueError("public factor calendar coverage differs")
        for row, (day, cutoff, _) in zip(rows, calendar, strict=True):
            if set(row) != FEATURE_KEYS | DRAW_KEYS or type(row["was_capped"]) is not bool:
                raise ValueError("public factor row schema differs")
            for key in ("beta_fraction", "market_variance_fraction", "stock_variance_fraction", "covariance_fraction"):
                value = row[key]
                if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int for v in value) or value[1] <= 0:
                    raise ValueError("factor moments require explicit rational numbers")
            for key in ("market_volatility", "source_stock_volatility", "standardized_innovation", "raw_shift_bps", "valuation_shift_bps"):
                if type(row[key]) not in (int, float) or not math.isfinite(row[key]):
                    raise ValueError("factor numeric values must be finite and nonboolean")
            f = feature(row["history"], day, cutoff, p["history_window"], p["benchmark_id"])
            if row != draw(f, p):
                raise ValueError("public valuation differs from its lagged source and common draw")
    return path


def subset(path, assets):
    return {"parameters": dict(path["parameters"]), "by_stock": {s: path["by_stock"][s] for s in assets}}


def message(row, parameters):
    fields = {k: row[k] for k in (FEATURE_KEYS | DRAW_KEYS) - {"history"}}
    return {**fields, "applied_belief_signal": row["valuation_shift_bps"] / parameters["belief_scale_bps"]}
