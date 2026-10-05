"""Past-only residual rank coupling; original per-stock risk budgets retained.

The ideal continuous rank mixture has uniform one-stock margins. Deterministic
SHA-256 midpoint jitters have finite precision; actual five-seed moments are
not asserted to equal theoretical moments. The shared date is global, not a
new draw per portfolio basket. No target return participates in construction.
"""
from bisect import bisect_left, bisect_right
from collections import Counter
from fractions import Fraction
import hashlib
import math

from . import temporal_public_information_risk as legacy
from . import temporal_fixed_marginal_risk as fixed
from .issuer_valuation import RISK_FIELDS
from .public_information_risk import (with_public_delivery, message_receipt,
    strategy_quote_terms, respond_issuer_background)

VERSION = "past-only-global-residual-rank-coupling-v1"
WINDOW_FIELDS = {"trade_date", "signal_cutoff_date", "common_source_dates",
    "shared_source_date_count", "rank_intervals_by_stock"}
TWO64 = 2 ** 64


def need(ok, message):
    if not ok:
        raise ValueError(message)


def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def choose_index(token, size):
    need(type(size) is int and 2 <= size <= TWO64, "invalid common source size")
    boundary = TWO64 - TWO64 % size
    for attempt in range(1000):
        value = digest(token + ":" + str(attempt))
        integer = int(value[:16], 16)
        if integer < boundary:
            return {"selected_index": integer % size, "draw_sha256": value,
                "rejection_attempt": attempt}
    raise ValueError("common date rejection sampling exhausted")


def residual_intervals(factor, dates):
    history = factor["history"]
    need(len(history) >= 2 and dates and sorted(set(dates)) == dates
        and max(dates) <= factor["signal_cutoff_date"] < factor["trade_date"],
        "residual source is missing or beyond cutoff")
    beta = Fraction(*factor["beta_fraction"])
    x = [Fraction(str(r["market_return"])) for r in history]
    y = [Fraction(str(r["stock_return"])) for r in history]
    alpha = sum(y) / len(y) - beta * sum(x) / len(x)
    source = {r["trade_date"]: b - alpha - beta * a
        for r, a, b in zip(history, x, y, strict=True)}
    need(len(source) == len(history) and set(dates) <= set(source), "unknown residual source date")
    values = [source[day] for day in dates]
    ordered = sorted(values)
    return [[bisect_left(ordered, v), bisect_right(ordered, v) - bisect_left(ordered, v)] for v in values]


def source_windows(features):
    need(isinstance(features, dict) and features, "empty residual cohort")
    stocks = sorted(features)
    first = features[stocks[0]]
    need(isinstance(first, list) and first, "empty residual calendar")
    windows = []
    for i, row in enumerate(first):
        day, cutoff = row["trade_date"], row["signal_cutoff_date"]
        need(all(len(features[s]) == len(first) and features[s][i]["trade_date"] == day
            and features[s][i]["signal_cutoff_date"] == cutoff for s in stocks), "residual cohort calendars differ")
        common = set.intersection(*(set(r["trade_date"] for r in features[s][i]["history"]) for s in stocks))
        dates = sorted(common)
        need(len(dates) >= 2 and dates[-1] <= cutoff < day,
            "insufficient known common residual history; no fallback allowed")
        windows.append({"trade_date": day, "signal_cutoff_date": cutoff,
            "common_source_dates": dates, "shared_source_date_count": len(dates),
            "rank_intervals_by_stock": {s: residual_intervals(features[s][i], dates) for s in stocks}})
    need(len({w["trade_date"] for w in windows}) == len(windows), "duplicate residual trade date")
    return windows


def selected_windows(windows, seed):
    need(isinstance(seed, str) and seed, "explicit independent common-date seed required")
    result = []
    for window in windows:
        need(set(window) == WINDOW_FIELDS, "residual source window fields differ")
        day, cutoff = window["trade_date"], window["signal_cutoff_date"]
        count = window["shared_source_date_count"]
        need(count == len(window["common_source_dates"]), "residual source count differs")
        draw = choose_index(f"residual-common-date-v1:{seed}:{day}:{cutoff}", count)
        result.append({k: window[k] for k in WINDOW_FIELDS - {"rank_intervals_by_stock"}} |
            draw | {"selected_source_date": window["common_source_dates"][draw["selected_index"]]})
    return result


