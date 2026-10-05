"""Explore whether lagged trading activity adds volatility information in 2019."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path

from ..data_pipeline.market_activity import DOCUMENTATION, REQUIRED_RAW, parse_activity_row
from ..data_pipeline.provenance import file_sha256
from ..baselines.run_experiments import _load_market
from . import screen_pre_wuhan_market_2019 as screen
from .historical_replay import load_config, prepare_steps

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_lagged_activity_2019.json"
ARCHIVE = ROOT / "research_outputs/pre_wuhan_order_arrival_sensitivity_2019_v1.json"
POLICY = ROOT / "research/configs/pre_wuhan_market_screen_policy_2019.json"
MARKET_MANIFEST = ROOT / "research_outputs/wuhan_pit_market_prepared_2020_v2/market_manifest.json"
DOWNLOAD_MANIFEST = ROOT / "research_outputs/wuhan_pit_market_download_2020/download_manifest.json"
REPLAY_CONFIG = ROOT / "research/configs/wuhan_pre_event_pit_replay_2020.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_lagged_activity_2019_v2.json"


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve a small positive-definite normal equation with pivoting."""
    size = len(vector)
    rows = [list(matrix[i]) + [vector[i]] for i in range(size)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(rows[row][col]))
        if abs(rows[pivot][col]) < 1e-12:
            raise ValueError("linear model is singular")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        rows[col] = [value / scale for value in rows[col]]
        for row in range(size):
            if row == col:
                continue
            factor = rows[row][col]
            rows[row] = [a - factor * b for a, b in zip(rows[row], rows[col], strict=True)]
    return [rows[row][-1] for row in range(size)]


def activity_surprise(amounts: list[float], cutoff_index: int, lookback: int) -> float:
    """Measure activity at the cutoff against the preceding sessions only."""
    if (type(cutoff_index) is not int or type(lookback) is not int or lookback < 2
            or cutoff_index < lookback or cutoff_index >= len(amounts)
            or any(not math.isfinite(value) or value < 0 for value in amounts)):
        raise ValueError("invalid lagged amount window")
    current = math.log1p(amounts[cutoff_index])
    baseline = statistics.median(math.log1p(value)
                                for value in amounts[cutoff_index - lookback:cutoff_index])
    return current - baseline


def fit_predict(train: list[dict], test: list[dict], features: list[str]) -> tuple[list[float], dict]:
    means = {name: statistics.mean(row[name] for row in train) for name in features}
    scales = {name: statistics.stdev(row[name] for row in train) for name in features}
    if any(not math.isfinite(value) or value <= 1e-12 for value in scales.values()):
        raise ValueError("training feature has no usable variation")
    width = len(features) + 1
    gram = [[0.0] * width for _ in range(width)]
    target = [0.0] * width
    for row in train:
        x = [1.0] + [(row[name] - means[name]) / scales[name] for name in features]
        for i, left in enumerate(x):
            target[i] += left * row["outcome"]
            for j, right in enumerate(x):
                gram[i][j] += left * right
    # A tiny fixed ridge stabilizes normal equations without penalizing intercept.
    for i in range(1, width):
        gram[i][i] += 1e-8
    coefficients = solve(gram, target)
    predictions = []
    for row in test:
        x = [1.0] + [(row[name] - means[name]) / scales[name] for name in features]
        predictions.append(sum(a * b for a, b in zip(coefficients, x, strict=True)))
    return predictions, {"features": features, "train_means": means, "train_scales": scales,
                         "standardized_coefficients": dict(zip(["intercept", *features], coefficients, strict=True))}


def metrics(rows: list[dict], predictions: list[float]) -> dict:
    errors = [pred - row["outcome"] for row, pred in zip(rows, predictions, strict=True)]
    mean_target = statistics.mean(row["outcome"] for row in rows)
    denominator = sum((row["outcome"] - mean_target) ** 2 for row in rows)
    by_day: dict[str, list[float]] = {}
    for row, error in zip(rows, errors, strict=True):
        by_day.setdefault(row["trade_date"], []).append(abs(error))
    return {"company_days": len(rows), "sessions": len(by_day),
            "mae": statistics.mean(abs(error) for error in errors),
            "rmse": math.sqrt(statistics.mean(error * error for error in errors)),
            "out_of_sample_r2": 1 - sum(error * error for error in errors) / denominator,
            "mean_session_mae": statistics.mean(statistics.mean(values) for values in by_day.values()),
            "session_mae": {day: statistics.mean(values) for day, values in sorted(by_day.items())}}


