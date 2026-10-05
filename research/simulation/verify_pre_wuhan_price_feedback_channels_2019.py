"""Reopen every factorial ledger, audit channels/settlement and independent stats."""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
import statistics

from ..data_pipeline.provenance import file_sha256
from .audit_own_price_feedback import strategy_orders, verify_arrival, verify_final_resources
from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs, independent_covariance
from .audit_price_feedback_channels import verify_day, verify_same_state, verify_specs
from .portfolio_audit import audit_portfolio_day
from .verify_pre_wuhan_own_price_feedback_2019_v2 import independent_issuer, independent_metrics

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_price_feedback_channels_2019.json"
SOURCE = ROOT / "research_outputs/pre_wuhan_price_feedback_channels_2019_v1"
LEGACY = ROOT / "research_outputs/pre_wuhan_own_price_feedback_2019_v2"
OUTPUT = ROOT / "research_outputs/pre_wuhan_price_feedback_channel_statistics_2019_v1.json"
VERSION = "pre-wuhan-target-quote-feedback-independent-ledger-statistics-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def legacy_record(stream, identity):
    for line in stream:
        row = json.loads(line)
        if row["variant"].endswith("_feedback_half"):
            continue
        actual = row["seed_id"], row["variant"], row["basket_index"], row["portfolio_auction"]["session"]
        if actual != identity:
            raise ValueError("independent legacy bridge identity/order differs")
        return {key: value for key, value in row.items() if key not in {"seed_id", "variant", "basket_index"}}
    raise ValueError("independent legacy source ended early")


def old_diagonal_day(day, scale):
    result = {key: value for key, value in day.items() if key not in {"seed_id", "variant", "basket_index"}}
    if scale != 1:
        result = {**result, "decisions": {name: dict(decision) for name, decision in day["decisions"].items()}}
        if result.pop("price_feedback_channels") != {"target_scale": scale, "quote_scale": scale}:
            raise ValueError("independent diagonal day metadata differs")
        result["own_price_feedback_scale"] = scale
        for decision in result["decisions"].values():
            if decision.pop("quote_base_beliefs") != decision["issuer_base_beliefs"]:
                raise ValueError("independent diagonal quote base differs")
    return result


def values(row):
    own, common = row["comparison"]["synthetic"], row["common_factor_metrics"]["synthetic"]
    return {"mean": own["mean"], "volatility": own["standard_deviation"], "zero_fraction": own["zero_return_fraction"],
            "stock_correlation": common["mean_pairwise_stock_return_correlation"],
            "portfolio_volatility": common["equal_weight_daily_return_std"]}


def distances(row):
    v = row["joint_values"]
    return {"mean": v["absolute_mean_gap"], "volatility": abs(v["volatility_ratio"] - 1),
            "zero_fraction": v["zero_fraction_gap"], "stock_correlation": v["stock_correlation_gap"],
            "portfolio_volatility": abs(v["portfolio_volatility_ratio"] - 1)}


def aggregate(seeds, cells, metrics):
    summaries, factorial = {}, {}
    for cell in cells:
        name = cell["name"]
        rows = [metrics[seed + "|" + name] for seed in seeds]
        measures = {key: [values(row)[key] for row in rows] for key in values(rows[0])}
        changes = []
        for seed, row in zip(seeds, rows):
            a, b = distances(row), distances(metrics[seed + "|" + cell["background_anchor"] + "_feedback_full"])
            delta = {key: a[key] - b[key] if a[key] is not None and b[key] is not None else None for key in a}
            changes.append({"seed_id": seed, "gap_changes": delta,
                            "all_five_nonworse": all(v is not None and v <= 1e-12 for v in delta.values())})
        summaries[name] = {"seed_count": len(rows), "all_five_pass_seeds": sum(row["all_five_pass"] for row in rows),
                           "metrics": {key: {"min": min(v for v in data if v is not None),
                                             "median": statistics.median(v for v in data if v is not None),
                                             "max": max(v for v in data if v is not None)}
                                       if any(v is not None for v in data) else {"min": None, "median": None, "max": None}
                                       for key, data in measures.items()},
                           "paired_changes": changes, "all_five_nonworse_seeds": sum(row["all_five_nonworse"] for row in changes)}
    for anchor in ("initial_inventory", "current_inventory"):
        factorial[anchor] = []
        for seed in seeds:
            base, target, quote, both = [values(metrics[seed + "|" + anchor + "_feedback_" + label])
                                        for label in ("full", "target_off", "quote_off", "both_off")]
            differences = {label: {} for label in ("target_off_minus_baseline", "quote_off_minus_baseline", "both_off_minus_baseline", "interaction")}
            for key in base:
                if any(row[key] is None for row in (base, target, quote, both)):
                    for row in differences.values(): row[key] = None
                else:
                    differences["target_off_minus_baseline"][key] = target[key] - base[key]
                    differences["quote_off_minus_baseline"][key] = quote[key] - base[key]
                    differences["both_off_minus_baseline"][key] = both[key] - base[key]
                    differences["interaction"][key] = (both[key] + base[key]) - (target[key] + quote[key])
            factorial[anchor].append({"seed_id": seed, **differences})
    return summaries, factorial


