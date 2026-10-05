"""Test whether strictly lagged volatility helps forecast risk in the 2019 panel."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
MARKET_MANIFEST = ROOT / "research_outputs/wuhan_pit_market_prepared_2020_v2/market_manifest.json"
BALANCED_SOURCE = ROOT / "research_outputs/pre_wuhan_common_shock_balanced_2019_v1.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_volatility_persistence_2019_v2.json"
WINDOW = 20
TRAIN_SESSIONS = 30
VERSION = "pre-wuhan-lagged-volatility-persistence-v1"


def build_risk_panel(groups: dict, codes: list[str], dates: list[str], window: int = WINDOW) -> list[dict]:
    """Build target-day absolute returns from features ending at t-2."""
    if (not isinstance(groups, dict) or not codes or len(set(codes)) != len(codes)
            or not dates or len(set(dates)) != len(dates) or type(window) is not int or window < 2):
        raise ValueError("risk panel requires unique stocks/dates and a valid history window")
    panel = []
    shared_market_returns = {}
    for code in codes:
        series = sorted(groups.get(code, []), key=lambda row: row.trade_date)
        index_by_day = {row.trade_date.isoformat(): index for index, row in enumerate(series)}
        if len(index_by_day) != len(series):
            raise ValueError("market series has duplicate dates")
        for day in dates:
            index = index_by_day.get(day)
            if index is None or index < window + 1:
                raise ValueError("market series lacks strictly lagged volatility history")
            history = series[index - window - 1:index - 1]
            if len(history) != window or history[-1].trade_date.isoformat() >= day:
                raise ValueError("volatility history does not end before t-1")
            stock_vol = statistics.stdev(row.stock_return for row in history)
            market_history = [row.market_return for row in history]
            market_vol = statistics.stdev(market_history)
            target = series[index].stock_return
            if not all(math.isfinite(value) for value in (stock_vol, market_vol, target)):
                raise ValueError("risk panel contains a non-finite value")
            prior_market_vol = shared_market_returns.setdefault(day, market_vol)
            if not math.isclose(prior_market_vol, market_vol, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("benchmark volatility differs across the same market date")
            panel.append({"stock_code": code, "trade_date": day,
                          "signal_cutoff_date": series[index - 2].trade_date.isoformat(),
                          "lagged_stock_volatility_20": stock_vol,
                          "lagged_benchmark_volatility_20": market_vol,
                          "absolute_observed_return": abs(target)})
    return panel


def _regression(train_x: list[float], train_y: list[float], test_x: list[float]) -> list[float]:
    if not train_x or len(train_x) != len(train_y) or not test_x:
        raise ValueError("regression needs matched nonempty train and test rows")
    mean_x, mean_y = statistics.fmean(train_x), statistics.fmean(train_y)
    denominator = sum((value - mean_x) ** 2 for value in train_x)
    slope = (sum((x - mean_x) * (y - mean_y) for x, y in zip(train_x, train_y, strict=True)) / denominator
             if denominator > 0 else 0.0)
    return [max(0.0, mean_y + slope * (value - mean_x)) for value in test_x]


def score_forecasts(actual: list[float], predicted: list[float], reference: list[float] | None = None) -> dict:
    if not actual or len(actual) != len(predicted) or (reference is not None and len(reference) != len(actual)):
        raise ValueError("forecast scores require equally sized actual/predicted arrays")
    mse = statistics.fmean((a - p) ** 2 for a, p in zip(actual, predicted, strict=True))
    mean_actual = statistics.fmean(actual)
    reference = reference if reference is not None else [mean_actual] * len(actual)
    reference_sse = sum((a - baseline) ** 2 for a, baseline in zip(actual, reference, strict=True))
    model_sse = sum((a - p) ** 2 for a, p in zip(actual, predicted, strict=True))
    return {"observations": len(actual), "mae": statistics.fmean(abs(a - p) for a, p in zip(actual, predicted, strict=True)),
            "rmse": math.sqrt(mse), "r2_vs_reference": 1 - model_sse / reference_sse if reference_sse else None}


def _score_panel(train: list[dict], test: list[dict]) -> dict:
    stock_means = {}
    for row in train:
        stock_means.setdefault(row["stock_code"], []).append(row["absolute_observed_return"])
    means = {code: statistics.fmean(values) for code, values in stock_means.items()}
    train_means = {row["stock_code"]: statistics.fmean(
        [r["absolute_observed_return"] for r in train if r["stock_code"] == row["stock_code"]])
                   for row in train}
    centered_x, centered_y = [], []
    for row in train:
        code = row["stock_code"]
        stock_x = [r["lagged_stock_volatility_20"] for r in train if r["stock_code"] == code]
        centered_x.append(row["lagged_stock_volatility_20"] - statistics.fmean(stock_x))
        centered_y.append(row["absolute_observed_return"] - train_means[code])
    denominator = sum(value * value for value in centered_x)
    slope = sum(x * y for x, y in zip(centered_x, centered_y, strict=True)) / denominator if denominator else 0.0
    test_actual = [row["absolute_observed_return"] for row in test]
    stock_mean_prediction = [means[row["stock_code"]] for row in test]
    stock_vol_prediction = [max(0.0, means[row["stock_code"]] + slope * (
        row["lagged_stock_volatility_20"] - statistics.fmean([
            r["lagged_stock_volatility_20"] for r in train if r["stock_code"] == row["stock_code"]]))) for row in test]
    return {
        "stock_specific_training_mean": score_forecasts(test_actual, stock_mean_prediction, stock_mean_prediction),
        "lagged_stock_volatility_with_stock_fixed_effect": score_forecasts(
            test_actual, stock_vol_prediction, stock_mean_prediction),
        "pooled_within_stock_slope": slope,
    }


def _daily_panel(panel: list[dict]) -> list[dict]:
    grouped = {}
    for row in panel:
        grouped.setdefault(row["trade_date"], []).append(row)
    daily = []
    for day, rows in sorted(grouped.items()):
        if len({row["stock_code"] for row in rows}) != len(rows):
            raise ValueError("daily risk aggregation contains duplicate stock rows")
        benchmark = {row["lagged_benchmark_volatility_20"] for row in rows}
        if len(benchmark) != 1:
            raise ValueError("daily benchmark volatility is not a single shared value")
        daily.append({"trade_date": day,
                      "mean_absolute_return": statistics.fmean(row["absolute_observed_return"] for row in rows),
                      "mean_lagged_stock_volatility": statistics.fmean(row["lagged_stock_volatility_20"] for row in rows),
                      "lagged_benchmark_volatility": next(iter(benchmark))})
    return daily


def _score_daily(daily: list[dict], train_dates: set[str]) -> dict:
    train = [row for row in daily if row["trade_date"] in train_dates]
    test = [row for row in daily if row["trade_date"] not in train_dates]
    if not train or not test:
        raise ValueError("daily forecast split is empty")
    actual = [row["mean_absolute_return"] for row in test]
    baseline = [statistics.fmean(row["mean_absolute_return"] for row in train)] * len(test)
    result = {"training_mean_baseline": score_forecasts(actual, baseline, baseline)}
    for label, feature in (("lagged_cross_section_stock_volatility", "mean_lagged_stock_volatility"),
                           ("lagged_benchmark_volatility", "lagged_benchmark_volatility")):
        predicted = _regression([row[feature] for row in train],
                                [row["mean_absolute_return"] for row in train],
                                [row[feature] for row in test])
        result[label] = score_forecasts(actual, predicted, baseline)
    return result


def compute() -> dict:
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    source = json.loads(BALANCED_SOURCE.read_text(encoding="utf-8"))
    csv_path = MARKET_MANIFEST.parent / "market_daily.csv"
    if (market.get("data_kind") != "observed"
            or market.get("settings", {}).get("stock_return_basis") != "adjusted_price_return"
            or file_sha256(csv_path) != market.get("artifacts", {}).get("market_daily.csv", {}).get("sha256")
            or source.get("result", {}).get("pipeline_version") != "pre-wuhan-common-shock-balanced-sensitivity-v1"):
        raise ValueError("observed market source or frozen paired archive differs")
    variants = {row["name"]: row for row in source["result"].get("variants", [])}
    if set(variants) != {"no_shock", "common_only", "issuer_specific_only", "common_plus_issuer_specific"}:
        raise ValueError("balanced scenario source lacks the frozen 2x2 comparison")
    outcome_rows = variants["no_shock"].get("daily_asset_rows", [])
    if len(outcome_rows) != 5289:
        raise ValueError("no-shock outcome panel is incomplete")
    outcomes = {(row["stock_code"], row["trade_date"]): row["observed_return"] for row in outcome_rows}
    for variant in variants.values():
        panel = {(row["stock_code"], row["trade_date"]): row["observed_return"]
                 for row in variant.get("daily_asset_rows", [])}
        if panel != outcomes:
            raise ValueError("paired variants do not share the exact observed target panel")
    codes = source["result"]["sample"]["selected_stock_codes"]
    dates = sorted({day for _, day in outcomes})
    if len(codes) != 123 or len(dates) != 43:
        raise ValueError("frozen stock/date coverage differs")
    groups = _load_market(csv_path, market)
    panel = build_risk_panel(groups, codes, dates, WINDOW)
    if len(panel) != len(outcomes):
        raise ValueError("risk features do not cover every observed company-day")
    if any(not math.isclose(row["absolute_observed_return"], abs(outcomes[row["stock_code"], row["trade_date"]]),
                           rel_tol=0.0, abs_tol=1e-12) for row in panel):
        raise ValueError("lagged-feature outcome rows do not match the archived panel")
    train_dates = set(dates[:TRAIN_SESSIONS])
    train = [row for row in panel if row["trade_date"] in train_dates]
    test = [row for row in panel if row["trade_date"] not in train_dates]
    daily = _daily_panel(panel)
    return {
        "pipeline_version": VERSION,
        "purpose": "screen the as-of t-2 usefulness of volatility as a risk-state feature; no future observation enters features",
        "source_market_manifest": str(MARKET_MANIFEST.resolve()),
        "source_market_manifest_sha256": file_sha256(MARKET_MANIFEST),
        "source_market_daily_csv_sha256": file_sha256(csv_path),
        "paired_outcome_archive": str(BALANCED_SOURCE.resolve()),
        "paired_outcome_archive_sha256": file_sha256(BALANCED_SOURCE),
        "sample": {"companies": 123, "sessions": 43, "company_days": 5289,
                   "training_sessions": 30, "test_sessions": 13,
                   "training_company_days": len(train), "test_company_days": len(test),
                   "period": "2019-11-01 through 2019-12-31",
                   "selection_warning": "development panel selected retrospectively from 2020-01 Q&A activity and outcomes have already been examined"},
        "feature_contract": {"cutoff": "t-2 inclusive", "window_sessions": WINDOW,
                             "stock_feature": "sample standard deviation of the prior 20 adjusted stock returns",
                             "benchmark_feature": "sample standard deviation of the prior 20 CSI 300 returns",
                             "target": "absolute adjusted stock return on t; aggregated to cross-sectional mean by date when applicable"},
        "company_day_forecasts": _score_panel(train, test),
        "daily_risk_forecasts": _score_daily(daily, train_dates),
        "interpretation": "Only a chronological development screen. Any improvement is not independent validation, a directional prediction claim, or evidence of causal market impact.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    payload = {"result": result, "code_sha256": file_sha256(Path(__file__))}
    destination = args.output.resolve()
    if destination.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("volatility diagnostic output must be written under research_outputs")
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != payload:
            raise ValueError("archived volatility diagnostic differs from recomputation")
    else:
        if destination.exists():
            raise ValueError("choose a new volatility diagnostic output path")
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    print(json.dumps({"company_day_forecasts": result["company_day_forecasts"],
                      "daily_risk_forecasts": result["daily_risk_forecasts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