def replace_residual(row, intervals, selected, stock, parameters):
    count = selected["shared_source_date_count"]
    need(len(intervals) == count, "residual rank coverage differs")
    blocks = Counter(tuple(r) for r in intervals)
    cursor = 0
    for (low, size), frequency in sorted(blocks.items()):
        need(type(low) is int and type(size) is int and low == cursor
            and size > 0 and frequency == size, "invalid residual tie partition")
        cursor += size
    need(cursor == count, "residual ranks do not partition source")
    hashed = digest(f"issuer-innovation-v1:{parameters['innovation_seed']}:{stock}:{row['trade_date']}")
    integer = int(hashed[:16], 16)
    jitter = Fraction(2 * integer + 1, 2 * TWO64)
    low, size = intervals[selected["selected_index"]]
    uniform = (low + size * jitter) / count
    z = math.sqrt(3) * (2 * float(uniform) - 1)
    residual = row["risk_budget"]["residual_amplitude_bps"] * z
    raw = residual + row["risk_budget"]["market_raw_shift_bps"]
    cap = parameters["max_shift_bps"]
    applied = max(-cap, min(cap, raw))
    return {**row, "innovation_sha256": hashed, "standardized_innovation": z,
        "raw_shift_bps": raw, "valuation_shift_bps": applied, "was_capped": raw != applied,
        "risk_budget": {**row["risk_budget"], "residual_raw_shift_bps": residual},
        "residual_coupling": {"version": VERSION, "rank_intervals": intervals,
            "selected_rank_lower": low, "selected_rank_size": size,
            "jitter_sha256": hashed, "uniform_jitter_fraction": [jitter.numerator, jitter.denominator],
            "coupled_uniform_fraction": [uniform.numerator, uniform.denominator]}}


def build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed, delivery,
               coupling_seed=None):
    if coupling_seed is None:
        return legacy.build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed, delivery)
    need(delivery in ("masked", "public"), "explicit residual delivery required")
    plain = fixed.build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed)
    windows = source_windows(features)
    selected = selected_windows(windows, coupling_seed)
    coupled = {**plain, "residual_coupling_parameters": {"version": VERSION,
        "common_date_seed": coupling_seed, "universe_stocks": sorted(features), "windows": selected},
        "by_stock": {s: [replace_residual(row, window["rank_intervals_by_stock"][s], choice, s, parameters)
            for row, window, choice in zip(rows, windows, selected, strict=True)]
            for s, rows in plain["by_stock"].items()}}
    return with_public_delivery(coupled) if delivery == "public" else coupled


def validate_issuer_valuation(assets, calendar, path):
    if not isinstance(path, dict) or "residual_coupling_parameters" not in path:
        return legacy.validate_issuer_valuation(assets, calendar, path)
    public = "information_coverage_parameters" in path
    required = {"scenario_id", "parameters", "risk_budget_parameters", "by_stock", "residual_coupling_parameters"}
    need(set(path) == required | ({"information_coverage_parameters"} if public else set()),
        "residual path header fields differ")
    need(isinstance(path["by_stock"], dict) and set(path["by_stock"]) == set(assets), "residual asset coverage differs")
    settings = path["residual_coupling_parameters"]
    need(isinstance(settings, dict) and set(settings) == {"version", "common_date_seed", "universe_stocks", "windows"}
        and settings["version"] == VERSION, "residual settings differ")
    universe = settings["universe_stocks"]
    need(isinstance(universe, list) and sorted(set(universe)) == universe and set(assets) <= set(universe)
        and all(isinstance(s, str) and s for s in universe), "residual global stock scope differs")
    need(len(settings["windows"]) == len(calendar), "residual global source calendar differs")
    templates = []
    for window, (day, cutoff, _) in zip(settings["windows"], calendar, strict=True):
        need(window["trade_date"] == day and window["signal_cutoff_date"] == cutoff,
            "residual information calendar differs")
        dates = window["common_source_dates"]
        need(sorted(set(dates)) == dates and len(dates) >= 2 and max(dates) <= cutoff,
            "residual common history is missing or beyond cutoff")
        templates.append({k: window[k] for k in WINDOW_FIELDS - {"rank_intervals_by_stock"}} |
            {"rank_intervals_by_stock": {}})
    need(settings["windows"] == selected_windows(templates, settings["common_date_seed"]),
        "residual global common draw differs")
    expected = {k: v for k, v in path.items() if k not in ("by_stock", "information_coverage_parameters")}
    expected["by_stock"] = {}
    for stock in assets:
        rows = path["by_stock"][stock]
        need(isinstance(rows, list) and len(rows) == len(calendar), "residual message calendar differs")
        rebuilt = []
        for row, window in zip(rows, settings["windows"], strict=True):
            old = fixed.budget_message({k: row[k] for k in RISK_FIELDS}, row["budget_factor_feature"],
                stock, path["parameters"], path["risk_budget_parameters"])
            need(type(row["was_capped"]) is bool, "residual cap flag must be boolean")
            intervals = residual_intervals(row["budget_factor_feature"], window["common_source_dates"])
            rebuilt.append(replace_residual(old, intervals, window, stock, path["parameters"]))
        expected["by_stock"][stock] = rebuilt
    if public:
        expected = with_public_delivery(expected)
    need(path == expected, "residual source, ranks, risk, innovations or public budget differ")
    return path


def subset(path, assets):
    return fixed.subset(path, assets)
