"""Five-seed fixed-total initial cash experiment and funding-only same-state audit."""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import tempfile
from collections import Counter
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .audit_cash_resource_controls import initial_state, verify_funding, verify_initial_wealth
from .audit_own_price_feedback import verify_arrival, verify_final_resources
from .audit_pre_wuhan_allocation_attribution_2019 import independent_covariance
from .audit_price_feedback_channels import verify_day, verify_specs
from .cash_resource_controls import CONTROLS, funding_only
from .cash_resource_metrics import expected_coverage, funding_totals, resource_summary
from .industry_shocks import subset_industry_shocks
from .issuer_valuation import subset_issuer_valuation
from .own_price_feedback_metrics import DEFAULT_LIMITS, metrics
from .portfolio_audit import audit_portfolio_day
from .portfolio_cash_resource_controls import simulate_portfolio
from .run_pre_wuhan_background_response_2019 import verify_hashes
from .run_pre_wuhan_own_price_feedback_2019_v2 import controls_for, load_inputs as feedback_inputs, merge, record_bytes, seeded_design
from .semantic_memory_sensitivity import canonical_hash

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_cash_resource_controls_2019.json"
SOURCE = ROOT / "research_outputs/pre_wuhan_price_feedback_channels_2019_v1"
PRIOR = ROOT / "research_outputs/pre_wuhan_order_price_final_verification_20261002.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_cash_resource_controls_2019_v1"
VERSION = "pre-wuhan-fixed-total-group-cash-five-seed-v1"
CELLS = [{"name": a + "_feedback_" + label + "_cash_" + cash, "background_anchor": a,
          "feedback_scale": f, "cash_name": cash, "cash_control": CONTROLS[cash]}
         for a in ("initial_inventory", "current_inventory") for label, f in (("full", 1.0), ("both_off", 0.0))
         for cash in ("original_cash", "less_background", "more_background")]


def load_inputs():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    loaded = feedback_inputs()
    (_, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades,
     membership, catalog, shared, issuer, feature_checks, industry_config, _) = loaded
    source = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    if (cfg["version"] != VERSION or cfg["variants"] != CELLS or cfg["joint_limits"] != DEFAULT_LIMITS
            or cfg["seeds"] != source["seeds"] or cfg["agent_signal_enabled"] is not False
            or cfg["dense_audit_sessions"] != [0, 10] or source["sample"]["selected_stock_codes"] != codes
            or len(codes) != 123 or len(dates) != 43):
        raise ValueError("frozen cash-resource experimental scope differs")
    prior = json.loads(PRIOR.read_text(encoding="utf-8"))
    if prior["status"] != "PASS_JSON_BOOK_AUDIT_WITH_RENDERER_ENVIRONMENT_EXCEPTION":
        raise ValueError("prior accepted-book audit is incomplete")
    merge(inputs, prior["inputs"])
    merge(inputs, cfg["inputs"])
    merge(inputs, {str(CONFIG): file_sha256(CONFIG), str(PRIOR): file_sha256(PRIOR)})
    verify_hashes(inputs)
    verify_hashes(code_hashes)
    return cfg, base, replay, market, codes, dates, joined, inputs, code_hashes, shared, issuer, feature_checks, industry_config, source


def legacy_next(stream, identity):
    for line in stream:
        row = json.loads(line)
        if row["variant"].endswith("_target_off") or row["variant"].endswith("_quote_off"):
            continue
        if (row["seed_id"], row["variant"], row["basket_index"], row["portfolio_auction"]["session"]) != identity:
            raise ValueError("cash baseline source path/day identity differs")
        return {k: v for k, v in row.items() if k not in {"seed_id", "variant", "basket_index"}}
    raise ValueError("cash baseline source ended early")


