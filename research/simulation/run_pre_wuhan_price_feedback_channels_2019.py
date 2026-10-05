"""Frozen five-seed target-by-quote feedback factorial, both inventory anchors."""
from __future__ import annotations

import argparse
import gzip
import json
import tempfile
from collections import Counter
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .audit_own_price_feedback import verify_arrival, verify_final_resources
from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs, independent_covariance
from .audit_price_feedback_channels import verify_day, verify_same_state, verify_specs
from .industry_shocks import subset_industry_shocks
from .issuer_valuation import subset_issuer_valuation
from .own_price_feedback import order_totals
from .own_price_feedback_metrics import DEFAULT_LIMITS, metrics, seed_summary
from .portfolio_audit import audit_portfolio_day
from .portfolio_price_feedback_channels import simulate_portfolio
from .price_feedback_channel_metrics import expected_coverage, factorial_summary
from .price_feedback_channels import diagonal_as_legacy, same_state_orders
from .run_pre_wuhan_background_response_2019 import verify_hashes
from .run_pre_wuhan_own_price_feedback_2019_v2 import (controls_for, load_inputs as feedback_inputs,
                                                      merge, record_bytes, seeded_design)
from .run_pre_wuhan_quantity_controls_2019 import decision_branch
from .semantic_memory_sensitivity import canonical_hash

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_price_feedback_channels_2019.json"
SOURCE = ROOT / "research_outputs/pre_wuhan_own_price_feedback_2019_v2"
OUTPUT = ROOT / "research_outputs/pre_wuhan_price_feedback_channels_2019_v1"
VERSION = "pre-wuhan-target-quote-feedback-factorial-five-seed-v1"
CELLS = [{"name": anchor + "_feedback_" + label, "background_anchor": anchor,
          "target_scale": target, "quote_scale": quote}
         for anchor in ("initial_inventory", "current_inventory")
         for label, target, quote in (("full", 1.0, 1.0), ("target_off", 0.0, 1.0),
                                      ("quote_off", 1.0, 0.0), ("both_off", 0.0, 0.0))]
CODE_FILES = ["research/simulation/price_feedback_channels.py", "research/simulation/portfolio_price_feedback_channels.py",
              "research/simulation/audit_price_feedback_channels.py", "research/simulation/price_feedback_channel_metrics.py",
              "research/simulation/run_pre_wuhan_price_feedback_channels_2019.py",
              "research/simulation/verify_pre_wuhan_price_feedback_channels_2019.py",
              "tests/test_price_feedback_channels.py", "tests/test_price_feedback_channel_metrics.py"]


def load_inputs():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    loaded = feedback_inputs()
    (prior_cfg, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades,
     membership, catalog, shared, issuer, feature_checks, industry_config, _) = loaded
    if (cfg["version"] != VERSION or cfg["variants"] != CELLS or cfg["seeds"] != prior_cfg["seeds"]
            or cfg["joint_limits"] != DEFAULT_LIMITS or cfg["dense_audit_sessions"] != [0, 10]
            or cfg["agent_signal_enabled"] is not False):
        raise ValueError("frozen target/quote channel design differs")
    merge(inputs, {str(CONFIG): file_sha256(CONFIG)})
    merge(inputs, cfg["inputs"])
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    for field in ("inputs", "code_sha256"):
        merge(inputs, manifest[field])
    merge(inputs, {str(SOURCE / "manifest.json"): file_sha256(SOURCE / "manifest.json")})
    for name, info in manifest["artifacts"].items():
        merge(inputs, {str(SOURCE / name): info["sha256"]})
    for rel in CODE_FILES:
        path = ROOT / rel
        code_hashes[str(path)] = file_sha256(path)
    verify_hashes(inputs)
    verify_hashes(code_hashes)
    reference = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    if reference["sample"]["selected_stock_codes"] != codes or len(codes) != 123 or len(dates) != 43:
        raise ValueError("channel cohort/calendar differs")
    return cfg, base, replay, market, codes, dates, joined, inputs, code_hashes, upgrades, shared, issuer, feature_checks, industry_config, reference