def _load_activity(codes: list[str], dates: list[str], download: dict) -> dict[tuple[str, str], float]:
    if (download.get("provider") != "BaoStock" or download.get("data_kind") != "observed"
            or download.get("documentation") != DOCUMENTATION
            or download.get("start_date", "") > dates[0]
            or download.get("end_date", "") < dates[-1]):
        raise ValueError("activity download identity or coverage differs")
    queries = [q for q in download["queries"] if q.get("function") == "query_history_k_data_plus"
               and q.get("symbol") != download.get("benchmark_symbol")]
    by_code = {q["symbol"].split(".")[1]: q for q in queries}
    if len(by_code) != len(queries) or not set(codes) <= set(by_code):
        raise ValueError("activity queries do not cover the research cohort")
    directory = DOWNLOAD_MANIFEST.parent
    expected_dates = set(dates)
    calendar_index = {day: index for index, day in enumerate(dates)}
    amounts = {}
    for code in codes:
        query = by_code[code]
        symbol = query["symbol"]
        path = directory / query["raw_file"]
        if (query.get("adjustflag") != "1" or query.get("frequency") != "d"
                or set(query.get("fields", "").split(",")) != REQUIRED_RAW
                or query["raw_file"] != symbol.replace(".", "_") + "_raw.csv"
                or file_sha256(path) != download["artifacts"][query["raw_file"]]["sha256"]):
            raise ValueError("raw activity query or file hash differs")
        seen = set()
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if set(reader.fieldnames or []) != REQUIRED_RAW:
                raise ValueError("raw activity columns differ")
            for raw in reader:
                if raw["date"] not in expected_dates:
                    continue
                parsed = parse_activity_row(raw, symbol)
                day = parsed["trade_date"]
                if day in seen:
                    raise ValueError("duplicate stock activity date")
                seen.add(day)
                amount = float(parsed["amount_cny"])
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError("invalid trading amount")
                amounts[(code, day)] = amount
        if not seen <= expected_dates or not seen or max(seen) != dates[-1]:
            raise ValueError("stock activity dates escape the market calendar or end early")
        positions = sorted(calendar_index[day] for day in seen)
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise ValueError("stock activity has a missing exchange session")
    return amounts


