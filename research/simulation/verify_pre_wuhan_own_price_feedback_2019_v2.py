"""Reopen full ledgers, independently verify orders/resources and joint statistics."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .audit_own_price_feedback import (verify_arrival, verify_final_resources, verify_same_state, verify_specs)
from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs, independent_covariance
from .audit_quantity_controls import verify_quantity_day
from .portfolio_audit import audit_portfolio_day
from .semantic_memory_sensitivity import canonical_hash

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_own_price_feedback_2019_v2"
CONFIG = ROOT / "research/configs/pre_wuhan_own_price_feedback_2019_v2.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_own_price_feedback_statistics_2019_v2.json"
VERSION = "pre-wuhan-own-price-feedback-independent-ledger-statistics-v2"


def independent_issuer(source, seed):
    p = {**source["parameters"], "innovation_seed": seed["innovation_seed"], "information_seed": seed["information_seed"]}
    stocks = {}
    for stock, rows in source["by_stock"].items():
        values = []
        for row in rows:
            feature = {key: row[key] for key in ("trade_date", "signal_cutoff_date", "history", "history_sha256", "lagged_stock_volatility")}
            digest = hashlib.sha256(f"issuer-innovation-v1:{p['innovation_seed']}:{stock}:{row['trade_date']}".encode()).hexdigest()
            innovation = math.sqrt(3) * (2 * ((int(digest[:16], 16) + 0.5) / 2**64) - 1)
            raw = innovation * p["risk_multiplier"] * feature["lagged_stock_volatility"] * 10000
            shift = min(p["max_shift_bps"], max(-p["max_shift_bps"], raw))
            values.append({**feature, "innovation_sha256": digest, "standardized_innovation": innovation,
                           "raw_shift_bps": raw, "valuation_shift_bps": shift, "was_capped": shift != raw})
        stocks[stock] = values
    return {"scenario_id": source["scenario_id"], "parameters": p, "by_stock": stocks}


def mean(values):
    return math.fsum(values) / len(values)


def std(values):
    center = mean(values)
    return math.sqrt(math.fsum((value - center) ** 2 for value in values) / len(values))


def pearson(left, right):
    a, b = mean(left), mean(right)
    x, y = [value - a for value in left], [value - b for value in right]
    denominator = math.sqrt(math.fsum(value * value for value in x) * math.fsum(value * value for value in y))
    return math.fsum(v * w for v, w in zip(x, y, strict=True)) / denominator if denominator else None


def description(values):
    magnitudes = sorted(abs(value) for value in values)
    return {"mean": mean(values), "standard_deviation": std(values), "mean_absolute_return": mean(magnitudes),
            "p95_absolute_return": magnitudes[math.ceil(0.95 * len(values)) - 1],
            "zero_return_fraction": sum(value == 0 for value in values) / len(values)}


def independent_metrics(rows, codes, dates, limits):
    panel = {(row["stock_code"], row["trade_date"]): row for row in rows}
    if len(panel) != len(rows) or set(panel) != {(code, day) for code in codes for day in dates}:
        raise ValueError("independent metric panel incomplete/duplicate")
    synthetic = [row["price_after_minor"] / row["price_before_minor"] - 1 for row in rows]
    observed = [row["observed_return"] for row in rows]
    nonzero = [(a, b) for a, b in zip(synthetic, observed) if a != 0 and b != 0]
    comparison = {"pairs": len(rows), "synthetic": description(synthetic), "observed": description(observed),
                  "return_correlation": pearson(synthetic, observed), "both_nonzero_pairs": len(nonzero),
                  "same_sign_nonzero_fraction": sum((a > 0) == (b > 0) for a, b in nonzero) / len(nonzero) if nonzero else None}
    common = {}
    for field in ("synthetic", "observed"):
        series = {code: [panel[code, day]["observed_return"] if field == "observed"
                         else panel[code, day]["price_after_minor"] / panel[code, day]["price_before_minor"] - 1 for day in dates]
                  for code in sorted(codes)}
        pairs = [pearson(series[a], series[b]) for i, a in enumerate(sorted(codes)) for b in sorted(codes)[i + 1:]]
        defined = [value for value in pairs if value is not None]
        daily = [mean([series[code][i] for code in codes]) for i in range(len(dates))]
        r2 = []
        for code in codes:
            factor = [mean([series[other][i] for other in codes if other != code]) for i in range(len(dates))]
            corr = pearson(series[code], factor)
            if corr is not None: r2.append(corr * corr)
        flat = [value for values in series.values() for value in values]
        common[field] = {"stock_count": len(codes), "sessions": len(dates), "stock_date_rows": len(rows),
                         "mean_stock_return_std": mean([std(values) for values in series.values()]),
                         "equal_weight_daily_return_std": std(daily),
                         "mean_pairwise_stock_return_correlation": mean(defined) if defined else None,
                         "pairwise_correlations_defined": len(defined),
                         "leave_one_stock_out_market_factor_mean_r2": mean(r2) if r2 else None,
                         "leave_one_stock_out_market_factor_median_r2": statistics.median(r2) if r2 else None,
                         "leave_one_stock_out_r2_defined_stocks": len(r2),
                         "zero_return_fraction": sum(value == 0 for value in flat) / len(flat)}
    a, o = comparison["synthetic"], comparison["observed"]
    ac, oc = common["synthetic"], common["observed"]
    values = {"absolute_mean_gap": abs(a["mean"] - o["mean"]),
              "volatility_ratio": a["standard_deviation"] / o["standard_deviation"],
              "zero_fraction_gap": abs(a["zero_return_fraction"] - o["zero_return_fraction"]),
              "stock_correlation_gap": abs(ac["mean_pairwise_stock_return_correlation"] - oc["mean_pairwise_stock_return_correlation"])
              if ac["mean_pairwise_stock_return_correlation"] is not None else None,
              "portfolio_volatility_ratio": ac["equal_weight_daily_return_std"] / oc["equal_weight_daily_return_std"]}
    criteria = {"mean": values["absolute_mean_gap"] <= limits["mean_return_gap_max"],
                "volatility": limits["volatility_ratio_min"] <= values["volatility_ratio"] <= limits["volatility_ratio_max"],
                "zero_fraction": values["zero_fraction_gap"] <= limits["zero_return_fraction_gap_max"],
                "stock_correlation": values["stock_correlation_gap"] is not None and values["stock_correlation_gap"] <= limits["stock_correlation_gap_max"],
                "portfolio_volatility": limits["portfolio_volatility_ratio_min"] <= values["portfolio_volatility_ratio"] <= limits["portfolio_volatility_ratio_max"]}
    return {"comparison": comparison, "common_factor_metrics": common,
            "joint_values": values, "joint_criteria": criteria, "all_five_pass": all(criteria.values())}


def compute():
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    bindings = {str(SOURCE / "manifest.json"): file_sha256(SOURCE / "manifest.json")}
    for key in ("inputs", "code_sha256"):
        for name, expected in manifest[key].items():
            if name in bindings and bindings[name] != expected: raise ValueError("conflicting independent bindings")
            bindings[name] = expected
    for name, info in manifest["artifacts"].items(): bindings[str(SOURCE / name)] = info["sha256"]
    def check_bindings():
        for name, expected in bindings.items():
            if file_sha256(Path(name)) != expected: raise ValueError("feedback source, algorithm or artifact changed")
    check_bindings()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    source = json.loads((ROOT / "research_outputs/pre_wuhan_quantity_controls_2019_v1/results.json").read_text(encoding="utf-8"))["result"]
    base = json.loads((ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json").read_text(encoding="utf-8"))
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    codes = result["sample"]["selected_stock_codes"]
    observed_rows = next(row for row in source["variants"] if row["name"] == "original_quantity_reference")["daily_asset_rows"]
    observed = {(row["stock_code"], row["trade_date"]): row["observed_return"] for row in observed_rows}
    dates = sorted({row["trade_date"] for row in observed_rows})
    if len(codes) != 123 or len(dates) != 43 or len(observed) != 5289: raise ValueError("source reference coverage changed")
    source_names = {"initial_inventory": "original_quantity_reference", "current_inventory": "background_current_anchor"}
    source_paths = {(row["variant"], row["basket_index"]): row for row in source["path_summaries"]}
    saved_paths = {(row["seed_id"], row["variant"], row["basket_index"]): row for row in result["path_summaries"]}
    saved_variants = {(row["seed_id"], row["name"]): row for row in result["variants"]}
    keys = {(seed["seed_id"], cell["name"]) for seed in cfg["seeds"] for cell in cfg["variants"]}
    if len(saved_paths) != 1230 or set(saved_variants) != keys or len(result["variants"]) != 30:
        raise ValueError("independent feedback grid differs")
    issuer_by_seed = {seed["seed_id"]: independent_issuer(source["issuer_design"], seed) for seed in cfg["seeds"]}
    panels, same_groups, checks, maximum = {key: [] for key in keys}, {}, Counter(), 0.0
    def close(actual, expected):
        nonlocal maximum
        if isinstance(expected, dict):
            if set(actual) != set(expected): raise ValueError("independent statistic fields differ")
            for key in expected: close(actual[key], expected[key])
        elif expected is None or type(expected) is bool:
            if actual != expected or type(actual) is not type(expected): raise ValueError("independent flag/undefined statistic differs")
        else:
            if type(actual) not in (int, float) or not math.isfinite(actual): raise ValueError("invalid recorded statistic")
            delta = abs(actual - expected); maximum = max(maximum, delta); checks["statistic_scalars_checked"] += 1
            if delta > 1e-10: raise ValueError("independent statistic differs")
    with gzip.open(SOURCE / "ledger.jsonl.gz", "rt", encoding="utf-8") as ledger, \
         gzip.open(SOURCE / "same_state_orders.jsonl.gz", "rt", encoding="utf-8") as same_file:
        for index, line in enumerate(ledger):
            if index >= 52890: raise ValueError("extra full path record")
            day = json.loads(line)
            seed = cfg["seeds"][index // (6 * 1763)]
            cell = cfg["variants"][(index // 1763) % 6]
            basket_index, session = (index % 1763) // 43, index % 43
            basket = codes[3 * basket_index:3 * basket_index + 3]
            key = seed["seed_id"], cell["name"]
            if (day["seed_id"], day["variant"], day["basket_index"], day["trade_date"], day["portfolio_auction"]["session"]) != (*key, basket_index, dates[session], session):
                raise ValueError("raw full-ledger identity/order differs")
            path = saved_paths[*key, basket_index]
            specs = path["participant_specs"]
            core = {**base["core"], "seed": seed["arrival_seed"]}
            background = {**base["background"], "seed": seed["background_seed"]}
            anchor, scale = cell["background_anchor"], cell["scale"]
            controls = None if anchor == "initial_inventory" else {"background_anchor": anchor, "strategy_wait": "original"}
            if session == 0:
                verify_specs(specs, source_paths[source_names[anchor], basket_index]["participant_specs"], scale)
                state = initial_from_specs(specs, basket, {**base, "core": core, "background": background}, case)
                histories = {stock: [] for stock in basket}
                streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
                issuer = {**issuer_by_seed[seed["seed_id"]], "by_stock": {stock: issuer_by_seed[seed["seed_id"]]["by_stock"][stock] for stock in basket}}
                industry = {**source["shared_design"]["industry"],
                            "membership": {stock: source["shared_design"]["industry"]["membership"][stock] for stock in basket},
                            "path_groups": {stock: source["shared_design"]["industry"]["path_groups"][stock] for stock in basket}}
                shocks = {"scenario_id": "common_component", "common": source["shared_design"]["common"],
                          "asset_specific": {stock: [0.0] * 43 for stock in basket}}
            verify_arrival(day, specs, core, session)
            independent_covariance(day, histories, base["feedback_parameters"])
            if scale == 1:
                for counter_scale in (0.5, 0.0):
                    row = json.loads(next(same_file))
                    if (row["seed_id"], row["background_anchor"], row["basket_index"], row["session"], row["scale"]) != (seed["seed_id"], anchor, basket_index, session, counter_scale):
                        raise ValueError("same-state raw identity/order differs")
                    if row["prior_state_sha256"] != canonical_hash(state) or row["prior_streaks_sha256"] != canonical_hash(streaks):
                        raise ValueError("same-state factual resource/streak hash differs")
                    payload = {field: row[field] for field in ("decisions", "orders")}
                    verify_same_state(payload, day, state, specs, base["venue"], background, source["background_response"],
                                      shocks, industry, issuer, controls, case, streaks, session, counter_scale)
                    group_key = seed["seed_id"] + ":" + anchor + ":" + str(counter_scale)
                    group = same_groups.setdefault(group_key, {"seed_id": seed["seed_id"], "background_anchor": anchor,
                                                              "scale": counter_scale, "records": 0, "requested_buy": 0, "requested_sell": 0})
                    group["records"] += 1
                    for orders in payload["orders"].values():
                        for order in orders: group["requested_" + order["side"]] += order["quantity"]
                    checks["same_state_scaled_records_reaudited"] += 1
            counts = verify_quantity_day(day, state, specs, base["venue"], background, source["background_response"],
                                         shocks, industry, issuer, controls, case, streaks, session)
            checks.update(counts)
            for stock, call in day["portfolio_auction"]["asset_calls"].items():
                row = {"stock_code": stock, "trade_date": day["trade_date"], "signal_cutoff_date": day["signal_cutoff_date"],
                       "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                       "matched_volume": call["matched_volume"], "observed_return": observed[stock, day["trade_date"]]}
                panels[key].append(row); histories[stock].append(row)
            state = audit_portfolio_day(day, state, base["venue"], session, dense=session in cfg["dense_audit_sessions"])
            checks["raw_settlement_asset_calls"] += len(basket)
            checks["full_ledger_records_reaudited"] += 1
            if session == 42: verify_final_resources(state, path["summary"])
            if (index + 1) % 3000 == 0: print(f"Independent feedback ledger: {index + 1}/52890", flush=True)
        if checks["full_ledger_records_reaudited"] != 52890 or checks["same_state_scaled_records_reaudited"] != 35260 or next(same_file, None) is not None:
            raise ValueError("independent full/one-step ledger coverage incomplete")
    if same_groups != result["same_state_groups"]: raise ValueError("independent same-state quantity totals differ")
    independent = {}
    for key, rows in panels.items():
        saved = saved_variants[key]
        if saved["daily_asset_rows"] != rows: raise ValueError("raw auction vs archived daily panel differs")
        measured = independent_metrics(rows, codes, dates, cfg["joint_limits"])
        for field in ("comparison", "common_factor_metrics"): close(saved[field], measured[field])
        close(saved["joint_checks"]["values"], measured["joint_values"])
        close(saved["joint_checks"]["criteria"], measured["joint_criteria"])
        close(saved["joint_checks"]["all_five_pass"], measured["all_five_pass"])
        close(saved["total_matched_volume"], sum(row["matched_volume"] for row in rows))
        independent["|".join(key)] = measured
    check_bindings()
    return {"pipeline_version": VERSION, "status": "PASS", "agent_signal_enabled": False,
            "inputs": dict(sorted(bindings.items())), "code_sha256": {str(Path(__file__).resolve()): file_sha256(Path(__file__))},
            "checks": dict(checks), "maximum_statistic_absolute_difference": maximum,
            "independent_variant_metrics": independent, "same_state_groups": same_groups,
            "interpretation": "All raw paths and same-state orders independently reopened and reaudited. Statistics use separate fsum/population variance/Pearson arithmetic, without importing the producer metric engine. Source observed panel is the previously audited frozen quantity reference. Development mechanism checks are not unseen-data prediction, empirical investor calibration or a semantic/economic release."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    path = args.output.resolve()
    if path.parent != (ROOT / "research_outputs").resolve(): raise ValueError("independent feedback output must be in research_outputs")
    result = compute()
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if path.read_bytes() != payload: raise ValueError("independent feedback statistics differ byte-for-byte")
        print("Independent own-price feedback statistics rebuilt byte-identically.")
    else:
        with path.open("xb") as handle: handle.write(payload)
    print(json.dumps({"status": result["status"], "checks": result["checks"],
                      "maximum_statistic_absolute_difference": result["maximum_statistic_absolute_difference"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
