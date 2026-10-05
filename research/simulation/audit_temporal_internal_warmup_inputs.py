"""Reconstruct temporal model inputs directly from original quoted source strings."""
from __future__ import annotations
from fractions import Fraction
import gzip
import hashlib
import json
import math
import statistics

from .temporal_information_study import ROOT, require
from .audit_fixed_marginal_risk import expected_budget_path
from .audit_public_information_risk import verify_budget


def compact_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def packed(value):
    return [value.numerator, value.denominator]


def records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return list(map(json.loads, stream))


def independent_source(cfg):
    pairs = records(ROOT / cfg["stock_pairs_path"])
    sources = {(r["secid"].split(".")[1], r["trade_date"]): r for r in pairs}
    positions = {(r["secid"].split(".")[1], r["trade_date"]): r for r in records(ROOT / cfg["positions_path"])}
    benchmark = {r["trade_date"]: r for r in records(ROOT / cfg["benchmark_positions_path"]) if r["kind"] == "benchmark"}
    calendar, stocks, dates = cfg["source_trade_dates"], cfg["stocks"], cfg["dates"]
    require(len(sources) == len(pairs) == len(stocks) * len(calendar)
        and set(sources) == {(s, d) for s in stocks for d in calendar}, "independent raw stock coverage differs")
    require(set(benchmark) == set(calendar) and set(positions) == {(s, d) for s in stocks for d in dates},
        "independent benchmark or position scope differs")
    indices = {d: i for i, d in enumerate(calendar)}
    def close(row):
        return Fraction(row["provider_fields"]["f53"]) if row["raw_line"] is not None else None
    def return_between(current, previous):
        return current / previous - 1 if current is not None and previous is not None else None
    benchmark_returns = {d: return_between(close(benchmark[d]), close(benchmark[calendar[i - 1]])) if i else None
        for i, d in enumerate(calendar)}
    risk, features, joined = {}, {}, {}
    stale, unknown_targets, unknown_references = [], [], []
    for s in stocks:
        source_returns = {d: return_between(close(sources[s, d]["back_adjusted_source"]),
            close(sources[s, calendar[i - 1]]["back_adjusted_source"])) if i else None for i, d in enumerate(calendar)}
        risk[s], features[s], joined[s] = [], [], []
        for d in dates:
            i = indices[d]
            cutoff, reference = calendar[i - 2], calendar[i - 1]
            position = positions[s, d]
            require(position["signal_cutoff_date"] == cutoff and position["execution_reference_date"] == reference,
                "independent source clock differs")
            actual_days = [past for past in calendar[:i - 1] if source_returns[past] is not None and benchmark_returns[past] is not None][-cfg["history_window"]:]
            h = [{"trade_date": past, "stock_return": float(source_returns[past]), "market_return": float(benchmark_returns[past]),
                "benchmark_id": "sh.000300"} for past in actual_days]
            profile = position["available_observation_profile"]
            expected_source_history = [{"trade_date": past, "stock_return_fraction": packed(source_returns[past]),
                "market_return_fraction": packed(benchmark_returns[past])} for past in actual_days]
            require(profile["history"] == expected_source_history and len(h) == cfg["history_window"]
                and profile["latest_history_date"] == actual_days[-1] and profile["stale_calendar_sessions"] == indices[cutoff] - indices[actual_days[-1]],
                "available history differs from original adjacent source quotes")
            if actual_days[-1] != cutoff:
                stale.append([s, d, actual_days[-1], profile["stale_calendar_sessions"]])
            past = [{"trade_date": r["trade_date"], "stock_return": r["stock_return"]} for r in h]
            risk[s].append({"trade_date": d, "signal_cutoff_date": cutoff, "history": past,
                "history_sha256": compact_hash(past), "lagged_stock_volatility": statistics.stdev(r["stock_return"] for r in past)})
            x, y = [Fraction(str(r["market_return"])) for r in h], [Fraction(str(r["stock_return"])) for r in h]
            n = len(h)
            vx = (sum(v * v for v in x) - sum(x) ** 2 / n) / (n - 1)
            vy = (sum(v * v for v in y) - sum(y) ** 2 / n) / (n - 1)
            cross = (sum(a * b for a, b in zip(x, y, strict=True)) - sum(x) * sum(y) / n) / (n - 1)
            require(vx > 0 and vy >= 0, "undefined independent benchmark exposure")
            features[s].append({"trade_date": d, "signal_cutoff_date": cutoff, "history": h,
                "history_sha256": compact_hash(h), "beta_fraction": packed(cross / vx), "market_variance_fraction": packed(vx),
                "stock_variance_fraction": packed(vy), "covariance_fraction": packed(cross),
                "market_volatility": math.sqrt(float(vx)), "source_stock_volatility": math.sqrt(float(vy))})
            target = source_returns[d]
            require(position["observed_adjacent_vendor_return_fraction"] == (packed(target) if target is not None else None),
                "independent target null/value differs")
            raw = sources[s, d]["unadjusted_source"]
            volume = Fraction(raw["provider_fields"]["f56"]) if raw["raw_line"] is not None else None
            available = True if volume is not None and volume > 0 else False if raw.get("documented_trade_status") == "SUSPENDED_FULL_SESSION" else None
            require(type(available) is bool and position["execution_available_for_conditional_replay"] is available,
                "source status was filled or differs")
            source_reference = sources[s, reference]["unadjusted_source"]["provider_fields"]["f53"]
            require(position["raw_reference_close"] == source_reference, "unknown raw reference was filled")
            if source_reference is None:
                unknown_references.append([s, d])
            if target is None:
                unknown_targets.append([s, d])
            joined[s].append({"trade_date": d, "signal_cutoff_date": cutoff, "execution_reference_date": reference,
                "observed_return": None if target is None else float(target), "execution_available": available,
                "text_signal": 0.0, "text_uncertainty": 0.0, "text_evidence": "text:disabled"})
    require(len(joined) == len(stocks) and all(len(rows) == len(dates) for rows in joined.values()), "full warmup plus evaluation scope differs")
    return risk, features, joined, {"stale_positions": stale, "unknown_targets": unknown_targets, "unknown_raw_references": unknown_references}