def compute() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if cfg != {"policy_id": "pre_wuhan_lagged_activity_increment_v1",
               "period_start": "2019-11-01", "period_end": "2019-12-31",
               "companies": 123, "sessions": 43, "training_sessions": 30,
               "test_sessions": 13, "activity_lookback_sessions": 20,
               "information_rule": "for return observation t, use activity through t-2; compare the t-2 log amount with the median log amount over the prior 20 sessions ending t-3",
               "outcome": "absolute_stock_return_on_t",
               "baseline_features": ["realized_volatility_through_t_minus_2", "absolute_market_momentum_through_t_minus_2"],
               "incremental_feature": "log_amount_t_minus_2_minus_median_log_amount_prior_20_sessions",
               "split_rule": "chronological first 30 sessions train, final 13 sessions test; no random row split"}:
        raise ValueError("frozen activity study config differs")
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    screen._validate_policy(policy)
    archived, provenance = screen._validated_archive(
        ARCHIVE, "pre-wuhan-order-arrival-sensitivity-v1", policy)
    codes = provenance["stock_codes"]
    if len(codes) != cfg["companies"]:
        raise ValueError("company count differs from frozen activity study")
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    market_csv = MARKET_MANIFEST.parent / "market_daily.csv"
    if (market.get("data_kind") != "observed"
            or file_sha256(market_csv) != market["artifacts"]["market_daily.csv"]["sha256"]
            or market["market_dataset_id"] != archived["market_dataset_id"]):
        raise ValueError("market panel differs from the frozen development archive")
    download = json.loads(DOWNLOAD_MANIFEST.read_text(encoding="utf-8"))
    replay = load_config(REPLAY_CONFIG)
    if ((REPLAY_CONFIG.parent / replay["market_manifest"]).resolve() != MARKET_MANIFEST.resolve()
            or not set(codes) <= set(replay["stock_codes"])
            or download.get("retrieved_at") is None):
        raise ValueError("replay/download provenance differs")
    dates = market["session_dates"]
    amounts = _load_activity(codes, dates, download)
    groups = _load_market(market_csv, market)
    variant = next((item for item in archived["variants"]
                    if item["name"] == "stochastic_nearest"), None)
    if variant is None:
        raise ValueError("frozen stochastic-nearest archive is missing")
    screen._verify_daily_rows(variant, codes, policy)
    observed_rows = {(row["stock_code"], row["trade_date"]): row
                     for row in variant["daily_asset_rows"]}
    by_code = {}
    for code in codes:
        observations = {row.trade_date.isoformat(): row for row in groups[code]}
        company_dates = sorted(session for stock, session in amounts if stock == code)
        company_index = {session: index for index, session in enumerate(company_dates)}
        company_amounts = [amounts[(code, session)] for session in company_dates]
        steps = prepare_steps(groups[code], cfg["period_start"], cfg["period_end"],
                              replay["momentum_sessions"], replay["volatility_sessions"])
        for step in steps:
            day, cutoff, execution = (step["trade_date"], step["signal_cutoff_date"],
                                      step["execution_reference_date"])
            target_index = dates.index(day)
            cutoff_index = company_index[day] - 2
            if (cutoff_index < cfg["activity_lookback_sessions"]
                    or company_dates[cutoff_index] != cutoff
                    or dates[target_index - 2] != cutoff or dates[target_index - 1] != execution):
                raise ValueError("activity cutoff/history is not strictly before execution")
            activity = activity_surprise(company_amounts, company_index[cutoff],
                                          cfg["activity_lookback_sessions"])
            archived_row = observed_rows.get((code, day))
            if (archived_row is None or archived_row["signal_cutoff_date"] != cutoff
                    or not math.isclose(archived_row["observed_return"], step["observed_return"], abs_tol=1e-12)):
                raise ValueError("outcome or information timestamp differs from archived replay")
            by_code.setdefault(day, []).append({
                "stock_code": code, "trade_date": day, "signal_cutoff_date": cutoff,
                "execution_reference_date": execution,
                "realized_volatility_through_t_minus_2": step["estimated_volatility"],
                "absolute_market_momentum_through_t_minus_2": abs(step["market_signal"]),
                "log_amount_t_minus_2_minus_median_log_amount_prior_20_sessions": activity,
                "outcome": abs(step["observed_return"]),
            })
    rows = [row for day in sorted(by_code) for row in by_code[day]]
    study_dates = sorted(by_code)
    if (len(study_dates) != cfg["sessions"] or any(len(by_code[day]) != cfg["companies"] for day in study_dates)
            or len(rows) != cfg["companies"] * cfg["sessions"]):
        raise ValueError("complete company-day panel differs")
    split_date = study_dates[cfg["training_sessions"]]
    train = [row for row in rows if row["trade_date"] < split_date]
    test = [row for row in rows if row["trade_date"] >= split_date]
    base_features = cfg["baseline_features"]
    extra = cfg["incremental_feature"]
    pred_base, model_base = fit_predict(train, test, base_features)
    pred_plus, model_plus = fit_predict(train, test, [*base_features, extra])
    return {
        "pipeline_version": "pre-wuhan-lagged-activity-audit-v2",
        "study_id": "frozen chronological incremental check of abnormal traded amount",
        "period": {"start": study_dates[0], "end": study_dates[-1],
                   "train_sessions": len({row["trade_date"] for row in train}),
                   "test_start": split_date,
                   "test_end": study_dates[-1],
                   "companies": len(codes), "company_days": len(rows)},
        "information_rule": cfg["information_rule"],
        "feature_definition": cfg["incremental_feature"],
        "training_sessions_only_fit": True,
        "models": {"baseline": {"fit": model_base, "test": metrics(test, pred_base)},
                   "baseline_plus_activity": {"fit": model_plus, "test": metrics(test, pred_plus)}},
        "source_vintage": {"provider": download["provider"],
                            "retrieved_at": download["retrieved_at"],
                            "point_in_time_archive": False},
        "source_sha256": {"study_config": file_sha256(CONFIG), "market_policy": file_sha256(POLICY),
                          "simulation_archive": file_sha256(ARCHIVE),
                          "market_manifest": file_sha256(MARKET_MANIFEST),
                          "market_daily": file_sha256(market_csv),
                          "download_manifest": file_sha256(DOWNLOAD_MANIFEST),
                          "replay_config": file_sha256(REPLAY_CONFIG),
                          "study_code": file_sha256(Path(__file__))},
        "interpretation": "Exploratory risk-context diagnostic only. Daily amount contains no buy/sell direction, order book depth, investor type or price-impact coefficient. The selected 2019 cohort was derived from 2020 Q&A activity; the provider data are a later retrieval vintage. A result here is not independent validation, causal evidence, or authorization to feed an Agent signal.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("lagged-activity archive differs from recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("lagged-activity archive exists; use --audit-existing or a new version")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    print(json.dumps({name: item["test"] for name, item in result["models"].items()},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
