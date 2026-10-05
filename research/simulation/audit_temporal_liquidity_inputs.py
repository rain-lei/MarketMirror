"""Independent lagged liquidity arithmetic from original dated provider strings."""
from __future__ import annotations

from fractions import Fraction
import gzip
import hashlib
import json

from .temporal_information_study import ROOT, require


def independent_liquidity(cfg, path):
    with gzip.open(ROOT / cfg["stock_pairs_path"], "rt", encoding="utf-8") as f:
        records = list(map(json.loads, f))
    stocks, dates, calendar = cfg["stocks"], cfg["dates"], cfg["source_trade_dates"]
    index = {d: i for i, d in enumerate(calendar)}
    data = {s: {} for s in stocks}
    for row in records:
        stock, date = row["secid"].split(".")[1], row["trade_date"]
        raw = row["unadjusted_source"]
        require(date not in data[stock] and raw["secid"] == row["secid"]
                and raw["trade_date"] == date and raw["fqt"] == 0, "independent liquidity identity differs")
        fields = raw["provider_fields"]
        if raw["raw_line"] is None:
            require(all(fields[f"f{n}"] is None for n in range(51, 62)), "independent missing liquidity was filled")
            data[stock][date] = None
        else:
            require(raw["raw_line"].split(",") == [fields[f"f{n}"] for n in range(51, 62)], "independent raw liquidity fields differ")
            data[stock][date] = {"trade_date": date, "source_volume_raw": fields["f56"],
                "source_amount_raw": fields["f57"], "source_turnover_percent_raw": fields["f61"]}
    require(len(records) == len(stocks) * len(calendar) and all(set(v) == set(calendar) for v in data.values()), "independent full raw scope differs")
    def history(stock, cutoff):
        selected = [data[stock][d] for d in calendar if d <= cutoff and data[stock][d] is not None]
        return selected[-20:]
    first_cutoff = calendar[index[dates[0]] - 2]
    reference_windows = {s: history(s, first_cutoff) for s in stocks}
    means = {}
    for s in stocks:
        require(len(reference_windows[s]) == 20, "incomplete independent reference history")
        means[s] = sum(Fraction(h["source_turnover_percent_raw"]) for h in reference_windows[s]) / 2000
    sorted_means = sorted(means.values())
    mid = len(sorted_means) // 2
    reference = sorted_means[mid] if len(sorted_means) % 2 else (sorted_means[mid - 1] + sorted_means[mid]) / 2
    pack = lambda v: [v.numerator, v.denominator]
    hash_value = lambda v: hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    require(path["stocks"] == stocks and path["dates"] == dates and path["history_window"] == 20
            and path["reference_cutoff_date"] == first_cutoff
            and path["turnover_reference_fraction"] == pack(reference)
            and path["reference_windows_sha256"] == hash_value(reference_windows)
            and path["reference_mean_turnover_by_stock"] == {s: pack(v) for s, v in means.items()}
            and path["missing_values_filled"] is False and path["new_default_selected"] is False,
            "independent preperiod reference differs")
    counts = {"independent_actual_liquidity_rows": 0, "independent_raw_history_observations": 0,
              "stale_liquidity_rows": 0, "capacity_lots_histogram": {}, "history_unknowns_filled": False}
    for s in stocks:
        require(len(path["by_stock"][s]) == len(dates), "independent liquidity calendar differs")
        for i, d in enumerate(dates):
            cutoff = calendar[index[d] - 2]
            h = history(s, cutoff)
            turnover = sum(Fraction(v["source_turnover_percent_raw"]) for v in h) / 2000
            volume = sum(Fraction(v["source_volume_raw"]) for v in h) / 20
            amount = sum(Fraction(v["source_amount_raw"]) for v in h) / 20
            ratio = turnover / reference
            bounded = Fraction(1, 2) if ratio < Fraction(1, 2) else Fraction(2) if ratio > 2 else ratio
            scaled = 4 * bounded
            whole, remainder = divmod(scaled.numerator, scaled.denominator)
            lots = whole + int(2 * remainder >= scaled.denominator)
            expected = {"stock_code": s, "trade_date": d, "signal_cutoff_date": cutoff,
                "latest_history_date": h[-1]["trade_date"], "stale_calendar_sessions": index[cutoff] - index[h[-1]["trade_date"]],
                "history": h, "history_sha256": hash_value(h), "mean_source_volume_units_fraction": pack(volume),
                "mean_source_amount_units_fraction": pack(amount), "mean_turnover_fraction": pack(turnover),
                "turnover_reference_fraction": pack(reference), "capacity_ratio_fraction": pack(ratio),
                "clipped_capacity_ratio_fraction": pack(bounded), "base_max_order_lots": 4,
                "applied_max_order_lots": lots, "source_unknowns_filled": False, "point_in_time_feed_certified": False}
            require(len(h) == 20 and all(v["trade_date"] <= cutoff for v in h)
                    and path["by_stock"][s][i] == expected, "independent full source liquidity row differs")
            counts["independent_actual_liquidity_rows"] += 1
            counts["independent_raw_history_observations"] += len(h)
            counts["stale_liquidity_rows"] += int(h[-1]["trade_date"] != cutoff)
            label = str(lots)
            counts["capacity_lots_histogram"][label] = counts["capacity_lots_histogram"].get(label, 0) + 1
    require(counts["independent_actual_liquidity_rows"] == 7134, "full liquidity scope differs")
    return counts
