"""Apply an explicit development-period screen to archived 2019 market paths."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .audit_synthetic_observed_returns import compare_pairs


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "research/configs/pre_wuhan_market_screen_policy_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_market_screen_2019_v1.json"
EXPECTED = {
    "pre_wuhan_market_calibration_2019_v1.json": "pre-wuhan-market-calibration-screen-v1",
    "pre_wuhan_price_tie_sensitivity_2019_v1.json": "pre-wuhan-price-tie-sensitivity-v1",
    "pre_wuhan_order_arrival_sensitivity_2019_v1.json": "pre-wuhan-order-arrival-sensitivity-v1",
    "pre_wuhan_strategy_inventory_sensitivity_2019_v1.json": "pre-wuhan-strategy-inventory-sensitivity-v1",
}


def _finite(value: object, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"invalid finite metric: {name}")
    return float(value)


def evaluate(comparison: dict, policy: dict, trace_verified: bool) -> dict:
    synthetic, observed = comparison["synthetic"], comparison["observed"]
    if comparison["pairs"] != policy["pairs_per_variant"]:
        raise ValueError("paired company-day count differs")
    std = _finite(synthetic["standard_deviation"], "synthetic standard deviation")
    observed_std = _finite(observed["standard_deviation"], "observed standard deviation")
    p95 = _finite(synthetic["p95_absolute_return"], "synthetic p95 absolute return")
    observed_p95 = _finite(observed["p95_absolute_return"], "observed p95 absolute return")
    if observed_std <= 0 or observed_p95 <= 0:
        raise ValueError("observed scale must be positive")
    volatility_ratio = std / observed_std
    p95_ratio = p95 / observed_p95
    zero_gap = abs(_finite(synthetic["zero_return_fraction"], "synthetic zero fraction")
                   - _finite(observed["zero_return_fraction"], "observed zero fraction"))
    mean_gap = abs(_finite(synthetic["mean"], "synthetic mean")
                   - _finite(observed["mean"], "observed mean"))
    correlation = comparison["return_correlation"]
    correlation = _finite(correlation, "same-day return correlation") if correlation is not None else None
    checks = {
        "volatility_scale": policy["volatility_ratio_min"] <= volatility_ratio <= policy["volatility_ratio_max"],
        "tail_scale": policy["p95_absolute_return_ratio_min"] <= p95_ratio <= policy["p95_absolute_return_ratio_max"],
        "zero_return_frequency": zero_gap <= policy["zero_return_fraction_gap_max"],
        "mean_return": mean_gap <= policy["mean_return_gap_max"],
        "same_day_alignment": correlation is not None
        and correlation >= policy["same_day_return_correlation_min"],
        "company_day_trace": trace_verified or not policy["require_company_day_trace_for_advancement"],
    }
    return {"metrics": {"volatility_ratio": volatility_ratio, "p95_absolute_return_ratio": p95_ratio,
                        "zero_return_fraction_gap": zero_gap, "mean_return_gap": mean_gap,
                        "same_day_return_correlation": correlation},
            "checks": checks, "development_screen_pass": all(checks.values())}


def _validate_policy(policy: dict) -> None:
    fields = {"policy_id", "period_start", "period_end", "companies", "sessions", "pairs_per_variant",
              "volatility_ratio_min", "volatility_ratio_max", "p95_absolute_return_ratio_min",
              "p95_absolute_return_ratio_max", "zero_return_fraction_gap_max", "mean_return_gap_max",
              "same_day_return_correlation_min", "require_company_day_trace_for_advancement", "archives"}
    if set(policy) != fields or policy["archives"] != list(EXPECTED):
        raise ValueError("frozen market screen policy differs")
    if (policy["period_start"] != "2019-11-01" or policy["period_end"] != "2019-12-31"
            or policy["companies"] != 123 or policy["sessions"] != 43
            or policy["pairs_per_variant"] != 5289
            or policy["require_company_day_trace_for_advancement"] is not True):
        raise ValueError("market screen sample rule differs")
    thresholds = {"volatility_ratio_min": 0.5, "volatility_ratio_max": 2.0,
                  "p95_absolute_return_ratio_min": 0.5, "p95_absolute_return_ratio_max": 2.0,
                  "zero_return_fraction_gap_max": 0.1, "mean_return_gap_max": 0.005,
                  "same_day_return_correlation_min": 0.2}
    if policy["policy_id"] != "pre_wuhan_market_development_screen_v1" or any(
            type(policy[key]) not in (int, float) or policy[key] != value
            for key, value in thresholds.items()):
        raise ValueError("frozen market screen thresholds differ")
    ranges = (("volatility_ratio_min", "volatility_ratio_max"),
              ("p95_absolute_return_ratio_min", "p95_absolute_return_ratio_max"))
    for lo, hi in ranges:
        if not 0 < _finite(policy[lo], lo) <= _finite(policy[hi], hi):
            raise ValueError("invalid market screen ratio bounds")
    for field in ("zero_return_fraction_gap_max", "mean_return_gap_max",
                  "same_day_return_correlation_min"):
        if not 0 <= _finite(policy[field], field) <= 1:
            raise ValueError("invalid market screen tolerance")


def _validated_archive(path: Path, expected_pipeline: str, policy: dict) -> tuple[dict, dict]:
    archived = json.loads(path.read_text(encoding="utf-8"))
    if set(archived) != {"result", "input_sha256", "code_sha256"}:
        raise ValueError("unexpected development archive fields")
    for group in ("input_sha256", "code_sha256"):
        if not archived[group]:
            raise ValueError("empty archive provenance bindings")
        for filename, digest in archived[group].items():
            if file_sha256(Path(filename)) != digest:
                raise ValueError(f"development archive source changed: {filename}")
    result = archived["result"]
    sample = result["sample"]
    if (result["pipeline_version"] != expected_pipeline
            or result["development_period"] != {"start": policy["period_start"],
                                                 "end": policy["period_end"]}
            or sample["selected_companies"] != policy["companies"]
            or sample["sessions"] != policy["sessions"]
            or len(sample["selected_stock_codes"]) != policy["companies"]
            or len(set(sample["selected_stock_codes"])) != policy["companies"]):
        raise ValueError("development archive sample differs from screen policy")
    return result, {"archive_sha256": file_sha256(path), "market_dataset_id": result["market_dataset_id"],
                    "stock_codes": sample["selected_stock_codes"]}


def _verify_daily_rows(variant: dict, codes: list[str], policy: dict) -> bool:
    rows = variant.get("daily_asset_rows")
    if rows is None:
        return False
    if len(rows) != policy["pairs_per_variant"]:
        raise ValueError("daily company-day count differs")
    keys = [(row["stock_code"], row["trade_date"]) for row in rows]
    if (len(set(keys)) != len(keys) or set(code for code, _ in keys) != set(codes)
            or len(set(day for _, day in keys)) != policy["sessions"]):
        raise ValueError("daily company-day panel is incomplete or duplicated")
    pairs = []
    for row in rows:
        before, after = row["price_before_minor"], row["price_after_minor"]
        if type(before) is not int or type(after) is not int or before <= 0 or after <= 0:
            raise ValueError("invalid simulated price in daily row")
        pairs.append((after / before - 1, row["observed_return"]))
        if sum(flow["requested_net"] for flow in row["strategy_role_flow"].values()) != row["strategy_requested_net"]:
            raise ValueError("strategy role requested flow does not balance")
        if sum(flow["accepted_net"] for flow in row["strategy_role_flow"].values()) != row["strategy_net"]:
            raise ValueError("strategy role accepted flow does not balance")
        if sum(flow["filled_net"] for flow in row["strategy_role_flow"].values()) != row["strategy_filled_net"]:
            raise ValueError("strategy role filled flow does not balance")
    if compare_pairs(pairs) != variant["comparison"]:
        raise ValueError("daily rows do not reproduce paired return metrics")
    return True


def compute(policy_path: Path = POLICY) -> dict:
    if policy_path.resolve() != POLICY.resolve():
        raise ValueError("use the frozen 2019 market screen policy")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    _validate_policy(policy)
    variants, binding = [], {}
    common_market, common_codes, common_observed = None, None, None
    for archive_name, pipeline in EXPECTED.items():
        path = ROOT / "research_outputs" / archive_name
        result, provenance = _validated_archive(path, pipeline, policy)
        binding[archive_name] = provenance["archive_sha256"]
        if common_market is None:
            common_market, common_codes = provenance["market_dataset_id"], provenance["stock_codes"]
        elif (provenance["market_dataset_id"] != common_market
              or provenance["stock_codes"] != common_codes):
            raise ValueError("development cohorts or market sources differ")
        for variant in result["variants"]:
            comparison = variant["comparison"]
            if common_observed is None:
                common_observed = comparison["observed"]
            elif comparison["observed"] != common_observed:
                raise ValueError("observed reference differs across variants")
            trace = _verify_daily_rows(variant, common_codes, policy)
            verdict = evaluate(comparison, policy, trace)
            variants.append({"archive": archive_name, "variant": variant["name"],
                             "price_tie_break": variant["price_tie_break"],
                             "background_mode": variant["background_mode"],
                             "trace_verified": trace, **verdict})
    if len(variants) != 12:
        raise ValueError("market screen expected exactly 12 mechanism variants")
    return {"pipeline_version": "pre-wuhan-market-development-screen-v1",
            "policy_id": policy["policy_id"], "policy_sha256": file_sha256(policy_path),
            "audit_code_sha256": file_sha256(Path(__file__)),
            "archive_sha256": binding, "market_dataset_id": common_market,
            "development_period": {"start": policy["period_start"], "end": policy["period_end"]},
            "observed_reference": common_observed, "variants": variants,
            "passing_variants": [f"{v['archive']}:{v['variant']}" for v in variants
                                 if v["development_screen_pass"]],
            "interpretation": "Post-hoc diagnostic tolerances set after development outcomes were inspected. A pass would only justify seeking independent validation, not prove market calibration, investor behavior, price formation or event prediction. This retrospectively selected 2019 cohort and previously inspected Wuhan paths are not blind holdouts."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    destination = args.output.resolve()
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != result:
            raise ValueError("market screen archive differs from recomputation")
    else:
        if destination.exists() or destination.parent != OUTPUT.parent.resolve():
            raise ValueError("market screen output must be a new research_outputs file")
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    print(json.dumps({"variants": len(result["variants"]),
                      "passing_variants": result["passing_variants"],
                      "failures_by_check": {name: sum(not row["checks"][name] for row in result["variants"])
                                            for name in result["variants"][0]["checks"]}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