def legacy_next(stream, seed_id, variant, basket_index, session):
    for line in stream:
        row = json.loads(line)
        if row["variant"].endswith("_feedback_half"):
            continue
        if (row["seed_id"], row["variant"], row["basket_index"], row["portfolio_auction"]["session"]) != (seed_id, variant, basket_index, session):
            raise ValueError("legacy diagonal source order/identity differs")
        return {key: value for key, value in row.items() if key not in {"seed_id", "variant", "basket_index"}}
    raise ValueError("legacy diagonal source ended early")


def quantities(payload):
    return {stock: [{key: value for key, value in order.items() if key != "limit_price_minor"}
                    for order in orders] for stock, orders in payload["orders"].items()}


def without_quote_metadata(payload):
    return {name: {key: value for key, value in decision.items() if key != "quote_base_beliefs"}
            for name, decision in payload["decisions"].items()}


def run(output=OUTPUT, audit_existing=False):
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or (output.exists() and not audit_existing):
        raise ValueError("channel study requires a fresh direct research_outputs directory")
    cfg, base, replay, market, codes, dates, joined, inputs, code_hashes, upgrades, shared, full_issuer, feature_checks, industry_config, reference = load_inputs()
    agents = [AgentParameters(**row) for row in replay["agents"]]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    response = industry_config["background_response"]
    baskets = [codes[index:index + 3] for index in range(0, len(codes), 3)]
    source_paths = {(row["seed_id"], row["variant"], row["basket_index"]): row for row in reference["path_summaries"]}
    variants, paths, groups, checks = [], [], {}, Counter()
    with tempfile.TemporaryDirectory(prefix="price-feedback-channels-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        with (stage / "ledger.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as ledger, \
             (stage / "same_state_orders.jsonl.gz").open("wb") as same_raw, gzip.GzipFile(fileobj=same_raw, mode="wb", filename="", mtime=0) as same_ledger, \
             gzip.open(SOURCE / "ledger.jsonl.gz", "rt", encoding="utf-8") as legacy, \
             gzip.open(SOURCE / "same_state_orders.jsonl.gz", "rt", encoding="utf-8") as legacy_same:
            for seed in cfg["seeds"]:
                core, background, seeded_issuer = seeded_design(base, full_issuer, seed)
                seeded_base = {**base, "core": core, "background": background}
                receipt_hashes = {}
                for cell in CELLS:
                    anchor, target, quote = cell["background_anchor"], cell["target_scale"], cell["quote_scale"]
                    controls = controls_for(anchor)
                    daily, coverage, branches, flows = [], Counter(), Counter(), Counter()
                    for basket_index, basket in enumerate(baskets):
                        shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {stock: [0.0] * len(dates) for stock in basket}}
                        industry = subset_industry_shocks(shared["industry"], basket)
                        issuer = subset_issuer_valuation(seeded_issuer, basket)
                        simulated = simulate_portfolio({stock: joined[stock] for stock in basket}, agents, core, background,
                                                       base["venue"], base["feedback_parameters"], case, False,
                                                       scenario_shocks=shocks, background_response=response, industry_shocks=industry,
                                                       issuer_valuation=issuer, quantity_controls=controls,
                                                       target_feedback_scale=target, quote_feedback_scale=quote)
                        specs = simulated["participant_specs"]
                        baseline = source_paths[seed["seed_id"], anchor + "_feedback_full", basket_index]
                        verify_specs(specs, baseline["participant_specs"], target, quote)
                        projected = None
                        if target == quote:
                            projected = diagonal_as_legacy(simulated, target)
                            legacy_name = anchor + "_feedback_" + ("full" if target == 1 else "off")
                            original = source_paths[seed["seed_id"], legacy_name, basket_index]
                            if projected["summary"] != original["summary"] or projected["participant_specs"] != original["participant_specs"]:
                                raise ValueError("complete channel diagonal summary/specs differs from archived paths")
                        state = initial_from_specs(specs, basket, seeded_base, case)
                        histories = {stock: [] for stock in basket}
                        streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
                        for session, day in enumerate(simulated["trace"]):
                            if (day["trade_date"] != dates[session] or day["signal_cutoff_date"] != joined[basket[0]][session]["signal_cutoff_date"]
                                    or day["execution_reference_date"] != joined[basket[0]][session]["execution_reference_date"]
                                    or day.get("quantity_control_parameters") != controls):
                                raise ValueError("channel date/reference/quantity control differs")
                            verify_arrival(day, specs, core, session)
                            independent_covariance(day, histories, base["feedback_parameters"])
                            if projected is not None:
                                if legacy_next(legacy, seed["seed_id"], legacy_name, basket_index, session) != projected["trace"][session]:
                                    raise ValueError("complete diagonal daily object differs")
                                checks["legacy_diagonal_daily_records_matched"] += 1
                            receipt_key = basket_index, session
                            receipt_hash = canonical_hash(day["issuer_information_receipts"])
                            if cell == CELLS[0]:
                                receipt_hashes[receipt_key] = receipt_hash
                            elif receipt_hash != receipt_hashes[receipt_key]:
                                raise ValueError("target/quote control changed private receipt draws")
                            else:
                                checks["paired_private_receipt_values"] += sum(len(rows) for rows in day["issuer_information_receipts"].values())
                            if target == quote == 1:
                                payloads = {}
                                for t, q in ((1.0, 1.0), (0.0, 1.0), (1.0, 0.0), (0.0, 0.0)):
                                    payload = same_state_orders(day, state, specs, case, base["venue"], streaks, t, q)
                                    same_counts = verify_same_state(payload, day, state, specs, base["venue"], issuer, case, streaks, session, t, q)
                                    checks["same_state_strategy_allocations_audited"] += same_counts["strategy_allocation_checks"]
                                    payloads[t, q] = payload
                                    if t == q == 1:
                                        checks["same_state_original_decision_order_matches"] += len(payload["decisions"])
                                        continue
                                    key = f"{seed['seed_id']}:{anchor}:{t}:{q}"
                                    group = groups.setdefault(key, {"seed_id": seed["seed_id"], "background_anchor": anchor,
                                                                   "target_scale": t, "quote_scale": q, "records": 0, "requested_buy": 0, "requested_sell": 0})
                                    totals = order_totals(payload["orders"])
                                    group["records"] += 1
                                    for side in ("buy", "sell"):
                                        group["requested_" + side] += totals[side]
                                    same_ledger.write(record_bytes({"seed_id": seed["seed_id"], "background_anchor": anchor,
                                                                   "basket_index": basket_index, "session": session,
                                                                   "target_scale": t, "quote_scale": q,
                                                                   "prior_state_sha256": canonical_hash(state),
                                                                   "prior_streaks_sha256": canonical_hash(streaks), **payload}))
                                    checks["same_state_records"] += 1
                                if (without_quote_metadata(payloads[1, 0]) != payloads[1, 1]["decisions"]
                                        or quantities(payloads[1, 0]) != quantities(payloads[1, 1])
                                        or without_quote_metadata(payloads[0, 0]) != without_quote_metadata(payloads[0, 1])
                                        or quantities(payloads[0, 0]) != quantities(payloads[0, 1])):
                                    raise ValueError("physical quote feedback changed same-state allocation or quantities")
                                checks["quote_only_quantity_identity_checks"] += len(payloads[1, 0]["decisions"])
                                for name, decision in payloads[0, 1]["decisions"].items():
                                    if decision["quote_base_beliefs"] != payloads[1, 1]["decisions"][name]["issuer_base_beliefs"]:
                                        raise ValueError("target feedback altered same-state physical quote base")
                                    checks["target_only_quote_base_identity_checks"] += len(basket)
                                old_half, old_off = json.loads(next(legacy_same)), json.loads(next(legacy_same))
                                if ((old_half["seed_id"], old_half["background_anchor"], old_half["basket_index"], old_half["session"], old_half["scale"]) != (seed["seed_id"], anchor, basket_index, session, 0.5)
                                        or (old_off["seed_id"], old_off["background_anchor"], old_off["basket_index"], old_off["session"], old_off["scale"]) != (seed["seed_id"], anchor, basket_index, session, 0.0)
                                        or old_off["prior_state_sha256"] != canonical_hash(state)
                                        or old_off["prior_streaks_sha256"] != canonical_hash(streaks)
                                        or old_off["decisions"] != without_quote_metadata(payloads[0, 0])
                                        or old_off["orders"] != payloads[0, 0]["orders"]):
                                    raise ValueError("channel both-off same-state bridge differs from archived old records")
                                checks["legacy_same_state_records_matched"] += 1
                            coverage.update(verify_day(day, state, specs, base["venue"], background, response, shocks, industry,
                                                        issuer, controls, case, streaks, session, target, quote))
                            for name, decision in day["decisions"].items():
                                branches[specs[name]["parameters"]["role"] + ":" + decision_branch(decision)] += 1
                            for stock, call in day["portfolio_auction"]["asset_calls"].items():
                                for order in call["orders"]:
                                    if specs[order["owner"]]["kind"] != "strategy":
                                        continue
                                    label = specs[order["owner"]]["parameters"]["role"] + ":" + decision_branch(day["decisions"][order["owner"]]) + ":" + order["side"]
                                    for stage_name, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                                        flows[label + ":" + stage_name] += order[field]
                                daily.append({"stock_code": stock, "trade_date": day["trade_date"], "signal_cutoff_date": day["signal_cutoff_date"],
                                              "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                                              "matched_volume": call["matched_volume"], "observed_return": joined[stock][session]["observed_return"]})
                                histories[stock].append({"trade_date": day["trade_date"], "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"]})
                            state = audit_portfolio_day(day, state, base["venue"], session, dense=session in cfg["dense_audit_sessions"])
                            coverage["settlement_asset_calls"] += len(basket)
                            ledger.write(record_bytes({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": basket_index, **day}))
                            checks["full_path_ledger_records"] += 1
                        verify_final_resources(state, simulated["summary"])
                        paths.append({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": basket_index,
                                      "participant_specs": specs, "summary": simulated["summary"]})
                        if (basket_index + 1) % 10 == 0 or basket_index + 1 == len(baskets):
                            print(f"{seed['seed_id']} {cell['name']}: audited {basket_index + 1}/{len(baskets)} baskets", flush=True)
                    variants.append({"seed_id": seed["seed_id"], **cell, **metrics(daily, cfg["joint_limits"]),
                                     "coverage": dict(coverage), "total_matched_volume": sum(row["matched_volume"] for row in daily),
                                     "strategy_decision_branches": dict(branches), "strategy_branch_order_quantities": dict(flows), "daily_asset_rows": daily})
            if next(legacy, None) is not None or next(legacy_same, None) is not None:
                raise ValueError("legacy bridge has unmatched extra records")
        baseline_specs = source_paths[cfg["seeds"][0]["seed_id"], "initial_inventory_feedback_full", 0]["participant_specs"]
        dimensions = expected_coverage(len(cfg["seeds"]), CELLS, len(baskets), len(dates),
                                       sum(spec["kind"] == "strategy" for spec in baseline_specs.values()),
                                       len(baskets[0]), base["background"]["participants"])
        checks["paths"] = len(paths)
        differences = {key: {"expected": value, "actual": checks.get(key)} for key, value in dimensions.items() if checks.get(key) != value}
        if dimensions != cfg["derived_expected_coverage"] or differences:
            raise ValueError("full channel/bridge coverage incomplete: " + json.dumps(differences, sort_keys=True))
        result = {"pipeline_version": VERSION, "agent_signal_enabled": False, "sample": reference["sample"],
                  "market_dataset_id": market["market_dataset_id"], "seeds": cfg["seeds"], "limits": cfg["joint_limits"],
                  "variants": variants, "path_summaries": paths, "seed_summary": seed_summary(variants),
                  "factorial_summary": factorial_summary(variants), "same_state_groups": groups,
                  "checks": {**dict(checks), "source_feature_checks": feature_checks}, "interpretation": cfg["interpretation"]}
        (stage / "results.json").write_bytes(record_bytes({"result": result}))
        verify_hashes(inputs)
        verify_hashes(code_hashes)
        manifest = {"pipeline_version": VERSION, "inputs": inputs, "code_sha256": code_hashes,
                    "reviewed_reference_code_upgrades": upgrades,
                    "artifacts": {name: {"sha256": file_sha256(stage / name)} for name in ("results.json", "ledger.jsonl.gz", "same_state_orders.jsonl.gz")}}
        (stage / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"))
        if audit_existing:
            for name in ("results.json", "ledger.jsonl.gz", "same_state_orders.jsonl.gz", "manifest.json"):
                if file_sha256(stage / name) != file_sha256(output / name):
                    raise ValueError("channel artifact differs byte-for-byte: " + name)
            print("Target/quote channel artifacts rebuilt byte-identically.", flush=True)
        else:
            stage.replace(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = run(args.output_dir, args.audit_existing)
    print(json.dumps({"checks": result["checks"], "factorial_summary": result["factorial_summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
