"""Reopen every temporal ledger and independently reconstruct all resources."""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math

from .temporal_liquidity_study import (ROOT, CONFIG, OUTPUT, CELLS, read, encoded, require, load_study,
    path_key, controls_for, shocks_for, read_gzip, checkpoint, bindings_ok, NEW_ARMS)
from .audit_temporal_inputs import independent_source, verify_prepared
from .audit_temporal_statistics import independent_metrics
from .audit_temporal_liquidity_channels import verify_day, verify_specs
from .audit_temporal_liquidity_inputs import independent_liquidity
from .temporal_liquidity_inputs import subset as subset_liquidity
from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs, independent_covariance
from .audit_own_price_feedback import verify_arrival, verify_final_resources
from .portfolio_audit import audit_portfolio_day
from .verify_pre_wuhan_order_price_diagnostics_2019 import compare
from .temporal_fixed_marginal_risk import subset
from .semantic_memory_sensitivity import canonical_hash
from ..data_pipeline.provenance import file_sha256


def audit_seed(si):
    require(type(si) is int and 0 <= si < 5, "seed outside temporal audit grid")
    cfg, expected_risk, expected_factors, joined, mask, liquidity = load_study()
    liquidity_qc = independent_liquidity(cfg, liquidity)
    target = OUTPUT / f"seed_{si}/independent.json"
    require(not target.exists(), "preserve completed independent temporal audit")
    risk, factors, raw_joined, source_qc = independent_source(cfg)
    require(risk == expected_risk and factors == expected_factors and raw_joined == joined,
        "temporal model interface differs from independently read original sources")
    seed, design = cfg["seeds"][si], cfg["model_design"]
    core = {**design["core"], "seed": seed["arrival_seed"], "scenario_id": f"endogenous_cohort_seed{seed['arrival_seed']}"}
    background = {**design["background"], "seed": seed["background_seed"]}
    folder = OUTPUT / f"seed_{si}"
    checkpoint(folder / "prepared", [seed["seed_id"], "input_paths"])
    paths = read_gzip(folder / "prepared/issuer_paths.json.gz")
    counts = Counter(verify_prepared(cfg, paths, risk, factors, seed))
    reference_specs = read(ROOT / cfg["legacy_spec_paths"][si])["paths"]
    bindings, coverage_by_cell = {}, {}
    for arm in NEW_ARMS:
        for ci, cell in enumerate(CELLS):
            cell_folder = folder / arm["arm"] / f"cell_{ci}"
            checkpoint(cell_folder, [seed["seed_id"], cell["name"], arm["arm"]])
            saved = read(cell_folder / "condition.json")
            require([r["basket_index"] for r in saved["paths"]] == list(range(41)), "complete temporal basket coverage differs")
            daily, flows, condition_counts = [], Counter(), Counter()
            with gzip.open(cell_folder / "ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
                for bi, saved_path in enumerate(saved["paths"]):
                    basket = cfg["baskets"][bi]
                    require(saved_path["seed_id"] == seed["seed_id"] and saved_path["variant"] == cell["name"]
                            and saved_path["arm"] == arm["arm"], "path identity differs")
                    issuer, specs = subset(paths[path_key(cell)], basket), saved_path["participant_specs"]
                    verify_specs(specs, reference_specs[bi]["participant_specs"], cell["feedback_scale"], cell["feedback_scale"])
                    state = initial_from_specs(specs, basket, {**design, "core": core, "background": background}, design["case"])
                    wealth = {n: sum(a["wallets"].values()) + sum(a["shares"][s] * state["prices"][s] for s in basket)
                        for n, a in state["accounts"].items()}
                    peaks, drawdowns = dict(wealth), dict.fromkeys(wealth, 0.0)
                    risks = {n: {"risk_breach_sessions": 0, "concentration_breach_sessions": 0} for n in wealth}
                    path_orders, hasher = Counter(), hashlib.sha256(b"[")
                    histories = {s: [] for s in basket}
                    streaks = {n: {"sign": 0, "streak": 0} for n, spec in specs.items() if spec["kind"] == "strategy"}
                    for session, date in enumerate(cfg["dates"]):
                        line = next(stream, None)
                        require(line is not None, "temporal ledger ended early")
                        row = json.loads(line)
                        require((row["seed_id"], row["variant"], row["basket_index"], row["portfolio_auction"]["session"], row["arm"])
                            == (seed["seed_id"], cell["name"], bi, session, arm["arm"]), "full ledger identity differs")
                        day = {k: v for k, v in row.items() if k not in {"seed_id", "variant", "basket_index", "arm"}}
                        require((day["trade_date"], day["signal_cutoff_date"], day["execution_reference_date"])
                            == (date, joined[basket[0]][session]["signal_cutoff_date"], joined[basket[0]][session]["execution_reference_date"]),
                            "ledger information clock differs")
                        verify_arrival(day, specs, core, session)
                        independent_covariance(day, histories, design["feedback_parameters"])
                        coverage = verify_day(day, state, specs, design["venue"], background, cfg["background_response"],
                            shocks_for(basket, cfg["dates"]), None, issuer, controls_for(cell["background_anchor"]), design["case"],
                            streaks, session, cell["feedback_scale"], cell["feedback_scale"],
                            liquidity_rows={s: liquidity["by_stock"][s][session] for s in basket} if arm["liquidity_mode"] == "lagged_turnover" else None)
                        condition_counts.update(coverage)
                        counts["strict_combined_receipt_positions"] += sum(len(r) for r in day["issuer_information_receipts"].values())
                        for stock, call in day["portfolio_auction"]["asset_calls"].items():
                            require(call["execution_available"] is joined[stock][session]["execution_available"], "retrospective status differs")
                            if not call["execution_available"]:
                                require(call["matched_volume"] == 0 and call["price_before_minor"] == call["price_after_minor"], "suspended venue executed")
                                counts["suspended_asset_calls"] += 1
                            if joined[stock][session]["observed_return"] is None:
                                counts["unknown_target_asset_calls_preserved"] += 1
                            daily.append({"stock_code": stock, "trade_date": date, "signal_cutoff_date": day["signal_cutoff_date"],
                                "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                                "matched_volume": call["matched_volume"], "observed_return": joined[stock][session]["observed_return"]})
                            histories[stock].append({"trade_date": date, "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"]})
                            for order in call["orders"]:
                                role = "background" if specs[order["owner"]]["kind"] == "background" else specs[order["owner"]]["parameters"]["role"]
                                for label, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                                    flows[role + "|" + order["side"] + "|" + label] += order[field]
                                    if role != "background":
                                        path_orders[label] += order[field]
                                if "cash_and_fee_reservation" in order["reasons"]:
                                    flows[role + "|" + order["side"] + "|cash_clipped"] += order["quantity"] - order["accepted_quantity"]
                                    if role != "background":
                                        path_orders["cash_clipped_orders"] += 1
                        state = audit_portfolio_day(day, state, design["venue"], session, dense=session in cfg["dense_audit_sessions"], price_tie_break=arm["price_tie_break"])
                        for actor, account in state["accounts"].items():
                            nav = sum(account["wallets"].values()) + sum(account["shares"][s] * state["prices"][s] for s in basket)
                            peaks[actor] = max(peaks[actor], nav)
                            drawdowns[actor] = max(drawdowns[actor], 1 - nav / peaks[actor])
                            if specs[actor]["kind"] == "strategy":
                                weights = {s: account["shares"][s] * state["prices"][s] / nav for s in basket}
                                r = math.sqrt(max(0.0, sum(weights[a] * day["covariance"][a][b] * weights[b] for a in basket for b in basket)))
                                p = specs[actor]["parameters"]
                                risks[actor]["risk_breach_sessions"] += int(r > p["risk_budget"] + 1e-9 or sum(weights.values()) > p["max_weight"] + 1e-9)
                                risks[actor]["concentration_breach_sessions"] += int(any(w > day["decisions"][actor]["asset_weight_cap"] + 1e-9 for w in weights.values()))
                        if session:
                            hasher.update(b", ")
                        hasher.update(json.dumps(day, ensure_ascii=False, sort_keys=True, allow_nan=False).encode())
                        counts["full_path_ledger_records"] += 1
                        counts["asset_calls"] += len(basket)
                        counts["strategy_allocation_checks"] += 12
                        if session in cfg["dense_audit_sessions"]:
                            counts["dense_asset_calls"] += len(basket)
                    hasher.update(b"]")
                    verify_final_resources(state, saved_path["summary"])
                    summary = saved_path["summary"]
                    for actor, account in state["accounts"].items():
                        nav = sum(account["wallets"].values()) + sum(account["shares"][s] * state["prices"][s] for s in basket)
                        expected = {"kind": specs[actor]["kind"], "role": specs[actor].get("parameters", {}).get("role"), **account,
                            "initial_wealth_minor": wealth[actor], "final_wealth_minor": nav, "wealth_multiple": nav / wealth[actor],
                            "max_drawdown": drawdowns[actor], **risks[actor]}
                        compare(summary["accounts"][actor], expected)
                        counts["complete_wealth_drawdown_risk_accounts"] += 1
                    for field, key in (("strategy_requested", "requested"), ("strategy_accepted", "accepted"), ("strategy_filled", "filled"), ("cash_clipped_orders", "cash_clipped_orders")):
                        require(summary[field] == path_orders[key], "complete strategy resource totals differ")
                    require(summary["trace_sha256"] == hasher.hexdigest() and summary["issuer_valuation_scenario"] ==
                        {"scenario_id": issuer["scenario_id"], "parameters": issuer["parameters"], "path_sha256": canonical_hash(issuer)}, "whole trace/message summary hash differs")
                    if arm["liquidity_mode"] == "lagged_turnover":
                        require(summary["liquidity_capacity_path"] == {"version": liquidity["version"], "path_sha256": canonical_hash(subset_liquidity(liquidity, basket))}, "full liquidity path binding differs")
                    counts["paths"] += 1
                    if (bi + 1) % 10 == 0:
                        print(f"Temporal audit seed {si + 1}/5 condition {ci + 1}/16: {bi + 1}/41 baskets audited.", flush=True)
                require(next(stream, None) is None, "temporal ledger has extra records")
            variant = saved["variant"]
            require(all(variant[k] == v for k, v in {**cell, **arm, "seed_id": seed["seed_id"]}.items()),
                    "complete condition factor labels differ")
            compare(daily, variant["daily_asset_rows"])
            compare(dict(flows), variant["role_order_quantities"])
            require(variant["total_matched_volume"] == sum(r["matched_volume"] for r in daily), "matched volume total differs")
            independent = independent_metrics(daily, mask, cfg["joint_limits"])
            for field in ("comparison", "common_factor_metrics", "all_model_return_distribution"):
                compare(independent[field], variant[field])
            compare(independent["joint_values"], variant["joint_checks"]["values"])
            require(independent["joint_criteria"] == variant["joint_checks"]["criteria"]
                and independent["all_five_pass"] == variant["joint_checks"]["all_five_pass"]
                and independent["undefined_synthetic_primary_pairs"] == variant["evaluation_coverage"]["undefined_synthetic_primary_pairs"],
                "independent joint checks or undefined correlations differ")
            coverage = variant["evaluation_coverage"]
            require(coverage["paired_company_days"] == 7119 and coverage["unknown_target_company_days"] == 15
                and coverage["full_simulated_company_days"] == 7134 and coverage["mask_sha256"] == canonical_hash(mask)
                and coverage["full_cohort_portfolio_common_sessions"] == len(mask["full_cohort_portfolio_common_dates"]), "evaluation denominators differ")
            coverage_by_cell[arm["arm"] + "|" + cell["name"]] = dict(condition_counts)
            bindings[str((cell_folder / "checkpoint.json").relative_to(ROOT))] = file_sha256(cell_folder / "checkpoint.json")
            counts["conditions"] += 1
            print(f"Temporal independent seed {si + 1}/5 condition {ci + 1}/16 passed.", flush=True)
    require(dict(counts) == cfg["per_seed_audit_scope"], "full independent audit scope differs: " + repr(dict(counts)))
    bindings_ok(cfg)
    with target.open("xb") as stream:
        stream.write(encoded({"status": "PASS_FULL_TEMPORAL_LIQUIDITY_FACTORIAL_SEED", "seed_id": seed["seed_id"],
            "protocol_sha256": file_sha256(CONFIG), "checks": dict(counts), "source_qc": source_qc, "liquidity_qc": liquidity_qc,
            "condition_channel_coverage": coverage_by_cell, "artifacts": bindings}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    audit_seed(parser.parse_args().seed)
