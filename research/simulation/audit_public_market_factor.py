"""Rebuild lagged exposure from raw moments, independent of factor production."""
from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction
from datetime import date
from types import SimpleNamespace
import csv


def load_raw_groups(path, manifest):
    groups, seen, benchmark = {}, set(), {}
    with open(path, encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            s, d = row["stock_code"], row["trade_date"]
            if (s, d) in seen or row["benchmark_id"] != manifest["settings"]["benchmark_id"]:
                raise ValueError("independent source duplicate/benchmark identity differs")
            seen.add((s, d))
            sr, mr = float(row["stock_return"]), float(row["market_return"])
            if not math.isfinite(sr) or not math.isfinite(mr) or benchmark.setdefault(d, mr) != mr:
                raise ValueError("independent source common benchmark differs")
            groups.setdefault(s, []).append(SimpleNamespace(trade_date=date.fromisoformat(d), stock_return=sr,
                                              market_return=mr, benchmark_id=row["benchmark_id"]))
    if set(groups) != set(manifest["series"]) or len(seen) != manifest["counts"]["market_rows"]:
        raise ValueError("independent source market coverage differs")
    for s, rows in groups.items():
        rows.sort(key=lambda r: r.trade_date)
        dates = [r.trade_date.isoformat() for r in rows]
        m = manifest["series"][s]
        if (len(rows) != m["return_rows"] or dates[0] != m["first_return_date"] or dates[-1] != m["last_return_date"]
                or dates != [d for d in manifest["session_dates"] if dates[0] <= d <= dates[-1]]):
            raise ValueError("independent source history calendar differs")
    return groups


def compact_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def expected_message(row, parameters):
    fields = ("trade_date", "signal_cutoff_date", "history_sha256", "beta_fraction", "market_variance_fraction",
              "stock_variance_fraction", "covariance_fraction", "market_volatility", "source_stock_volatility",
              "innovation_sha256", "standardized_innovation", "raw_shift_bps", "valuation_shift_bps", "was_capped")
    return {**{k: row[k] for k in fields}, "applied_belief_signal": row["valuation_shift_bps"] / parameters["belief_scale_bps"]}


def independent_panel(groups, joined, parameters):
    n = parameters["history_window"]
    output, common = {}, {}
    for stock, steps in sorted(joined.items()):
        source = sorted(groups[stock], key=lambda r: r.trade_date)
        dates = [r.trade_date.isoformat() for r in source]
        if dates != sorted(set(dates)):
            raise ValueError("independent factor source dates repeat")
        output[stock] = []
        for step in steps:
            cutoff, day = step["signal_cutoff_date"], step["trade_date"]
            eligible = [r for r in source if r.trade_date.isoformat() <= cutoff]
            if len(eligible) < n or eligible[-1].trade_date.isoformat() != cutoff or not cutoff < day:
                raise ValueError("independent factor lagged history missing")
            history = []
            for r in eligible[-n:]:
                if r.benchmark_id != parameters["benchmark_id"]:
                    raise ValueError("independent factor benchmark differs")
                for v in (r.market_return, r.stock_return):
                    if type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 10:
                        raise ValueError("independent factor history return invalid")
                record = {"trade_date": r.trade_date.isoformat(), "stock_return": r.stock_return,
                          "market_return": r.market_return, "benchmark_id": r.benchmark_id}
                marker = (r.benchmark_id, r.market_return)
                if common.setdefault(record["trade_date"], marker) != marker:
                    raise ValueError("independent public benchmark inconsistently delivered")
                history.append(record)
            x = [Fraction(str(r["market_return"])) for r in history]
            y = [Fraction(str(r["stock_return"])) for r in history]
            vx = (sum(v * v for v in x) - sum(x) ** 2 / n) / (n - 1)
            vy = (sum(v * v for v in y) - sum(y) ** 2 / n) / (n - 1)
            cov = (sum(a * b for a, b in zip(x, y, strict=True)) - sum(x) * sum(y) / n) / (n - 1)
            if vx <= 0:
                raise ValueError("independent beta undefined; no zero fill")
            beta = cov / vx
            h = hashlib.sha256(("public-factor-common-v1:" + parameters["innovation_seed"] + ":" + day).encode()).hexdigest()
            z = math.sqrt(3) * (2 * ((int(h[:16], 16) + 0.5) / 2 ** 64) - 1)
            raw = float(beta) * math.sqrt(float(vx)) * parameters["risk_multiplier"] * z * 10000
            shift = max(-parameters["max_shift_bps"], min(parameters["max_shift_bps"], raw))
            pairs = lambda f: [f.numerator, f.denominator]
            output[stock].append({"trade_date": day, "signal_cutoff_date": cutoff, "history": history,
                "history_sha256": compact_hash(history), "beta_fraction": pairs(beta), "market_variance_fraction": pairs(vx),
                "stock_variance_fraction": pairs(vy), "covariance_fraction": pairs(cov),
                "market_volatility": math.sqrt(float(vx)), "source_stock_volatility": math.sqrt(float(vy)),
                "innovation_sha256": h, "standardized_innovation": z, "raw_shift_bps": raw,
                "valuation_shift_bps": shift, "was_capped": raw != shift})
    return {"parameters": dict(parameters), "by_stock": output}


def verify_panel(actual, groups, joined, parameters):
    expected = independent_panel(groups, joined, parameters)
    if actual != expected:
        raise ValueError("public factor differs from independently rebuilt raw history")
    for rows in actual["by_stock"].values():
        if any(type(r["was_capped"]) is not bool for r in rows):
            raise ValueError("public cap flag must be a boolean")
        for r in rows:
            for k in ("beta_fraction", "market_variance_fraction", "stock_variance_fraction", "covariance_fraction"):
                if not isinstance(r[k], list) or len(r[k]) != 2 or any(type(v) is not int for v in r[k]):
                    raise ValueError("independent rational factor fields require integer types")
            for k in ("market_volatility", "source_stock_volatility", "standardized_innovation", "raw_shift_bps", "valuation_shift_bps"):
                if type(r[k]) not in (int, float) or not math.isfinite(r[k]):
                    raise ValueError("independent public numeric fields must be finite")
    return sum(len(rows) for rows in expected["by_stock"].values())
