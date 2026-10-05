"""Audit whether an as-of market signal contains the missing 2019 co-movement.

The same-day market return is a retrospective comparator only. It must never
enter an Agent observation or advance a simulated price.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from . import screen_pre_wuhan_market_2019 as screen
from .historical_replay import load_config, prepare_steps


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "research_outputs/pre_wuhan_order_arrival_sensitivity_2019_v1.json"
MARKET_MANIFEST = ROOT / "research_outputs/wuhan_pit_market_prepared_2020_v2/market_manifest.json"
REPLAY_CONFIG = ROOT / "research/configs/wuhan_pre_event_pit_replay_2020.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_lagged_information_2019_v1.json"


def pearson(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 3:
        raise ValueError("correlation requires at least three paired observations")
    if any(not math.isfinite(value) for value in left + right):
        raise ValueError("correlation inputs must be finite")
    a, b = sum(left) / len(left), sum(right) / len(right)
    numerator = sum((x - a) * (y - b) for x, y in zip(left, right, strict=True))
    denominator = math.sqrt(sum((x - a) ** 2 for x in left)
                            * sum((y - b) ** 2 for y in right))
    if denominator == 0:
        raise ValueError("correlation input has no variation")
    return numerator / denominator


def summarize(rows: list[dict], *, companies: int, sessions: int) -> dict:
    if len(rows) != companies * sessions:
        raise ValueError("company-day panel size differs")
    by_day: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for row in rows:
        key = (row["stock_code"], row["trade_date"])
        if key in seen or not row["signal_cutoff_date"] < row["execution_reference_date"] < row["trade_date"]:
            raise ValueError("duplicated company-day or future information")
        seen.add(key)
        by_day[row["trade_date"]].append(row)
    if len(by_day) != sessions or len({code for code, _ in seen}) != companies:
        raise ValueError("company-day grid differs")
    daily = []
    for day in sorted(by_day):
        group = by_day[day]
        if len(group) != companies or len({row["stock_code"] for row in group}) != companies:
            raise ValueError("daily company coverage differs")
        # The benchmark and its strictly lagged signal must be common to all
        # companies on the same session; do not count their repetitions as
        # independent observations when describing market-wide variation.
        signal, benchmark = group[0]["lagged_market_signal"], group[0]["same_day_market_return"]
        if any(not math.isclose(row["lagged_market_signal"], signal, abs_tol=1e-12)
               or not math.isclose(row["same_day_market_return"], benchmark, abs_tol=1e-12)
               for row in group):
            raise ValueError("benchmark differs across companies on the same day")
        daily.append({"trade_date": day, "lagged_market_signal": signal,
                      "same_day_market_return": benchmark,
                      "mean_stock_return": sum(row["stock_return"] for row in group) / companies})
    return {
        "company_days": len(rows), "companies": companies, "sessions": sessions,
        "pooled_company_day_correlations": {
            "lagged_signal_vs_stock_return": pearson(
                [row["lagged_market_signal"] for row in rows], [row["stock_return"] for row in rows]),
            "same_day_market_vs_stock_return_oracle": pearson(
                [row["same_day_market_return"] for row in rows], [row["stock_return"] for row in rows]),
            "lagged_signal_vs_same_day_market": pearson(
                [row["lagged_market_signal"] for row in rows],
                [row["same_day_market_return"] for row in rows]),
        },
        "daily_market_correlations": {
            "lagged_signal_vs_mean_stock_return": pearson(
                [row["lagged_market_signal"] for row in daily],
                [row["mean_stock_return"] for row in daily]),
            "same_day_market_vs_mean_stock_return_oracle": pearson(
                [row["same_day_market_return"] for row in daily],
                [row["mean_stock_return"] for row in daily]),
            "lagged_signal_vs_same_day_market": pearson(
                [row["lagged_market_signal"] for row in daily],
                [row["same_day_market_return"] for row in daily]),
        },
    }


def compute() -> dict:
    policy = json.loads(screen.POLICY.read_text(encoding="utf-8"))
    screen._validate_policy(policy)
    result, provenance = screen._validated_archive(
        ARCHIVE, "pre-wuhan-order-arrival-sensitivity-v1", policy)
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    csv_path = MARKET_MANIFEST.parent / "market_daily.csv"
    replay = load_config(REPLAY_CONFIG)
    if (market["market_dataset_id"] != result["market_dataset_id"]
            or market["data_kind"] != "observed"
            or market["artifacts"]["market_daily.csv"]["sha256"] != file_sha256(csv_path)
            or (REPLAY_CONFIG.parent / replay["market_manifest"]).resolve()
            != MARKET_MANIFEST.resolve()
            or set(provenance["stock_codes"]) - set(replay["stock_codes"])):
        raise ValueError("development market or replay provenance differs")
    groups = _load_market(csv_path, market)
    candidates = [row for row in result["variants"] if row["name"] == "stochastic_nearest"]
    if len(candidates) != 1:
        raise ValueError("expected exactly one frozen stochastic-nearest trace")
    variant = candidates[0]
    screen._verify_daily_rows(variant, provenance["stock_codes"], policy)
    archived = {(row["stock_code"], row["trade_date"]): row for row in variant["daily_asset_rows"]}
    if len(archived) != policy["pairs_per_variant"]:
        raise ValueError("archived company-day identities differ")
    rows = []
    for code in provenance["stock_codes"]:
        observations = {row.trade_date.isoformat(): row for row in groups[code]}
        steps = prepare_steps(groups[code], policy["period_start"], policy["period_end"],
                              replay["momentum_sessions"], replay["volatility_sessions"])
        if len(steps) != policy["sessions"]:
            raise ValueError("lagged market step coverage differs")
        for step in steps:
            day = step["trade_date"]
            prior = archived[(code, day)]
            actual = observations[day]
            if (step["signal_cutoff_date"] != prior["signal_cutoff_date"]
                    or not step["signal_cutoff_date"] < step["execution_reference_date"] < day
                    or not math.isclose(actual.stock_return, prior["observed_return"], abs_tol=1e-12)):
                raise ValueError("as-of dates or observed returns differ from the archived trace")
            rows.append({"stock_code": code, "trade_date": day,
                         "signal_cutoff_date": step["signal_cutoff_date"],
                         "execution_reference_date": step["execution_reference_date"],
                         "lagged_market_signal": step["market_signal"],
                         "same_day_market_return": actual.market_return,
                         "stock_return": actual.stock_return})
    if set(archived) != {(row["stock_code"], row["trade_date"]) for row in rows}:
        raise ValueError("prepared signal panel differs from the archived simulation panel")
    return {
        "pipeline_version": "pre-wuhan-lagged-information-audit-v1",
        "development_period": result["development_period"],
        "market_dataset_id": result["market_dataset_id"],
        "source_sha256": {
            "simulation_archive": file_sha256(ARCHIVE),
            "market_manifest": file_sha256(MARKET_MANIFEST),
            "market_daily": file_sha256(csv_path),
            "replay_config": file_sha256(REPLAY_CONFIG),
            "historical_replay_code": file_sha256(Path(prepare_steps.__code__.co_filename)),
            "screen_code": file_sha256(Path(screen.__file__)),
            "audit_code": file_sha256(Path(__file__)),
        },
        "information_rule": "The lagged market signal uses observed benchmark returns only through trade_date t-2; the same-day market return at t is retrospective and ineligible as an Agent input.",
        "summary": summarize(rows, companies=policy["companies"], sessions=policy["sessions"]),
        "interpretation": "Development-sample descriptive correlations, not out-of-sample predictive performance or a calibrated historical simulation. A same-day market-return comparator is an unavailable oracle, never an input. Company-days sharing a session are not independent market observations.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("lagged-information archive differs from recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("lagged-information audit output already exists")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
