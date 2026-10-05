"""Reopen every raw condition and reconstruct resources, messages and statistics."""
from __future__ import annotations
import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
import statistics

from .public_information_study import (ROOT, CONFIG, OUTPUT, CELLS, read, require, load_study,
    seed_paths, checkpoint, prior_index, path_key, read_gzip_json)
from . import fixed_risk_study as prior
from ..data_pipeline.provenance import file_sha256
from .run_pre_wuhan_public_factor_channels_2019 import (controls_for, subset_industry_shocks, initial_from_specs,
    independent_covariance, verify_arrival, audit_portfolio_day, verify_final_resources, verify_specs, record_bytes)
from .public_information_risk import subset
from .audit_public_information_risk import verify_budget, verify_receipt
from .audit_public_information_channels import verify_day
from .verify_pre_wuhan_own_price_feedback_2019_v2 import independent_metrics
from .verify_pre_wuhan_order_price_diagnostics_2019 import compare
from .semantic_memory_sensitivity import canonical_hash
from .public_factor_numeric_inputs_v3 import verify_numeric

def audit_seed(si):
    require(type(si) is int and 0 <= si < 5, "audit seed outside frozen grid")
    cfg, loaded = load_study()
    _, base, _, market, codes, dates, _, joined, _, _, shared, _, _, industry_cfg, source = loaded
    seed = cfg["seeds"][si]
    root = OUTPUT
    folder = root / f"seed_{si}"
    target = folder / "independent.json"
    require(not target.exists(), "preserve independent full seed audit")
    checkpoint(folder / "prepared", [seed["seed_id"], "input_paths"])
    core, background, expected_paths = seed_paths(loaded, seed, independent=True)
    require(read_gzip_json(folder / "prepared/issuer_paths.json.gz") == expected_paths, "saved complete source risk paths differ from independent raw reconstruction")
    case = next(c for c in base["cases"] if c["case_id"] == "separated_institution35")
    old_paths = {(r["seed_id"], r["variant"], r["basket_index"]): r for r in source["path_summaries"]}
    counts = Counter(independently_rebuilt_budget_rows=10578)
    for mode in ("market_independent", "market_shared"):
        for rows in expected_paths[mode + "_public"]["by_stock"].values():
            for message_row in rows:
                verify_budget(message_row, expected_paths[mode + "_public"]["parameters"])
                counts["independently_integrated_public_budget_rows"] += 1
    measured, source_bindings, receipt_measurements = {}, {}, {}
    for ci, cell in enumerate(CELLS):
        cell_folder = folder / f"cell_{ci}"
        sealed = checkpoint(cell_folder, [seed["seed_id"], cell["name"]])
        source_bindings[f"cell_{ci}"] = sealed
        saved = read(cell_folder / "condition.json")
        require(len(saved["paths"]) == 41 and [p["basket_index"] for p in saved["paths"]] == list(range(41)), "independent path coverage differs")
        daily, flows, coverage = [], Counter(), Counter()
        receipt_samples = {role: [] for role in ('aggressive', 'conservative', 'institutional', 'background')}
        receipt_flags = {role: Counter() for role in receipt_samples}
        old_ci = prior_index(cell)
        golden_folder = prior.OUTPUT / f"seed_{si}/cell_{old_ci}"
        prior.checkpoint(golden_folder, [seed["seed_id"], prior.CELLS[old_ci]["name"]])
        golden = read(golden_folder / "condition.json")
        old_stream = gzip.open(golden_folder / "ledger.jsonl.gz", "rt", encoding="utf-8") if cell["delivery"] == "masked" else None
        try:
            with gzip.open(cell_folder / "ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
                for bi, saved_path in enumerate(saved["paths"]):
                    basket = codes[bi * 3:(bi + 1) * 3]
                    require(saved_path["seed_id"] == seed["seed_id"] and saved_path["variant"] == cell["name"], "saved path identity differs")
                    issuer = subset(expected_paths[path_key(cell)], basket)
                    specs = saved_path["participant_specs"]
                    original_summary = golden["paths"][bi]
                    verify_specs(specs, old_paths[seed["seed_id"], cell["background_anchor"] + "_feedback_full", bi]["participant_specs"],
                        cell["feedback_scale"], cell["feedback_scale"], None)
                    state = initial_from_specs(specs, basket, {**base, "core": core, "background": background}, case)
                    wealth = {n: sum(a["wallets"].values()) + sum(a["shares"][s] * state["prices"][s] for s in basket)
                        for n, a in state["accounts"].items()}
                    peaks, drawdowns = dict(wealth), dict.fromkeys(wealth, 0.0)
                    risks = {n: {"risk_breach_sessions": 0, "concentration_breach_sessions": 0} for n in wealth}
                    path_orders, hasher = Counter(), hashlib.sha256(b"[")
                    histories = {s: [] for s in basket}
                    streaks = {n: {"sign": 0, "streak": 0} for n, s in specs.items() if s["kind"] == "strategy"}
                    shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {s: [0.0] * len(dates) for s in basket}}
                    industry = subset_industry_shocks(shared["industry"], basket)
                    for session, date in enumerate(dates):
                        line = next(stream, None)
                        require(line is not None, "full risk ledger ended early")
                        row = json.loads(line)
                        require((row["seed_id"], row["variant"], row["basket_index"], row["portfolio_auction"]["session"])
                            == (seed["seed_id"], cell["name"], bi, session), "full risk ledger identity differs")
                        day = {k: v for k, v in row.items() if k not in {"seed_id", "variant", "basket_index"}}
                        require((day["trade_date"], day["signal_cutoff_date"], day["execution_reference_date"])
                            == (date, joined[basket[0]][session]["signal_cutoff_date"], joined[basket[0]][session]["execution_reference_date"]), "full risk clock differs")
                        if old_stream:
                            old_row = json.loads(next(old_stream))
                            require((old_row["seed_id"], old_row["variant"], old_row["basket_index"], old_row["portfolio_auction"]["session"])
                                == (seed["seed_id"], prior.CELLS[old_ci]["name"], bi, session), "independent legacy identity differs")
                            require(day == {k: v for k, v in old_row.items() if k not in {"seed_id", "variant", "basket_index"}}, "independent complete legacy day differs")
                            counts["masked_daily_records_matched"] += 1
                        verify_arrival(day, specs, core, session)
                        independent_covariance(day, histories, base["feedback_parameters"])
                        coverage.update(verify_day(day, state, specs, base["venue"], background, industry_cfg["background_response"],
                            shocks, industry, issuer, controls_for(cell["background_anchor"]), case, streaks, session,
                            cell["feedback_scale"], cell["feedback_scale"], None, None))
                        for actor, receipts in day["issuer_information_receipts"].items():
                            for stock, receipt in receipts.items():
                                verify_receipt(receipt, issuer["by_stock"][stock][session], issuer["parameters"], stock, actor)
                                counts["strict_combined_receipt_positions"] += 1
                                role = 'background' if specs[actor]['kind'] == 'background' else specs[actor]['parameters']['role']
                                receipt_samples[role].append(receipt['applied_valuation_shift_bps'])
                                detail = receipt.get('public_information_receipt')
                                private_received = detail['private_received'] if detail else receipt['received']
                                receipt_flags[role]['private_received'] += int(private_received)
                                receipt_flags[role]['public_received'] += int(bool(detail))
                                receipt_flags[role]['capped'] += int(detail['was_capped'] if detail else
                                    private_received and issuer['by_stock'][stock][session]['was_capped'])
                        for stock, call in day["portfolio_auction"]["asset_calls"].items():
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
                        state = audit_portfolio_day(day, state, base["venue"], session, dense=session in cfg["dense_audit_sessions"])
                        for actor, account in state["accounts"].items():
                            nav = sum(account["wallets"].values()) + sum(account["shares"][s] * state["prices"][s] for s in basket)
                            peaks[actor] = max(peaks[actor], nav)
                            drawdowns[actor] = max(drawdowns[actor], 1 - nav / peaks[actor])
                            if specs[actor]["kind"] == "strategy":
                                weights = {s: account["shares"][s] * state["prices"][s] / nav for s in basket}
                                risk = math.sqrt(max(0.0, sum(weights[a] * day["covariance"][a][b] * weights[b] for a in basket for b in basket)))
                                parameters = specs[actor]["parameters"]
                                risks[actor]["risk_breach_sessions"] += int(risk > parameters["risk_budget"] + 1e-9 or sum(weights.values()) > parameters["max_weight"] + 1e-9)
                                risks[actor]["concentration_breach_sessions"] += int(any(w > day["decisions"][actor]["asset_weight_cap"] + 1e-9 for w in weights.values()))
                        if session:
                            hasher.update(b", ")
                        hasher.update(json.dumps(day, ensure_ascii=False, sort_keys=True, allow_nan=False).encode())
                        counts["full_path_ledger_records"] += 1
                        counts["asset_calls"] += 3
                        counts["strategy_allocation_checks"] += 12
                        if session in cfg["dense_audit_sessions"]:
                            counts["dense_asset_calls"] += 3
                    hasher.update(b"]")
                    verify_final_resources(state, saved_path["summary"])
                    accounts = {}
                    for actor, account in state["accounts"].items():
                        nav = sum(account["wallets"].values()) + sum(account["shares"][s] * state["prices"][s] for s in basket)
                        accounts[actor] = {"kind": specs[actor]["kind"], "role": specs[actor].get("parameters", {}).get("role"), **account,
                            "initial_wealth_minor": wealth[actor], "final_wealth_minor": nav, "wealth_multiple": nav / wealth[actor],
                            "max_drawdown": drawdowns[actor], **risks[actor]}
                        counts["complete_wealth_drawdown_risk_accounts"] += 1
                    rebuilt = {**original_summary["summary"], "accounts": accounts, "final_prices_minor": state["prices"], "fee_pool_minor": state["fee_pool_minor"],
                        "strategy_requested": path_orders["requested"], "strategy_accepted": path_orders["accepted"], "strategy_filled": path_orders["filled"],
                        "cash_clipped_orders": path_orders["cash_clipped_orders"],
                        "strategy_fill_fraction": path_orders["filled"] / path_orders["accepted"] if path_orders["accepted"] else 0.0,
                        "strategy_requested_fill_fraction": path_orders["filled"] / path_orders["requested"] if path_orders["requested"] else 0.0,
                        "trace_sha256": hasher.hexdigest(), "issuer_valuation_scenario": {"scenario_id": issuer["scenario_id"],
                            "parameters": issuer["parameters"], "path_sha256": canonical_hash(issuer)},
                        "role_wealth_multiple": {role: sum(a["final_wealth_minor"] for a in accounts.values() if a["role"] == role)
                            / sum(a["initial_wealth_minor"] for a in accounts.values() if a["role"] == role)
                            for role in ("aggressive", "conservative", "institutional")}}
                    compare(saved_path["summary"], rebuilt, "entire_fixed_risk_path_summary")
                    require(saved_path["summary"]["trace_sha256"] == hasher.hexdigest(), "complete raw trace hash differs")
                    counts["paths"] += 1
                    if old_stream:
                        require(saved_path["summary"] == original_summary["summary"] and specs == original_summary["participant_specs"], "independent original full summary differs")
                        counts["masked_complete_paths_matched"] += 1
                require(next(stream, None) is None, "full condition ledger has extra rows")
            if old_stream:
                require(next(old_stream, None) is None, "independent legacy archive has extra rows")
        finally:
            if old_stream:
                old_stream.close()
        variant = saved["variant"]
        compare(daily, variant["daily_asset_rows"])
        compare(dict(flows), variant["role_order_quantities"])
        independent = independent_metrics(daily, codes, dates, cfg["joint_limits"])
        for left, right in (("comparison", "comparison"), ("common_factor_metrics", "common_factor_metrics")):
            compare(independent[left], variant[right])
        compare(independent["joint_values"], variant["joint_checks"]["values"])
        compare(independent["joint_criteria"], variant["joint_checks"]["criteria"])
        compare(independent["all_five_pass"], variant["joint_checks"]["all_five_pass"])
        require(variant["total_matched_volume"] == sum(r["matched_volume"] for r in daily), "complete volume aggregation differs")
        measured[cell["name"]] = independent
        receipt_measurements[cell['name']] = {role: {'positions': len(samples),
            'finite_mean_applied_shift_bps': statistics.mean(samples),
            'finite_variance_applied_shift_bps2': statistics.pvariance(samples),
            'private_received_positions': receipt_flags[role]['private_received'],
            'public_received_positions': receipt_flags[role]['public_received'],
            'capped_positions': receipt_flags[role]['capped'],
            'cap_fraction': receipt_flags[role]['capped'] / len(samples)}
            for role, samples in receipt_samples.items()}
        counts["conditions"] += 1
        print(f"Independently audited public information seed {si + 1}/5 condition {ci + 1}/16.", flush=True)
    require(dict(counts) == cfg["per_seed_audit_scope"], "independent full seed audit count differs: " + repr(dict(counts)))
    verify_numeric(loaded[8])
    record = {"status": "PASS_FULL_PUBLIC_INFORMATION_SEED_RAW_LEDGER_AND_STATISTICS", "seed_id": seed["seed_id"],
        "protocol_sha256": file_sha256(CONFIG), "market_dataset_id": market["market_dataset_id"], "checks": dict(counts),
        "independent_variant_metrics": measured, "source_checkpoints": source_bindings,
        "finite_received_message_measurements": receipt_measurements,
        "source_paths_sha256": file_sha256(folder / "prepared/issuer_paths.json.gz")}
    encoded = record_bytes(record)
    target.write_bytes(encoded)
    print("Public information full seed raw ledger, received budgets and independent statistics passed.", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    audit_seed(args.seed)
