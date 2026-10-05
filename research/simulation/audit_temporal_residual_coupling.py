"""Independent raw-source rank grouping and hash/draw reconstruction.

Does not import the residual coupling producer. Existing raw-source OLS and
independent budget/receipt integration remain applicable; a different counting
algorithm rebuilds ranks, including ties, and exact analytic margins.
"""
from collections import Counter
from fractions import Fraction
import hashlib
import math

from .audit_temporal_inputs import independent_paths
from .audit_public_information_risk import verify_budget
from .temporal_information_study import require, read, ROOT

VERSION = "past-only-global-residual-rank-coupling-v1"
SIZE64 = 1 << 64


def independent_windows(cfg, features):
    result = []
    for i, date in enumerate(cfg["dates"]):
        rows = {s: features[s][i] for s in cfg["stocks"]}
        common = sorted(set.intersection(*(set(v["trade_date"] for v in r["history"]) for r in rows.values())))
        cutoff = rows[cfg["stocks"][0]]["signal_cutoff_date"]
        require(len(common) >= 2 and common[-1] <= cutoff < date, "independent joint source has insufficient past history")
        declared = {}
        for stock, row in rows.items():
            require(row["trade_date"] == date and row["signal_cutoff_date"] == cutoff, "independent joint clocks differ")
            n = len(row["history"])
            x = [Fraction(str(v["market_return"])) for v in row["history"]]
            y = [Fraction(str(v["stock_return"])) for v in row["history"]]
            centered_x = [v - sum(x) / n for v in x]
            centered_y = [v - sum(y) / n for v in y]
            slope = sum(a * b for a, b in zip(centered_x, centered_y, strict=True)) / sum(a * a for a in centered_x)
            require(slope == Fraction(*row["beta_fraction"]), "independent centered beta differs")
            residuals = {r["trade_date"]: b - slope * a
                for r, a, b in zip(row["history"], centered_x, centered_y, strict=True)}
            values = [residuals[d] for d in common]
            frequencies = Counter(values)
            intervals, cursor = {}, 0
            mean, second = Fraction(0), Fraction(0)
            for value, size in sorted(frequencies.items()):
                intervals[value] = [cursor, size]
                a, b = Fraction(cursor, len(common)), Fraction(cursor + size, len(common))
                mass = Fraction(size, len(common))
                mean += mass * (a + b) / 2
                second += mass * (a * a + a * b + b * b) / 3
                cursor += size
            require(cursor == len(common) and mean == Fraction(1, 2) and second == Fraction(1, 3),
                "independent rank mixture marginal differs")
            declared[stock] = [intervals[v] for v in values]
        result.append({"trade_date": date, "signal_cutoff_date": cutoff, "common_source_dates": common,
            "shared_source_date_count": len(common), "rank_intervals_by_stock": declared})
    return result


