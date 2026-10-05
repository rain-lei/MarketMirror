"""Compare archived no-text synthetic returns with paired observed stock returns."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from ..data_pipeline.provenance import file_sha256


ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO = ROOT / "research_outputs/wuhan_pit_portfolio_no_text_2020_v2"
MARKET = ROOT / "research_outputs/wuhan_pit_market_prepared_2020_v2"
CASE = "separated_institution35"


def describe(values: list[float]) -> dict:
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("empty or non-finite return series")
    magnitudes = sorted(abs(value) for value in values)
    return {
        "mean": statistics.mean(values),
        "standard_deviation": statistics.pstdev(values),
        "mean_absolute_return": statistics.mean(magnitudes),
        "p95_absolute_return": magnitudes[math.ceil(.95 * len(magnitudes)) - 1],
        "zero_return_fraction": sum(value == 0 for value in values) / len(values),
    }


def compare_pairs(pairs: list[tuple[float, float]]) -> dict:
    if not pairs:
        raise ValueError("no paired company-days")
    synthetic, observed = map(list, zip(*pairs))
    synthetic_stats, observed_stats = describe(synthetic), describe(observed)
    covariance = statistics.mean((a - synthetic_stats["mean"]) *
                                 (b - observed_stats["mean"]) for a, b in pairs)
    denominator = synthetic_stats["standard_deviation"] * observed_stats["standard_deviation"]
    both_nonzero = [(a, b) for a, b in pairs if a != 0 and b != 0]
    return {
        "pairs": len(pairs),
        "synthetic": synthetic_stats,
        "observed": observed_stats,
        "return_correlation": covariance / denominator if denominator else None,
        "same_sign_nonzero_fraction": (sum((a > 0) == (b > 0) for a, b in both_nonzero) /
                                       len(both_nonzero) if both_nonzero else None),
        "both_nonzero_pairs": len(both_nonzero),
    }


def flat_price_tie(call: dict, tick_minor: int) -> bool:
    """Was an unchanged traded price tied with another price on volume/imbalance?"""
    before = call["price_before_minor"]
    if call["matched_volume"] <= 0 or before != call["price_after_minor"]:
        raise ValueError("tie diagnostic requires a flat traded call")
    lower, upper = call["price_bounds_minor"]
    accepted = [order for order in call["orders"] if order["accepted_quantity"] > 0]
    candidates = {before}
    for order in accepted:
        for offset in (-tick_minor, 0, tick_minor):
            candidate = order["limit_price_minor"] + offset
            if lower <= candidate <= upper:
                candidates.add(candidate)
    ranked = []
    for candidate in candidates:
        demand = sum(order["accepted_quantity"] for order in accepted
                     if order["side"] == "buy" and order["limit_price_minor"] >= candidate)
        supply = sum(order["accepted_quantity"] for order in accepted
                     if order["side"] == "sell" and order["limit_price_minor"] <= candidate)
        ranked.append((candidate, -min(demand, supply), abs(demand - supply)))
    best = min((volume_rank, imbalance_rank) for _, volume_rank, imbalance_rank in ranked)
    winners = {price for price, volume_rank, imbalance_rank in ranked
               if (volume_rank, imbalance_rank) == best}
    if before not in winners:
        raise ValueError("archived flat clearing price fails volume/imbalance ranking")
    return len(winners) > 1


def _check_artifacts(directory: Path, manifest_name: str, names: tuple[str, ...]) -> dict:
    manifest = json.loads((directory / manifest_name).read_text(encoding="utf-8"))
    for name in names:
        expected = manifest.get("artifacts", {}).get(name, {}).get("sha256")
        if expected != file_sha256(directory / name):
            raise ValueError(f"archived artifact hash differs: {name}")
    return manifest


def compute(portfolio: Path = PORTFOLIO, market: Path = MARKET) -> tuple[dict, dict]:
    portfolio, market = portfolio.resolve(), market.resolve()
    portfolio_files = ("portfolio_no_text_summary.json", "portfolio_no_text_paths.json",
                       "portfolio_no_text_ledger.jsonl.gz")
    portfolio_manifest = _check_artifacts(portfolio, "portfolio_no_text_manifest.json",
                                           portfolio_files)
    market_manifest = _check_artifacts(market, "market_manifest.json", ("market_daily.csv",))
    summary = json.loads((portfolio / portfolio_files[0]).read_text(encoding="utf-8"))
    paths = json.loads((portfolio / portfolio_files[1]).read_text(encoding="utf-8"))
    baseline_config = ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json"
    if (portfolio_manifest.get("inputs", {}).get(str(baseline_config.resolve()))
            != file_sha256(baseline_config)):
        raise ValueError("archived venue configuration hash differs")
    tick_minor = json.loads(baseline_config.read_text(encoding="utf-8"))["venue"]["tick_minor"]
    market_csv = market / "market_daily.csv"
    market_manifest_path = market / "market_manifest.json"
    inputs = portfolio_manifest.get("inputs", {})
    if (summary.get("pipeline_version") != "wuhan-pre-event-no-text-portfolio-v1"
            or summary.get("experiment_id") != portfolio_manifest.get("experiment_id")
            or summary.get("market_dataset_id") != market_manifest.get("market_dataset_id")
            or inputs.get(str(market_csv)) != file_sha256(market_csv)
            or inputs.get(str(market_manifest_path)) != file_sha256(market_manifest_path)
            or market_manifest.get("settings", {}).get("stock_return_basis") != "adjusted_price_return"):
        raise ValueError("portfolio and observed market source bindings differ")
    selected = {row["path_id"]: row for row in paths if row["case_id"] == CASE}
    if (len(selected) != len(summary["baskets"]) or
            {row["basket_index"] for row in selected.values()} != set(range(len(summary["baskets"]))) or
            any(row["assets"] != summary["baskets"][row["basket_index"]]
                or row["use_text"] is not False for row in selected.values())):
        raise ValueError("selected no-text case does not cover all baskets once")
    actual: dict[tuple[str, str], float] = {}
    with market_csv.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            key = row["stock_code"], row["trade_date"]
            if key in actual:
                raise ValueError("duplicate observed company-date")
            value = float(row["stock_return"])
            if not math.isfinite(value) or value <= -1:
                raise ValueError("invalid observed simple return")
            actual[key] = value
    pairs, coverage = [], Counter()
    seen, sessions = set(), Counter()
    date_min, date_max = None, None
    with gzip.open(portfolio / portfolio_files[2], "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            path_id = row["path_id"]
            if path_id not in selected:
                continue
            trade_date = row["trade_date"]
            date_min = min(date_min, trade_date) if date_min else trade_date
            date_max = max(date_max, trade_date) if date_max else trade_date
            sessions[path_id] += 1
            calls = row["portfolio_auction"]["asset_calls"]
            if set(calls) != set(selected[path_id]["assets"]):
                raise ValueError("asset-call coverage differs from basket")
            for code, call in calls.items():
                key = code, trade_date
                if key in seen:
                    raise ValueError("duplicate synthetic company-date")
                seen.add(key)
                coverage["synthetic_company_days"] += 1
                coverage["execution_unavailable"] += int(not call["execution_available"])
                coverage["zero_matched_volume"] += int(call["matched_volume"] == 0)
                before, after = call["price_before_minor"], call["price_after_minor"]
                if before <= 0 or after <= 0:
                    raise ValueError("invalid synthetic price")
                coverage["zero_synthetic_return"] += int(before == after)
                coverage["zero_return_with_trades"] += int(
                    before == after and call["matched_volume"] > 0)
                if before == after and call["matched_volume"] > 0:
                    coverage["flat_traded_price_ties"] += int(flat_price_tie(call, tick_minor))
                if key not in actual:
                    coverage["missing_observed_returns"] += 1
                    continue
                pairs.append((after / before - 1, actual[key]))
    if (set(sessions) != set(selected) or
            any(sessions[path_id] != path["sessions"] for path_id, path in selected.items()) or
            len(seen) != len(summary["baskets"]) * 3 * sessions[next(iter(sessions))] or
            (date_min, date_max) != (summary["dates"]["start"], summary["dates"]["end"])):
        raise ValueError("synthetic ledger date or path coverage differs")
    coverage["paired_company_days"] = len(pairs)
    coverage["companies"] = len({code for code, _ in seen})
    coverage["sessions"] = len({date for _, date in seen})
    comparison = compare_pairs(pairs)
    result = {"pipeline_version": "synthetic-observed-return-diagnostic-v1",
              "experiment_id": summary["experiment_id"],
              "market_dataset_id": summary["market_dataset_id"], "case": CASE,
              "period": {"start": date_min, "end": date_max},
              "coverage": dict(coverage), "comparison": comparison,
              "interpretation": "Descriptive same-company same-date comparison of a hypothetical no-text endogenous market with observed adjusted-price returns; not a forecast or fit test. Synthetic level, traded volume and investor identity are not observed-market calibrated."}
    bindings = {str(path): file_sha256(path) for path in
                (portfolio / "portfolio_no_text_manifest.json", baseline_config,
                 *(portfolio / name for name in portfolio_files),
                 market_manifest_path, market_csv)}
    return result, bindings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result, bindings = compute()
    if args.output:
        destination = args.output.resolve()
        if destination.exists():
            raise ValueError("output already exists")
        if destination.suffix != ".json":
            raise ValueError("output must be JSON")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"result": result, "input_sha256": bindings,
                                           "code_sha256": file_sha256(Path(__file__))},
                                          ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