def run(output=OUTPUT, audit_existing=False):
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or output.exists() and not audit_existing:
        raise ValueError("cash-resource experiment requires a fresh direct research_outputs directory")
    cfg, base, replay, market, codes, dates, joined, inputs, code_hashes, shared, full_issuer, feature_checks, industry_config, source = load_inputs()
    agents = [AgentParameters(**a) for a in replay["agents"]]
    case = next(c for c in base["cases"] if c["case_id"] == "separated_institution35")
    response = industry_config["background_response"]
    baskets = [codes[i:i + 3] for i in range(0, len(codes), 3)]
    source_paths = {(r["seed_id"], r["variant"], r["basket_index"]): r for r in source["path_summaries"]}
    paths, variants, groups, checks = [], [], {}, Counter()
    with tempfile.TemporaryDirectory(prefix="cash-resource-stage-", dir=output.parent) as temp:
        stage = Path(temp)
        with (stage / "ledger.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as ledger, \
             (stage / "funding_only.jsonl.gz").open("wb") as hypo_raw, gzip.GzipFile(fileobj=hypo_raw, mode="wb", filename="", mtime=0) as hypo, \
             gzip.open(SOURCE / "ledger.jsonl.gz", "rt", encoding="utf-8") as old_stream:
            for seed in cfg["seeds"]:
                core, background, seeded_issuer = seeded_design(base, full_issuer, seed)
                seeded_base = {**base, "core": core, "background": background}
                receipt_hashes = {}
                for cell in CELLS:
                    cash, scale, anchor = cell["cash_control"], cell["feedback_scale"], cell["background_anchor"]
                    controls, daily, flow, coverage = controls_for(anchor), [], Counter(), Counter()
                    old_name = anchor + "_feedback_" + ("full" if scale else "both_off")
                    for basket_index, basket in enumerate(baskets):
                        shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {a: [0.0] * len(dates) for a in basket}}
                        industry, issuer = subset_industry_shocks(shared["industry"], basket), subset_issuer_valuation(seeded_issuer, basket)
                        simulated = simulate_portfolio({a: joined[a] for a in basket}, agents, core, background, base["venue"],
                            base["feedback_parameters"], case, False, scenario_shocks=shocks, background_response=response,
                            industry_shocks=industry, issuer_valuation=issuer, quantity_controls=controls,
                            target_feedback_scale=scale, quote_feedback_scale=scale, cash_resource_control=cash)
                        specs = simulated["participant_specs"]
                        verify_specs(specs, source_paths[seed["seed_id"], anchor + "_feedback_full", basket_index]["participant_specs"], scale, scale)
                        if cell["cash_name"] == "original_cash":
                            old = source_paths[seed["seed_id"], old_name, basket_index]
                            if simulated["summary"] != old["summary"] or specs != old["participant_specs"] or "initial_cash_resource_receipt" in simulated:
                                raise ValueError("original resources do not match complete legacy path summary/specs")
                            checks["legacy_complete_paths_matched"] += 1
                        state = initial_state(specs, basket, seeded_base, case, cash, simulated.get("initial_cash_resource_receipt"))
                        verify_initial_wealth(state, simulated["summary"])
                        if cell["cash_name"] != "original_cash":
                            checks["initial_resource_receipts_audited"] += 1
                        histories = {a: [] for a in basket}
                        streaks = {n: {"sign": 0, "streak": 0} for n, s in specs.items() if s["kind"] == "strategy"}
                        for session, day in enumerate(simulated["trace"]):
                            if (day["trade_date"] != dates[session] or day["signal_cutoff_date"] != joined[basket[0]][session]["signal_cutoff_date"]
                                    or day["execution_reference_date"] != joined[basket[0]][session]["execution_reference_date"]
                                    or day.get("initial_cash_resource_control") != (None if cash == CONTROLS["original_cash"] else cash)
                                    or day.get("quantity_control_parameters") != controls):
                                raise ValueError("cash path clock/control metadata differs")
                            verify_arrival(day, specs, core, session)
                            independent_covariance(day, histories, base["feedback_parameters"])
                            if cell["cash_name"] == "original_cash":
                                if legacy_next(old_stream, (seed["seed_id"], old_name, basket_index, session)) != day:
                                    raise ValueError("entire original-cash daily object differs from legacy")
                                checks["legacy_daily_records_matched"] += 1
                                for cash_name, alternative in CONTROLS.items():
                                    payload = funding_only(day, state, specs, base["venue"], alternative)
                                    verify_funding(payload, day, state, specs, base["venue"], alternative)
                                    if cash_name == "original_cash":
                                        checks["baseline_funding_records_audited"] += 1
                                        continue
                                    key = f"{seed['seed_id']}|{anchor}|{scale}|{cash_name}"
                                    group = groups.setdefault(key, {"records": 0, "role_quantities": {}})
                                    group["records"] += 1
                                    for role, values in funding_totals(payload, specs).items():
                                        target = group["role_quantities"].setdefault(role, Counter())
                                        target.update(values)
                                    hypo.write(record_bytes({"seed_id": seed["seed_id"], "base_variant": cell["name"],
                                        "background_anchor": anchor, "feedback_scale": scale, "basket_index": basket_index,
                                        "session": session, "cash_name": cash_name, "prior_state_sha256": canonical_hash(state), **payload}))
                                    checks["same_state_funding_records"] += 1
                            receipt_key = basket_index, session
                            receipt_hash = canonical_hash(day["issuer_information_receipts"])
                            if cell == CELLS[0]:
                                receipt_hashes[receipt_key] = receipt_hash
                            elif receipt_hash != receipt_hashes[receipt_key]:
                                raise ValueError("initial cash allocation changed synthetic private message draws")
                            else:
                                checks["paired_private_receipt_values"] += sum(len(v) for v in day["issuer_information_receipts"].values())
                            coverage.update(verify_day(day, state, specs, base["venue"], background, response, shocks, industry,
                                issuer, controls, case, streaks, session, scale, scale))
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
                            "participant_specs": specs, "summary": simulated["summary"],
                            "initial_cash_resource_receipt": simulated.get("initial_cash_resource_receipt")})
                        if (basket_index + 1) % 10 == 0 or basket_index == len(baskets) - 1:
                            print(f"{seed['seed_id']} {cell['name']}: audited {basket_index + 1}/{len(baskets)} baskets", flush=True)
                    variants.append({"seed_id": seed["seed_id"], **cell, **metrics(daily, cfg["joint_limits"]),
                        "coverage": dict(coverage), "role_order_quantities": dict(flow), "daily_asset_rows": daily,
                        "total_matched_volume": sum(r["matched_volume"] for r in daily)})
            if next(old_stream, None) is not None:
                raise ValueError("legacy original-cash bridge has unread extra source records")
        checks["conditions"], checks["paths"] = len(variants), len(paths)
        dimensions = expected_coverage(len(cfg["seeds"]), len(baskets), len(dates), 3, 12, base["background"]["participants"])
        if dimensions != cfg["derived_expected_coverage"] or dict(checks) != dimensions:
            raise ValueError("cash-resource declared full coverage differs: " + json.dumps(dict(checks), sort_keys=True))
        result = {"pipeline_version": VERSION, "agent_signal_enabled": False, "sample": source["sample"],
            "market_dataset_id": market["market_dataset_id"], "seeds": cfg["seeds"], "limits": cfg["joint_limits"],
            "variants": variants, "path_summaries": paths, **resource_summary(variants), "funding_only_groups": groups,
            "checks": dict(checks), "source_feature_checks": feature_checks, "interpretation": cfg["interpretation"]}
        (stage / "results.json").write_bytes(record_bytes({"result": result}))
        verify_hashes(inputs)
        verify_hashes(code_hashes)
        manifest = {"pipeline_version": VERSION, "inputs": inputs, "code_sha256": code_hashes,
            "artifacts": {n: {"sha256": file_sha256(stage / n)} for n in ("results.json", "ledger.jsonl.gz", "funding_only.jsonl.gz")}}
        (stage / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"))
        if audit_existing:
            for name in ("results.json", "ledger.jsonl.gz", "funding_only.jsonl.gz", "manifest.json"):
                if file_sha256(stage / name) != file_sha256(output / name):
                    raise ValueError("cash-resource artifact did not rebuild byte-identically: " + name)
            print("All four fixed-total cash-resource artifacts rebuilt byte-identically.", flush=True)
        else:
            stage.replace(output)
            print(json.dumps(dict(checks), sort_keys=True), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    run(args.output_dir, args.audit_existing)
