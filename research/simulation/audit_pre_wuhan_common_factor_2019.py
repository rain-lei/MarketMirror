"""Describe how much same-day cross-stock co-movement the 2019 simulator produces."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_exchange_auction_tie_break_2019_v2.json"
SHOCK_SOURCE = ROOT / "research_outputs/pre_wuhan_common_shock_sensitivity_2019_v1.json"
SHOCK_BALANCED_SOURCE = ROOT / "research_outputs/pre_wuhan_common_shock_balanced_2019_v1.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_common_factor_2019_v2.json"
VERSION = "pre-wuhan-common-factor-description-v1"


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean, right_mean = statistics.fmean(left), statistics.fmean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    left_ss = sum(value * value for value in left_centered)
    right_ss = sum(value * value for value in right_centered)
    if left_ss == 0 or right_ss == 0:
        return None
    return sum(a * b for a, b in zip(left_centered, right_centered)) / math.sqrt(left_ss * right_ss)


def common_factor_metrics(rows: list[dict]) -> dict:
    """Compute descriptive metrics; contemporaneous factors are never model inputs."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("common-factor audit requires nonempty daily rows")
    panel = {}
    for row in rows:
        required = {"stock_code", "trade_date", "observed_return", "price_before_minor", "price_after_minor"}
        if not isinstance(row, dict) or not required <= set(row):
            raise ValueError("daily row lacks common-factor fields")
        key = (row["stock_code"], row["trade_date"])
        if key in panel:
            raise ValueError("duplicate stock-date row")
        before, after, observed = row["price_before_minor"], row["price_after_minor"], row["observed_return"]
        if (type(before) is not int or before <= 0 or type(after) is not int or after <= 0
                or type(observed) not in (int, float) or not math.isfinite(observed)):
            raise ValueError("invalid return or price in common-factor panel")
        panel[key] = {"observed": float(observed), "synthetic": after / before - 1}
    stocks = sorted({stock for stock, _ in panel})
    dates = sorted({date for _, date in panel})
    if set(panel) != {(stock, date) for stock in stocks for date in dates}:
        raise ValueError("common-factor panel must have complete stock-date coverage")

    results = {}
    for field in ("observed", "synthetic"):
        series = {stock: [panel[stock, date][field] for date in dates] for stock in stocks}
        daily_equal_weight = [statistics.fmean(series[stock][index] for stock in stocks)
                              for index in range(len(dates))]
        pair_correlations = []
        for index, left_stock in enumerate(stocks):
            for right_stock in stocks[index + 1:]:
                corr = _correlation(series[left_stock], series[right_stock])
                if corr is not None:
                    pair_correlations.append(corr)
        loo_r2 = []
        for stock in stocks:
            factor = [statistics.fmean(series[other][index] for other in stocks if other != stock)
                      for index in range(len(dates))]
            corr = _correlation(series[stock], factor)
            if corr is not None:
                loo_r2.append(corr * corr)
        results[field] = {
            "stock_count": len(stocks),
            "sessions": len(dates),
            "stock_date_rows": len(panel),
            "mean_stock_return_std": statistics.fmean(statistics.pstdev(values) for values in series.values()),
            "equal_weight_daily_return_std": statistics.pstdev(daily_equal_weight),
            "mean_pairwise_stock_return_correlation": statistics.fmean(pair_correlations) if pair_correlations else None,
            "pairwise_correlations_defined": len(pair_correlations),
            "leave_one_stock_out_market_factor_mean_r2": statistics.fmean(loo_r2) if loo_r2 else None,
            "leave_one_stock_out_market_factor_median_r2": statistics.median(loo_r2) if loo_r2 else None,
            "leave_one_stock_out_r2_defined_stocks": len(loo_r2),
            "zero_return_fraction": sum(value == 0 for values in series.values() for value in values) / len(panel),
        }
    return results


def compute(source_path: Path = SOURCE) -> dict:
    source_path = source_path.resolve()
    allowed_sources = {SOURCE.resolve(), SHOCK_SOURCE.resolve(), SHOCK_BALANCED_SOURCE.resolve()}
    if source_path not in allowed_sources:
        raise ValueError("common-factor diagnostic is bound to a frozen 2019 mechanism archive")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    result = source.get("result", {})
    expected_version = ("pre-wuhan-exchange-auction-tie-break-v1" if source_path == SOURCE.resolve()
                        else "pre-wuhan-common-shock-sensitivity-v1" if source_path == SHOCK_SOURCE.resolve()
                        else "pre-wuhan-common-shock-balanced-sensitivity-v1")
    if (result.get("pipeline_version") != expected_version
            or result.get("sample", {}).get("selected_companies") != 123
            or result.get("sample", {}).get("sessions") != 43):
        raise ValueError("source archive does not contain the frozen 2019 development panel")
    variants = {row["name"]: row for row in result.get("variants", [])}
    expected_names = ({"uniform_nearest_prior", "exchange_specific_tie_break"}
                      if source_path == SOURCE.resolve()
                      else {"no_shock", "common_only", "issuer_specific_only", "common_plus_issuer_specific"})
    if set(variants) != expected_names:
        raise ValueError("source archive variant coverage differs")
    keyed_rows = {}
    observed_panel = None
    for name, variant in variants.items():
        rows = variant.get("daily_asset_rows")
        if not isinstance(rows, list) or len(rows) != 5289:
            raise ValueError("source archive lacks complete daily asset rows")
        this_panel = {(row["stock_code"], row["trade_date"]): row["observed_return"] for row in rows}
        if len(this_panel) != len(rows):
            raise ValueError("source archive has duplicate stock-date rows")
        if observed_panel is None:
            observed_panel = this_panel
        elif this_panel != observed_panel:
            raise ValueError("observed returns differ between paired variants")
        keyed_rows[name] = rows
    return {
        "pipeline_version": VERSION,
        "purpose": "after-the-fact descriptive decomposition of cross-stock co-movement; no same-day value is an Agent input",
        "source_archive": str(source_path),
        "source_archive_sha256": file_sha256(source_path),
        "sample": {"companies": 123, "sessions": 43, "company_days": 5289,
                   "interpretation": "2019 mechanism development sample; cohort selected retrospectively from 2020-01 question activity"},
        "metrics": {name: common_factor_metrics(rows) for name, rows in keyed_rows.items()},
        "method": {
            "equal_weight_market_proxy": "daily cross-sectional mean of the same 123-company return panel",
            "leave_one_out_factor": "each company's same-day equal-weight mean return of the other 122 companies",
            "r2": "squared Pearson correlation over the 43 dates; descriptive in-sample statistic",
            "prohibition": "contemporaneous market factors are outcomes for diagnosis only and must not be used as prediction inputs",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute(args.source)
    payload = {"result": result, "code_sha256": file_sha256(Path(__file__))}
    destination = args.output.resolve()
    if destination.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("common-factor output must be written under research_outputs")
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != payload:
            raise ValueError("archived common-factor audit differs from recomputation")
    else:
        if destination.exists():
            raise ValueError("choose a new common-factor output path")
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
