"""Execute one frozen experimental condition, preserving the complete original loop."""
from __future__ import annotations
import gzip
import json
from collections import Counter
from .run_pre_wuhan_public_factor_channels_2019 import (AgentParameters, controls_for, subset_industry_shocks,
    subset_issuer_valuation, subset, simulate_portfolio, verify_specs, initial_from_specs, verify_arrival,
    independent_covariance, legacy_next, MODES, same_state_orders, verify_same_state, canonical_hash,
    verify_day, audit_portfolio_day, verify_final_resources, metrics, record_bytes, CELLS, seeded_design)


def execute_cell(loaded, seed, cell, public, receipt_hashes, legacy_path, stage):
    cfg, base, replay, market, codes, dates, groups, joined, inputs, code_hashes, shared, full_issuer, feature_checks, industry_config, source = loaded
    agents = [AgentParameters(**a) for a in replay["agents"]]
    case = next(c for c in base["cases"] if c["case_id"] == "separated_institution35")
    response = industry_config["background_response"]
    baskets = [codes[i:i + 3] for i in range(0, len(codes), 3)]
    source_paths = {(r["seed_id"], r["variant"], r["basket_index"]): r for r in source["path_summaries"]}
    core, background, seeded_issuer = seeded_design(base, full_issuer, seed)
    seeded_base = {**base, "core": core, "background": background}
    paths, variants, checks = [], [], Counter()
    with (stage / "ledger.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as ledger, \
         (stage / "same_state_orders.jsonl.gz").open("wb") as hypo_raw, gzip.GzipFile(fileobj=hypo_raw, mode="wb", filename="", mtime=0) as hypo, \
         gzip.open(legacy_path, "rt", encoding="utf-8") as old_stream:
        scale, anchor, pub = cell["feedback_scale"], cell["background_anchor"], cell["public_controls"]
        controls, daily, flow, coverage = controls_for(anchor), [], Counter(), Counter()
        old_name = anchor + "_feedback_" + ("full" if scale else "both_off")
        for basket_index, basket in enumerate(baskets):
            shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {a: [0.0] * len(dates) for a in basket}}
            industry = subset_industry_shocks(shared["industry"], basket)
            issuer, factor = subset_issuer_valuation(seeded_issuer, basket), subset(public, basket)
            simulated = simulate_portfolio({a: joined[a] for a in basket}, agents, core, background, base["venue"],
                base["feedback_parameters"], case, False, scenario_shocks=shocks, background_response=response,
                industry_shocks=industry, issuer_valuation=issuer, quantity_controls=controls,
                target_feedback_scale=scale, quote_feedback_scale=scale,
                public_factor_path=factor if pub is not None else None, public_factor_controls=pub)
            specs = simulated["participant_specs"]
            verify_specs(specs, source_paths[seed["seed_id"], anchor + "_feedback_full", basket_index]["participant_specs"], scale, scale, pub)
            if pub is None:
                old = source_paths[seed["seed_id"], old_name, basket_index]
                if simulated["summary"] != old["summary"] or specs != old["participant_specs"]:
                    raise ValueError("public none cell differs from complete legacy path summary/specs")
                checks["legacy_complete_paths_matched"] += 1
            state = initial_from_specs(specs, basket, seeded_base, case)
            histories = {a: [] for a in basket}
            streaks = {n: {"sign": 0, "streak": 0} for n, s in specs.items() if s["kind"] == "strategy"}
            for session, day in enumerate(simulated["trace"]):
                if (day["trade_date"] != dates[session] or day["signal_cutoff_date"] != joined[basket[0]][session]["signal_cutoff_date"]
                        or day["execution_reference_date"] != joined[basket[0]][session]["execution_reference_date"]
                        or day.get("quantity_control_parameters") != controls or day.get("public_factor_controls") != pub):
                    raise ValueError("public path clock/control metadata differs")
                verify_arrival(day, specs, core, session)
                independent_covariance(day, histories, base["feedback_parameters"])
                if pub is None:
                    if legacy_next(old_stream, (seed["seed_id"], old_name, basket_index, session)) != day:
                        raise ValueError("entire public none daily object differs from legacy")
                    checks["legacy_daily_records_matched"] += 1
                    for mode, alternative in MODES.items():
                        payload = same_state_orders(day, state, specs, base["venue"], case, streaks,
                            {a: factor["by_stock"][a][session] for a in basket}, factor["parameters"], alternative, background, response)
                        tested = verify_same_state(payload, day, state, specs, base["venue"], background, response,
                            shocks, industry, issuer, controls, case, streaks, session, scale, scale, factor, alternative)
                        for k in ("quote_only_same_state_strategy_targets", "target_only_same_state_backgrounds"):
                            checks[k] += tested.get(k, 0)
                        if alternative is None:
                            checks["baseline_same_state_records_audited"] += 1
                        else:
                            hypo.write(record_bytes({"seed_id": seed["seed_id"], "base_variant": cell["name"],
                                "basket_index": basket_index, "session": session, "public_mode": mode,
                                "prior_state_sha256": canonical_hash(state), **payload}))
                            checks["same_state_public_records"] += 1
                receipt_key = basket_index, session
                h = canonical_hash(day["issuer_information_receipts"])
                if cell == CELLS[0]:
                    receipt_hashes[receipt_key] = h
                elif h != receipt_hashes[receipt_key]:
                    raise ValueError("public intervention changed private information values")
                else:
                    checks["paired_private_receipt_values"] += sum(len(v) for v in day["issuer_information_receipts"].values())
                coverage.update(verify_day(day, state, specs, base["venue"], background, response, shocks, industry, issuer,
                    controls, case, streaks, session, scale, scale, factor if pub is not None else None, pub))
                for a, call in day["portfolio_auction"]["asset_calls"].items():
                    for o in call["orders"]:
                        role = "background" if specs[o["owner"]]["kind"] == "background" else specs[o["owner"]]["parameters"]["role"]
                        for label, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                            flow[role + "|" + o["side"] + "|" + label] += o[field]
                        if "cash_and_fee_reservation" in o["reasons"]:
                            flow[role + "|" + o["side"] + "|cash_clipped"] += o["quantity"] - o["accepted_quantity"]
                    daily.append({"stock_code": a, "trade_date": day["trade_date"], "signal_cutoff_date": day["signal_cutoff_date"],
                        "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                        "matched_volume": call["matched_volume"], "observed_return": joined[a][session]["observed_return"]})
                    histories[a].append({"trade_date": day["trade_date"], "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"]})
                state = audit_portfolio_day(day, state, base["venue"], session, dense=session in cfg["dense_audit_sessions"])
                ledger.write(record_bytes({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": basket_index, **day}))
                checks["full_path_ledger_records"] += 1
                checks["asset_calls"] += len(basket)
                checks["strategy_allocation_checks"] += len(streaks)
                if session in cfg["dense_audit_sessions"]:
                    checks["dense_asset_calls"] += len(basket)
            verify_final_resources(state, simulated["summary"])
            paths.append({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": basket_index,
                "participant_specs": specs, "summary": simulated["summary"]})
            if (basket_index + 1) % 10 == 0 or basket_index == len(baskets) - 1:
                print(f"{seed['seed_id']} {cell['name']}: audited {basket_index + 1}/{len(baskets)} baskets", flush=True)
        variants.append({"seed_id": seed["seed_id"], **cell, **metrics(daily, cfg["joint_limits"]), "coverage": dict(coverage),
            "role_order_quantities": dict(flow), "daily_asset_rows": daily, "total_matched_volume": sum(r["matched_volume"] for r in daily)})
        if next(old_stream, None) is not None:
            raise ValueError("condition legacy partition has unread records")
    if len(variants) != 1 or len(paths) != 41 or checks["full_path_ledger_records"] != 1763:
        raise ValueError("condition checkpoint incomplete")
    result = {"paths": paths, "variant": variants[0], "checks": dict(checks),
              "receipt_hashes": {f"{b}|{s}": h for (b, s), h in sorted(receipt_hashes.items())}}
    (stage / "condition.json").write_bytes(record_bytes(result))
    return result