def independent_selection(window, seed):
    n = window["shared_source_date_count"]
    require(type(n) is int and n >= 2, "independent source size invalid")
    ceiling = (SIZE64 // n) * n
    for rejected in range(1000):
        token = f"residual-common-date-v1:{seed}:{window['trade_date']}:{window['signal_cutoff_date']}:{rejected}"
        hashed = hashlib.sha256(token.encode()).hexdigest()
        number = int.from_bytes(bytes.fromhex(hashed[:16]), "big")
        if number < ceiling:
            chosen = number - (number // n) * n
            return {k: v for k, v in window.items() if k != "rank_intervals_by_stock"} | {
                "selected_index": chosen, "draw_sha256": hashed, "rejection_attempt": rejected,
                "selected_source_date": window["common_source_dates"][chosen]}
    raise ValueError("independent common date rejection draw exhausted")


def independent_row(legacy, window, choice, stock, parameters):
    token = f"issuer-innovation-v1:{parameters['innovation_seed']}:{stock}:{legacy['trade_date']}"
    raw_hash = hashlib.sha256(token.encode()).hexdigest()
    integer = int.from_bytes(bytes.fromhex(raw_hash[:16]), "big")
    noise = Fraction(integer, SIZE64) + Fraction(1, 2 * SIZE64)
    intervals = window["rank_intervals_by_stock"][stock]
    low, block = intervals[choice["selected_index"]]
    u = Fraction(low, window["shared_source_date_count"]) + noise * Fraction(block, window["shared_source_date_count"])
    z = math.sqrt(3) * (2 * float(u) - 1)
    private = legacy["risk_budget"]["residual_amplitude_bps"] * z
    total = private + legacy["risk_budget"]["market_raw_shift_bps"]
    cap = parameters["max_shift_bps"]
    applied = min(cap, max(-cap, total))
    return {**legacy, "innovation_sha256": raw_hash, "standardized_innovation": z,
        "raw_shift_bps": total, "valuation_shift_bps": applied, "was_capped": total != applied,
        "risk_budget": {**legacy["risk_budget"], "residual_raw_shift_bps": private},
        "residual_coupling": {"version": VERSION, "rank_intervals": intervals,
            "selected_rank_lower": low, "selected_rank_size": block, "jitter_sha256": raw_hash,
            "uniform_jitter_fraction": [noise.numerator, noise.denominator],
            "coupled_uniform_fraction": [u.numerator, u.denominator]}}


def verify_prepared(cfg, saved, risk, features, seed):
    windows = independent_windows(cfg, features)
    source = read(ROOT / cfg["residual_rank_preparation_path"])
    require(source["status"] == "PASS_FULL_PAST_ONLY_RESIDUAL_RANK_PREPARATION"
        and source["windows"] == windows, "independently rebuilt source rank preparation differs")
    legacy = independent_paths(cfg, risk, features, seed)
    date_seed = cfg["residual_common_seed_namespace"] + seed["innovation_seed"]
    selected = [independent_selection(w, date_seed) for w in windows]
    settings = {"version": VERSION, "common_date_seed": date_seed,
        "universe_stocks": sorted(features), "windows": selected}
    counts = {"independently_rebuilt_residual_rank_windows": len(cfg["stocks"]) * len(cfg["dates"]),
        "independently_verified_common_date_draws": len(cfg["dates"]),
        "independently_verified_coupled_innovations": 0, "independently_rebuilt_budget_rows": 0,
        "independently_integrated_public_budget_rows": 0}
    for mode in ("market_independent", "market_shared"):
        old = legacy[mode + "_masked"]
        expected = {**old, "residual_coupling_parameters": settings,
            "by_stock": {s: [independent_row(row, window, choice, s, old["parameters"])
                for row, window, choice in zip(rows, windows, selected, strict=True)]
                for s, rows in old["by_stock"].items()}}
        require(saved[mode + "_masked"] == expected, "independent coupled risk messages differ")
        counts["independently_verified_coupled_innovations"] += sum(map(len, expected["by_stock"].values()))
        public = saved[mode + "_public"]
        require(public["information_coverage_parameters"] ==
            {"version": "public-information-matched-received-risk-v1", "delivery": "public"}, "coupled public coverage differs")
        plain = {k: v for k, v in public.items() if k != "information_coverage_parameters"}
        plain["by_stock"] = {s: [{k: v for k, v in r.items() if k != "public_information_budget"} for r in rows]
            for s, rows in public["by_stock"].items()}
        require(plain == expected, "public coverage changed coupled innovations or past source")
        for rows in public["by_stock"].values():
            for row in rows:
                verify_budget(row, public["parameters"])
                counts["independently_rebuilt_budget_rows"] += 1
                counts["independently_integrated_public_budget_rows"] += 1
                counts["independently_verified_coupled_innovations"] += 1
    require(set(saved) == {m + "_" + d for m in ("market_independent", "market_shared") for d in ("masked", "public")},
        "undeclared coupled message path")
    return counts