def compute():
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    bindings = {str(SOURCE / "manifest.json"): file_sha256(SOURCE / "manifest.json")}
    for field in ("inputs", "code_sha256"):
        for name, expected in manifest[field].items():
            if name in bindings and bindings[name] != expected:
                raise ValueError("conflicting channel bindings")
            bindings[name] = expected
    for name, info in manifest["artifacts"].items():
        bindings[str(SOURCE / name)] = info["sha256"]
    def check_bindings():
        for name, expected in bindings.items():
            if file_sha256(Path(name)) != expected:
                raise ValueError("channel source/algorithm/outputs changed: " + name)
    check_bindings()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    old_result = json.loads((LEGACY / "results.json").read_text(encoding="utf-8"))["result"]
    quantity = json.loads((ROOT / "research_outputs/pre_wuhan_quantity_controls_2019_v1/results.json").read_text(encoding="utf-8"))["result"]
    base = json.loads((ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json").read_text(encoding="utf-8"))
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    codes = result["sample"]["selected_stock_codes"]
    observed_rows = next(row for row in quantity["variants"] if row["name"] == "original_quantity_reference")["daily_asset_rows"]
    observed = {(row["stock_code"], row["trade_date"]): row["observed_return"] for row in observed_rows}
    dates = sorted({row["trade_date"] for row in observed_rows})
    with gzip.open(LEGACY / "ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
        clocks = {row["trade_date"]: (row["signal_cutoff_date"], row["execution_reference_date"])
                  for row in (json.loads(next(stream)) for _ in dates)}
    seeds, cells = cfg["seeds"], cfg["variants"]
    keys = {(seed["seed_id"], cell["name"]) for seed in seeds for cell in cells}
    paths = {(row["seed_id"], row["variant"], row["basket_index"]): row for row in result["path_summaries"]}
    expected_paths = {(*key, basket) for key in keys for basket in range(41)}
    variants = {(row["seed_id"], row["name"]): row for row in result["variants"]}
    if (len(codes) != 123 or len(set(codes)) != 123 or len(dates) != 43 or len(observed) != 5289
            or len(keys) != 40 or len(result["variants"]) != 40 or set(variants) != keys
            or len(result["path_summaries"]) != 1640 or set(paths) != expected_paths):
        raise ValueError("independent full channel grid/cohort differs")
    old_paths = {(row["seed_id"], row["variant"], row["basket_index"]): row for row in old_result["path_summaries"]}
    issuer_by_seed = {seed["seed_id"]: independent_issuer(quantity["issuer_design"], seed) for seed in seeds}
    panels, branches, flows, coverage = {key: [] for key in keys}, {}, {}, {}
    groups, checks, maximum = {}, Counter(), 0.0
    def close(actual, expected):
        nonlocal maximum
        if isinstance(expected, dict):
            if not isinstance(actual, dict) or actual.keys() != expected.keys(): raise ValueError("independent aggregate fields differ")
            for key in expected: close(actual[key], expected[key])
        elif isinstance(expected, list):
            if not isinstance(actual, list) or len(actual) != len(expected): raise ValueError("independent aggregate length differs")
            for a, b in zip(actual, expected): close(a, b)
        elif type(expected) is float:
            if type(actual) not in (int, float) or not math.isfinite(actual): raise ValueError("invalid channel statistic")
            delta = abs(actual - expected)
            maximum = max(maximum, delta)
            checks["statistic_scalars_checked"] += 1
            if delta > 1e-10: raise ValueError("independent channel statistic differs")
        elif actual != expected or type(actual) is not type(expected):
            raise ValueError("independent aggregate value/flag differs")
    with gzip.open(SOURCE / "ledger.jsonl.gz", "rt", encoding="utf-8") as ledger, \
         gzip.open(SOURCE / "same_state_orders.jsonl.gz", "rt", encoding="utf-8") as same_file, \
         gzip.open(LEGACY / "ledger.jsonl.gz", "rt", encoding="utf-8") as old_ledger, \
         gzip.open(LEGACY / "same_state_orders.jsonl.gz", "rt", encoding="utf-8") as old_same:
        for index, line in enumerate(ledger):
            if index >= 70520: raise ValueError("extra channel ledger record")
            day = json.loads(line)
            seed, cell = seeds[index // (8 * 1763)], cells[(index // 1763) % 8]
            basket_index, session = (index % 1763) // 43, index % 43
            key = seed["seed_id"], cell["name"]
            identity = day["seed_id"], day["variant"], day["basket_index"], day["trade_date"], day["portfolio_auction"]["session"]
            if identity != (*key, basket_index, dates[session], session): raise ValueError("channel raw identity/order differs")
            if (day["signal_cutoff_date"], day["execution_reference_date"]) != clocks[day["trade_date"]]:
                raise ValueError("independent channel causal clock differs")
            anchor, target, quote = cell["background_anchor"], cell["target_scale"], cell["quote_scale"]
            controls = None if anchor == "initial_inventory" else {"background_anchor": anchor, "strategy_wait": "original"}
            if day.get("quantity_control_parameters") != controls: raise ValueError("channel inventory/wait controls differ")
            basket = codes[3 * basket_index:3 * basket_index + 3]
            path, core = paths[*key, basket_index], {**base["core"], "seed": seed["arrival_seed"]}
            background = {**base["background"], "seed": seed["background_seed"]}
            specs = path["participant_specs"]
            if session == 0:
                verify_specs(specs, old_paths[seed["seed_id"], anchor + "_feedback_full", basket_index]["participant_specs"], target, quote)
                state = initial_from_specs(specs, basket, {**base, "core": core, "background": background}, case)
                histories, trace, projected_trace = {stock: [] for stock in basket}, [], []
                streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
                issuer = {**issuer_by_seed[seed["seed_id"]], "by_stock": {stock: issuer_by_seed[seed["seed_id"]]["by_stock"][stock] for stock in basket}}
                shared = quantity["shared_design"]
                industry = {**shared["industry"], "membership": {stock: shared["industry"]["membership"][stock] for stock in basket},
                            "path_groups": {stock: shared["industry"]["path_groups"][stock] for stock in basket}}
                shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {stock: [0.0] * 43 for stock in basket}}
            verify_arrival(day, specs, core, session)
            independent_covariance(day, histories, base["feedback_parameters"])
            if target == quote:
                name = anchor + "_feedback_" + ("full" if target == 1 else "off")
                projected = old_diagonal_day(day, target)
                if projected != legacy_record(old_ledger, (seed["seed_id"], name, basket_index, session)):
                    raise ValueError("independent full diagonal trace differs")
                projected_trace.append(projected)
                checks["legacy_diagonal_daily_records_reaudited"] += 1
            if index % (8 * 1763) == 0:
                receipt_hashes = {}
            receipt_key = basket_index, session
            if cell == cells[0]:
                receipt_hashes[receipt_key] = digest(day["issuer_information_receipts"])
            elif digest(day["issuer_information_receipts"]) != receipt_hashes[receipt_key]:
                raise ValueError("independent paired receipt masks/messages differ")
            else:
                checks["paired_private_receipt_values_reaudited"] += sum(len(v) for v in day["issuer_information_receipts"].values())
            if target == quote == 1:
                payloads = {(1.0, 1.0): {"decisions": day["decisions"], "orders": strategy_orders(day, specs)}}
                counts = verify_same_state(payloads[1, 1], day, state, specs, base["venue"], issuer, case, streaks, session, 1.0, 1.0)
                checks["same_state_strategy_allocations_reaudited"] += counts["strategy_allocation_checks"]
                for t, q in ((0.0, 1.0), (1.0, 0.0), (0.0, 0.0)):
                    row = json.loads(next(same_file))
                    if (row["seed_id"], row["background_anchor"], row["basket_index"], row["session"], row["target_scale"], row["quote_scale"]) != (seed["seed_id"], anchor, basket_index, session, t, q):
                        raise ValueError("raw same-state channel identity/order differs")
                    if row["prior_state_sha256"] != digest(state) or row["prior_streaks_sha256"] != digest(streaks):
                        raise ValueError("raw same-state prior resource/streak binding differs")
                    payload = {field: row[field] for field in ("decisions", "orders")}
                    counts = verify_same_state(payload, day, state, specs, base["venue"], issuer, case, streaks, session, t, q)
                    checks["same_state_strategy_allocations_reaudited"] += counts["strategy_allocation_checks"]
                    payloads[t, q] = payload
                    group_key = f"{seed['seed_id']}:{anchor}:{t}:{q}"
                    group = groups.setdefault(group_key, {"seed_id": seed["seed_id"], "background_anchor": anchor,
                                                          "target_scale": t, "quote_scale": q, "records": 0,
                                                          "requested_buy": 0, "requested_sell": 0})
                    group["records"] += 1
                    for orders in payload["orders"].values():
                        for order in orders: group["requested_" + order["side"]] += order["quantity"]
                    checks["same_state_records_reaudited"] += 1
                decisions_without_quote = lambda payload: {name: {k: v for k, v in d.items() if k != "quote_base_beliefs"} for name, d in payload["decisions"].items()}
                orders_without_price = lambda payload: {stock: [{k: v for k, v in o.items() if k != "limit_price_minor"} for o in orders] for stock, orders in payload["orders"].items()}
                for t in (1.0, 0.0):
                    if (decisions_without_quote(payloads[t, 1]) != decisions_without_quote(payloads[t, 0])
                            or orders_without_price(payloads[t, 1]) != orders_without_price(payloads[t, 0])):
                        raise ValueError("independent quote-only quantities/allocations changed")
                checks["quote_only_quantity_identity_checks"] += len(specs) - sum(s["kind"] == "background" for s in specs.values())
                for owner in payloads[0, 1]["decisions"]:
                    if payloads[0, 1]["decisions"][owner]["quote_base_beliefs"] != payloads[1, 1]["decisions"][owner]["issuer_base_beliefs"]:
                        raise ValueError("independent target-only quote base changed")
                    checks["target_only_quote_base_identity_checks"] += len(basket)
                half, off = json.loads(next(old_same)), json.loads(next(old_same))
                for old_row, scale in ((half, 0.5), (off, 0.0)):
                    if (old_row["seed_id"], old_row["background_anchor"], old_row["basket_index"], old_row["session"], old_row["scale"]) != (seed["seed_id"], anchor, basket_index, session, scale):
                        raise ValueError("independent old same-state source order differs")
                if (off["decisions"] != decisions_without_quote(payloads[0, 0]) or off["orders"] != payloads[0, 0]["orders"]
                        or off["prior_state_sha256"] != digest(state) or off["prior_streaks_sha256"] != digest(streaks)):
                    raise ValueError("independent both-off same-state bridge differs")
                checks["legacy_same_state_records_reaudited"] += 1
            counts = verify_day(day, state, specs, base["venue"], background, quantity["background_response"], shocks, industry,
                                issuer, controls, case, streaks, session, target, quote)
            checks.update(counts)
            coverage.setdefault(key, Counter()).update(counts)
            coverage[key]["settlement_asset_calls"] += len(basket)
            branch_counts, flow_counts = branches.setdefault(key, Counter()), flows.setdefault(key, Counter())
            for owner, decision in day["decisions"].items():
                b = "risk_liquidation" if decision["risk_liquidation"] else "confirmation_or_rebalance_wait" if "confirmation_or_rebalance_wait" in decision["reasons"] else "ordinary_allocation"
                branch_counts[specs[owner]["parameters"]["role"] + ":" + b] += 1
            for stock, call in day["portfolio_auction"]["asset_calls"].items():
                for order in call["orders"]:
                    spec = specs[order["owner"]]
                    if spec["kind"] != "strategy": continue
                    d = day["decisions"][order["owner"]]
                    b = "risk_liquidation" if d["risk_liquidation"] else "confirmation_or_rebalance_wait" if "confirmation_or_rebalance_wait" in d["reasons"] else "ordinary_allocation"
                    label = spec["parameters"]["role"] + ":" + b + ":" + order["side"]
                    for stage, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                        flow_counts[label + ":" + stage] += order[field]
                row = {"stock_code": stock, "trade_date": day["trade_date"], "signal_cutoff_date": day["signal_cutoff_date"],
                       "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                       "matched_volume": call["matched_volume"], "observed_return": observed[stock, day["trade_date"]]}
                panels[key].append(row)
                histories[stock].append(row)
            state = audit_portfolio_day(day, state, base["venue"], session, dense=session in cfg["dense_audit_sessions"])
            trace.append({k: v for k, v in day.items() if k not in {"seed_id", "variant", "basket_index"}})
            checks["raw_settlement_asset_calls"] += len(basket)
            checks["full_ledger_records_reaudited"] += 1
            if session == 42:
                verify_final_resources(state, path["summary"])
                if digest(trace) != path["summary"]["trace_sha256"]: raise ValueError("full raw trace hash differs")
                if target == quote:
                    old_path = old_paths[seed["seed_id"], name, basket_index]
                    summary, old_specs = copy.deepcopy(path["summary"]), copy.deepcopy(specs)
                    if target != 1:
                        summary.pop("price_feedback_channels")
                        summary["own_price_feedback_scale"] = target
                        summary["trace_sha256"] = digest(projected_trace)
                        for spec in old_specs.values():
                            if spec["kind"] == "strategy":
                                detail = spec.pop("price_feedback_channels")
                                spec["own_price_feedback_control"] = {"scale": target, "original_momentum_loading": detail["original_momentum_loading"]}
                    if summary != old_path["summary"] or old_specs != old_path["participant_specs"]:
                        raise ValueError("independent complete diagonal summary/specs differs")
                    checks["legacy_complete_paths_reaudited"] += 1
            if (index + 1) % 3000 == 0:
                print(f"Independent target/quote ledger: {index + 1}/70520", flush=True)
        expected = {"full_ledger_records_reaudited": 70520, "same_state_records_reaudited": 52890,
                    "same_state_strategy_allocations_reaudited": 846240, "legacy_diagonal_daily_records_reaudited": 35260,
                    "legacy_same_state_records_reaudited": 17630, "legacy_complete_paths_reaudited": 820,
                    "paired_private_receipt_values_reaudited": 4442760,
                    "quote_only_quantity_identity_checks": 211560, "target_only_quote_base_identity_checks": 634680}
        if any(checks[k] != v for k, v in expected.items()) or any(next(stream, None) is not None for stream in (same_file, old_ledger, old_same)):
            raise ValueError("independent full/same-state/legacy/paired coverage incomplete")
    if groups != result["same_state_groups"]: raise ValueError("independent same-state requested totals differ")
    independent = {}
    for key in sorted(keys):
        rows, saved = panels[key], variants[key]
        if (rows != saved["daily_asset_rows"] or dict(branches[key]) != saved["strategy_decision_branches"]
                or dict(flows[key]) != saved["strategy_branch_order_quantities"] or dict(coverage[key]) != saved["coverage"]):
            raise ValueError("raw daily/branch/flow/coverage aggregates differ")
        measured = independent_metrics(rows, codes, dates, cfg["joint_limits"])
        for field in ("comparison", "common_factor_metrics"): close(saved[field], measured[field])
        close(saved["joint_checks"]["values"], measured["joint_values"])
        close(saved["joint_checks"]["criteria"], measured["joint_criteria"])
        close(saved["joint_checks"]["all_five_pass"], measured["all_five_pass"])
        close(saved["total_matched_volume"], sum(row["matched_volume"] for row in rows))
        independent["|".join(key)] = measured
    summaries, factorial = aggregate([seed["seed_id"] for seed in seeds], cells, independent)
    close(result["seed_summary"], summaries)
    close(result["factorial_summary"], factorial)
    check_bindings()
    return {"pipeline_version": VERSION, "status": "PASS", "agent_signal_enabled": False,
            "inputs": dict(sorted(bindings.items())), "code_sha256": {str(Path(__file__).resolve()): file_sha256(Path(__file__))},
            "checks": dict(checks), "maximum_statistic_absolute_difference": maximum,
            "independent_variant_metrics": independent, "independent_seed_summary": summaries,
            "independent_factorial_summary": factorial, "same_state_groups": groups,
            "interpretation": "Every actual path, hypothetical submitted order and legacy diagonal reopened and audited. Separate fsum/variance/Pearson statistics, seed aggregates, paired factorial changes and role/branch requested/accepted/filled totals agree. Internal development mechanism controls are not empirical investor calibration, new historical validation, predictive improvement or signal release."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    path = args.output.resolve()
    if path.parent != (ROOT / "research_outputs").resolve(): raise ValueError("independent output must be in research_outputs")
    result = compute()
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if path.read_bytes() != payload: raise ValueError("independent channel statistics differ byte-for-byte")
        print("Independent target/quote channel statistics rebuilt byte-identically.", flush=True)
    else:
        with path.open("xb") as stream: stream.write(payload)
        print("Independent target/quote channel audit completed.", flush=True)
    print(json.dumps({"status": result["status"], "checks": result["checks"],
                      "maximum_statistic_absolute_difference": result["maximum_statistic_absolute_difference"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
