"""Reopen every temporal ledger and independently reconstruct all resources."""
from __future__ import annotations
import argparse
import copy
from collections import Counter
import gzip
import hashlib
import json
import math

from .temporal_warmup_study import (ROOT, CONFIG, OUTPUT, CELLS, read, encoded, require, load_study,
    path_key, controls_for, shocks_for, read_gzip, checkpoint, bindings_ok)
from .audit_temporal_internal_warmup_inputs import independent_source
from .audit_temporal_inputs import verify_prepared
from .audit_temporal_statistics import independent_metrics
from .audit_temporal_warmup_channels import verify_day, verify_specs
from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs, independent_covariance
from .audit_own_price_feedback import verify_arrival, verify_final_resources
from .portfolio_audit import audit_portfolio_day
from .verify_pre_wuhan_order_price_diagnostics_2019 import compare
from .temporal_fixed_marginal_risk import subset
from .semantic_memory_sensitivity import canonical_hash
from ..data_pipeline.provenance import file_sha256
from .temporal_warmup_study import BASE_OUTPUT
from .temporal_information_study import checkpoint as baseline_checkpoint


def audit_seed(si, preflight=False):
    require(type(si) is int and 0 <= si < 5, "seed outside temporal audit grid")
    require(type(preflight) is bool and (not preflight or si == 0), "preflight must use declared seed zero")
    cfg, expected_risk, expected_factors, joined, mask = load_study()
    output = ROOT / "research_outputs/temporal_carried_warmup_full_condition_preflight_20261003_v1" if preflight else OUTPUT
    cells = CELLS[:1] if preflight else CELLS
    if preflight:
        cfg = {**cfg, "per_seed_audit_scope": {k: v if k.startswith("independently_") or k == "unchanged_q1_private_and_public_budget_rows" else v // 16
            for k, v in cfg["per_seed_audit_scope"].items()}}
    target = output / f"seed_{si}/independent.json"
    require(not target.exists(), "preserve completed independent temporal audit")
    risk, factors, raw_joined, source_qc = independent_source(cfg)
    require(risk == expected_risk and factors == expected_factors and raw_joined == joined,
        "temporal model interface differs from independently read original sources")
    seed, design = cfg["seeds"][si], cfg["model_design"]
    core = {**design["core"], "seed": seed["arrival_seed"], "scenario_id": f"endogenous_cohort_seed{seed['arrival_seed']}"}
    background = {**design["background"], "seed": seed["background_seed"]}
    folder = output / f"seed_{si}"
    checkpoint(folder / "prepared", [seed["seed_id"], "input_paths"])
    paths = read_gzip(folder / "prepared/issuer_paths.json.gz")
    counts = Counter(verify_prepared(cfg, paths, risk, factors, seed))
    baseline_prepared = BASE_OUTPUT / f"seed_{si}/prepared"
    baseline_checkpoint(baseline_prepared, [seed["seed_id"], "input_paths"])
    cold_paths = read_gzip(baseline_prepared / "issuer_paths.json.gz")
    require(set(paths) == set(cold_paths), "Q1 prepared path scope differs")
    for key in paths:
        for stock in cfg["stocks"]:
            require(paths[key]["by_stock"][stock][42:] == cold_paths[key]["by_stock"][stock],
                "Q1 information budget or actual private message differs from frozen cold baseline")
            counts["unchanged_q1_private_and_public_budget_rows"] += len(cfg["evaluation_dates"])
    reference_specs = read(ROOT / cfg["legacy_spec_paths"][si])["paths"]
    bindings, coverage_by_cell = {}, {}
    for ci, cell in enumerate(cells):
        cell_folder = folder / f"cell_{ci}"
        checkpoint(cell_folder, [seed["seed_id"], cell["name"]])
        saved = read(cell_folder / "condition.json")
        require([r["basket_index"] for r in saved["paths"]] == list(range(41)), "complete temporal basket coverage differs")
        daily, flows, warm_flows, condition_counts = [], Counter(), Counter(), Counter()
        with gzip.open(cell_folder / "ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
            for bi, saved_path in enumerate(saved["paths"]):
                basket = cfg["baskets"][bi]
                require(saved_path["seed_id"] == seed["seed_id"] and saved_path["variant"] == cell["name"], "path identity differs")
                issuer, specs = subset(paths[path_key(cell)], basket), saved_path["participant_specs"]
                verify_specs(specs, reference_specs[bi]["participant_specs"], cell["feedback_scale"], cell["feedback_scale"])
                state = initial_from_specs(specs, basket, {**design, "core": core, "background": background}, design["case"])
                wealth = {n: sum(a["wallets"].values()) + sum(a["shares"][s] * state["prices"][s] for s in basket)
                    for n, a in state["accounts"].items()}
                peaks, drawdowns = dict(wealth), dict.fromkeys(wealth, 0.0)
                risks = {n: {"risk_breach_sessions": 0, "concentration_breach_sessions": 0} for n in wealth}
                eval_start, eval_peaks, eval_drawdowns, eval_risks, eval_opening = {}, {}, {}, {}, None
                path_orders, hasher = Counter(), hashlib.sha256(b"[")
                histories = {s: [] for s in basket}
                streaks = {n: {"sign": 0, "streak": 0} for n, spec in specs.items() if spec["kind"] == "strategy"}
                for session, date in enumerate(cfg["dates"]):
                    line = next(stream, None)
                    require(line is not None, "temporal ledger ended early")
                    row = json.loads(line)
                    require((row["seed_id"], row["variant"], row["basket_index"], row["portfolio_auction"]["session"])
                        == (seed["seed_id"], cell["name"], bi, session), "full ledger identity differs")
                    day = {k: v for k, v in row.items() if k not in {"seed_id", "variant", "basket_index"}}
                    require((day["trade_date"], day["signal_cutoff_date"], day["execution_reference_date"])
                        == (date, joined[basket[0]][session]["signal_cutoff_date"], joined[basket[0]][session]["execution_reference_date"]),
                        "ledger information clock differs")
                    draw = session - cfg["warmup_sessions"]
                    require(day["warmup_draw_clock"] == {"version": "date-aligned-carried-state-warmup-clock-v1",
                        "evaluation_start_date": cfg["evaluation_dates"][0], "warmup_sessions": 42,
                        "model_session": session, "draw_session": draw, "phase": "warmup" if draw < 0 else "evaluation"},
                        "independent date-aligned draw clock differs")
                    verify_arrival(day, specs, core, draw)
                    if draw == 0:
                        eval_opening = copy.deepcopy(state)
                        for owner, account in state["accounts"].items():
                            nav = sum(account["wallets"].values()) + sum(account["shares"][s] * state["prices"][s] for s in basket)
                            eval_start[owner] = eval_peaks[owner] = nav
                            eval_drawdowns[owner] = 0.0
                            eval_risks[owner] = {"risk_breach_sessions": 0, "concentration_breach_sessions": 0}
                        require(day["portfolio_auction"]["prices_before_minor"] == state["prices"], "evaluation price reset")
                        counts["evaluation_boundary_checks"] += 1
                    if draw >= 0:
                        require(day["trade_date"] == cfg["evaluation_dates"][draw], "original Q1 date alignment differs")
                        counts["date_coupled_evaluation_day_checks"] += 1
                        counts["date_coupled_background_draw_positions"] += sum(len(v) for v in day["background_demands"].values())
                        for stock in basket:
                            require(day["observations"][stock]["warmup_missing"] == 0, "Q1 own-history is still incomplete")
                            counts["full_evaluation_own_history_asset_calls"] += 1
                    independent_covariance(day, histories, design["feedback_parameters"])
                    coverage = verify_day(day, state, specs, design["venue"], background, cfg["background_response"],
                        shocks_for(basket, cfg["dates"]), None, issuer, controls_for(cell["background_anchor"]), design["case"],
                        streaks, session, cell["feedback_scale"], cell["feedback_scale"], draw_session=draw)
                    condition_counts.update(coverage)
                    counts["strict_combined_receipt_positions"] += sum(len(r) for r in day["issuer_information_receipts"].values())
                    active_flows = flows if session >= cfg["warmup_sessions"] else warm_flows
                    for stock, call in day["portfolio_auction"]["asset_calls"].items():
                        require(call["execution_available"] is joined[stock][session]["execution_available"], "retrospective status differs")
                        if not call["execution_available"]:
                            require(call["matched_volume"] == 0 and call["price_before_minor"] == call["price_after_minor"], "suspended venue executed")
                            counts["suspended_asset_calls"] += 1
                        if joined[stock][session]["observed_return"] is None:
                            counts["unknown_target_asset_calls_preserved"] += 1
                        if session >= cfg["warmup_sessions"]:
                            daily.append({"stock_code": stock, "trade_date": date, "signal_cutoff_date": day["signal_cutoff_date"],
                                "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                                "matched_volume": call["matched_volume"], "observed_return": joined[stock][session]["observed_return"]})
                        histories[stock].append({"trade_date": date, "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"]})
                        for order in call["orders"]:
                            role = "background" if specs[order["owner"]]["kind"] == "background" else specs[order["owner"]]["parameters"]["role"]
                            for label, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                                active_flows[role + "|" + order["side"] + "|" + label] += order[field]
                                if role != "background":
                                    path_orders[label] += order[field]
                            if "cash_and_fee_reservation" in order["reasons"]:
                                active_flows[role + "|" + order["side"] + "|cash_clipped"] += order["quantity"] - order["accepted_quantity"]
                                if role != "background":
                                    path_orders["cash_clipped_orders"] += 1
                    state = audit_portfolio_day(day, state, design["venue"], session, dense=session in cfg["dense_audit_sessions"])
                    for actor, account in state["accounts"].items():
                        nav = sum(account["wallets"].values()) + sum(account["shares"][s] * state["prices"][s] for s in basket)
                        peaks[actor] = max(peaks[actor], nav)
                        drawdowns[actor] = max(drawdowns[actor], 1 - nav / peaks[actor])
                        if draw >= 0:
                            eval_peaks[actor] = max(eval_peaks[actor], nav)
                            eval_drawdowns[actor] = max(eval_drawdowns[actor], 1 - nav / eval_peaks[actor])
                        if specs[actor]["kind"] == "strategy":
                            weights = {s: account["shares"][s] * state["prices"][s] / nav for s in basket}
                            r = math.sqrt(max(0.0, sum(weights[a] * day["covariance"][a][b] * weights[b] for a in basket for b in basket)))
                            p = specs[actor]["parameters"]
                            risks[actor]["risk_breach_sessions"] += int(r > p["risk_budget"] + 1e-9 or sum(weights.values()) > p["max_weight"] + 1e-9)
                            risks[actor]["concentration_breach_sessions"] += int(any(w > day["decisions"][actor]["asset_weight_cap"] + 1e-9 for w in weights.values()))
                            if draw >= 0:
                                eval_risks[actor]["risk_breach_sessions"] += int(r > p["risk_budget"] + 1e-9 or sum(weights.values()) > p["max_weight"] + 1e-9)
                                eval_risks[actor]["concentration_breach_sessions"] += int(any(w > day["decisions"][actor]["asset_weight_cap"] + 1e-9 for w in weights.values()))
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
                    expected_eval = {"kind": specs[actor]["kind"], "role": specs[actor].get("parameters", {}).get("role"), **account,
                        "opening_evaluation_wealth_minor": eval_start[actor], "final_wealth_minor": nav,
                        "evaluation_wealth_multiple": nav / eval_start[actor], "evaluation_max_drawdown": eval_drawdowns[actor], **eval_risks[actor]}
                    compare(saved_path["evaluation_summary"]["accounts"][actor], expected_eval)
                    counts["complete_evaluation_wealth_drawdown_risk_accounts"] += 1
                require(eval_opening is not None, "carried evaluation boundary was omitted")
                eval_expected = {"evaluation_sessions": 58, "opening_trade_date": cfg["dates"][41],
                    "first_evaluation_date": cfg["evaluation_dates"][0], "last_evaluation_date": cfg["evaluation_dates"][-1],
                    "opening_prices_minor": eval_opening["prices"], "opening_fee_pool_minor": eval_opening["fee_pool_minor"],
                    "evaluation_fee_increment_minor": state["fee_pool_minor"] - eval_opening["fee_pool_minor"],
                    "state_reset_at_evaluation_start": False,
                    "interpretation": "Q1 wealth, drawdown and breach counts use carried Dec31 opening state; full 100-session summaries remain separate."}
                compare({k: v for k, v in saved_path["evaluation_summary"].items() if k != "accounts"}, eval_expected)
                for field, key in (("strategy_requested", "requested"), ("strategy_accepted", "accepted"), ("strategy_filled", "filled"), ("cash_clipped_orders", "cash_clipped_orders")):
                    require(summary[field] == path_orders[key], "complete strategy resource totals differ")
                require(summary["trace_sha256"] == hasher.hexdigest() and summary["issuer_valuation_scenario"] ==
                    {"scenario_id": issuer["scenario_id"], "parameters": issuer["parameters"], "path_sha256": canonical_hash(issuer)}, "whole trace/message summary hash differs")
                counts["paths"] += 1
                if (bi + 1) % 10 == 0:
                    print(f"Temporal audit seed {si + 1}/5 condition {ci + 1}/16: {bi + 1}/41 baskets audited.", flush=True)
            require(next(stream, None) is None, "temporal ledger has extra records")
        variant = saved["variant"]
        compare(daily, variant["daily_asset_rows"])
        compare(dict(flows), variant["role_order_quantities"])
        compare(dict(warm_flows), variant["warmup_role_order_quantities"])
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
        coverage_by_cell[cell["name"]] = dict(condition_counts)
        bindings[str((cell_folder / "checkpoint.json").relative_to(ROOT))] = file_sha256(cell_folder / "checkpoint.json")
        counts["conditions"] += 1
        print(f"Temporal independent seed {si + 1}/5 condition {ci + 1}/16 passed.", flush=True)
    require(dict(counts) == cfg["per_seed_audit_scope"], "full independent audit scope differs: " + repr(dict(counts)))
    bindings_ok(cfg)
    with target.open("xb") as stream:
        stream.write(encoded({"status": "PASS_ONE_FULL_TEMPORAL_CARRIED_WARMUP_CONDITION_PREFLIGHT" if preflight else "PASS_FULL_TEMPORAL_CARRIED_WARMUP_SEED_RAW_LEDGER_AND_Q1_STATISTICS", "seed_id": seed["seed_id"],
            "preflight_condition_only": preflight, "declared_condition_count": len(cells),
            "protocol_sha256": file_sha256(CONFIG), "checks": dict(counts), "source_qc": source_qc,
            "condition_channel_coverage": coverage_by_cell, "artifacts": bindings}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    audit_seed(args.seed, args.preflight)
