"""Run the frozen five-seed own-price feedback and inventory-anchor study."""
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
from .audit_own_price_feedback import (verify_arrival, verify_final_resources, verify_same_state, verify_specs)
from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs, independent_covariance
from .audit_quantity_controls import verify_quantity_day
from .industry_shocks import subset_industry_shocks
from .issuer_valuation import RISK_FIELDS, build_issuer_valuation, subset_issuer_valuation
from .own_price_feedback import order_totals, same_state_orders
from .own_price_feedback_metrics import DEFAULT_LIMITS, metrics, seed_summary
from .portfolio_audit import audit_portfolio_day
from .portfolio_own_price_feedback import simulate_portfolio
from .run_pre_wuhan_background_response_2019 import verify_hashes
from .run_pre_wuhan_issuer_valuation_2019 import reference_days
from .run_pre_wuhan_quantity_controls_2019 import decision_branch, load_inputs as quantity_inputs
from .semantic_memory_sensitivity import canonical_hash

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_own_price_feedback_2019.json"
SOURCE = ROOT / "research_outputs/pre_wuhan_quantity_controls_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_own_price_feedback_2019_v1"
VERSION = "pre-wuhan-own-price-feedback-five-seed-full-path-v1"
CELLS = [{"name": anchor + "_feedback_" + label, "background_anchor": anchor, "scale": scale}
         for anchor in ("initial_inventory", "current_inventory")
         for label, scale in (("full", 1.0), ("half", 0.5), ("off", 0.0))]
CODE_FILES = ["research/simulation/own_price_feedback.py", "research/simulation/portfolio_own_price_feedback.py",
              "research/simulation/audit_own_price_feedback.py", "research/simulation/own_price_feedback_metrics.py",
              "research/simulation/run_pre_wuhan_own_price_feedback_2019.py",
              "research/simulation/verify_pre_wuhan_own_price_feedback_2019.py",
              "research/simulation/audit_pre_wuhan_allocation_attribution_2019.py",
              "tests/test_own_price_feedback.py", "tests/test_own_price_feedback_metrics.py"]


def merge(binding, additions):
    for name, value in additions.items():
        path = str((ROOT / name).resolve())
        if path in binding and binding[path] != value:
            raise ValueError("conflicting frozen feedback binding")
        binding[path] = value


def load_inputs():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (cfg["version"] != VERSION or cfg["variants"] != CELLS or cfg["joint_limits"] != DEFAULT_LIMITS
            or cfg["dense_audit_sessions"] != [0, 10] or cfg["agent_signal_enabled"] is not False
            or [row["arrival_seed"] for row in cfg["seeds"]] != [7, 11, 23, 47, 89]
            or [row["background_seed"] for row in cfg["seeds"]] != [7, 11, 23, 47, 89]
            or len({row["seed_id"] for row in cfg["seeds"]}) != 5):
        raise ValueError("frozen own-price feedback design changed")
    loaded = quantity_inputs()
    (_, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership,
     catalog, shared, issuer, feature_checks, _, _, industry_config) = loaded
    merge(inputs, {str(CONFIG): file_sha256(CONFIG)})
    merge(inputs, cfg["inputs"])
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    for key in ("inputs", "code_sha256"):
        merge(inputs, manifest[key])
    for name, info in manifest["artifacts"].items():
        merge(inputs, {str(SOURCE / name): info["sha256"]})
    for rel in CODE_FILES:
        path = ROOT / rel
        code_hashes[str(path)] = file_sha256(path)
    verify_hashes(inputs)
    verify_hashes(code_hashes)
    reference = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    if reference["sample"]["selected_stock_codes"] != codes or len(codes) != 123 or len(dates) != 43:
        raise ValueError("feedback study lost frozen cohort/date coverage")
    first = cfg["seeds"][0]
    if (first["innovation_seed"] != issuer["parameters"]["innovation_seed"]
            or first["information_seed"] != issuer["parameters"]["information_seed"]):
        raise ValueError("first feedback seed no longer reproduces original messages")
    return cfg, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership, catalog, shared, issuer, feature_checks, industry_config, reference


def seeded_design(base, full_issuer, seed):
    core = {**base["core"], "seed": seed["arrival_seed"]}
    if seed["arrival_seed"] != base["core"]["seed"]:
        core["scenario_id"] = f"endogenous_cohort_seed{seed['arrival_seed']}"
    background = {**base["background"], "seed": seed["background_seed"]}
    parameters = {**full_issuer["parameters"], "innovation_seed": seed["innovation_seed"],
                  "information_seed": seed["information_seed"]}
    risk = {stock: [{key: row[key] for key in RISK_FIELDS} for row in rows] for stock, rows in full_issuer["by_stock"].items()}
    issuer = build_issuer_valuation(risk, full_issuer["scenario_id"], parameters)
    if parameters == full_issuer["parameters"] and issuer != full_issuer:
        raise ValueError("reference seed issuer design differs byte-for-value")
    return core, background, issuer


def controls_for(anchor):
    return None if anchor == "initial_inventory" else {"background_anchor": "current_inventory", "strategy_wait": "original"}


def record_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def run(output=OUTPUT, audit_existing=False):
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or (output.exists() and not audit_existing):
        raise ValueError("feedback study needs a fresh direct research_outputs directory")
    (cfg, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership,
     catalog, shared, full_issuer, feature_checks, industry_config, reference) = load_inputs()
    agents = [AgentParameters(**row) for row in replay["agents"]]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    response = industry_config["background_response"]
    baskets = [codes[index:index + 3] for index in range(0, len(codes), 3)]
    source_names = {"initial_inventory": "original_quantity_reference", "current_inventory": "background_current_anchor"}
    source_paths = {(row["variant"], row["basket_index"]): row for row in reference["path_summaries"]}
    variants, paths, same_state_groups, checks = [], [], {}, Counter()
    with tempfile.TemporaryDirectory(prefix="own-price-feedback-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        with (stage / "ledger.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as ledger, \
             (stage / "same_state_orders.jsonl.gz").open("wb") as same_raw, gzip.GzipFile(fileobj=same_raw, mode="wb", filename="", mtime=0) as same_ledger:
            for seed_index, seed in enumerate(cfg["seeds"]):
                core, background, seeded_issuer = seeded_design(base, full_issuer, seed)
                seed_base = {**base, "core": core, "background": background}
                paired_receipts = {}
                for cell in CELLS:
                    anchor, scale = cell["background_anchor"], cell["scale"]
                    controls = controls_for(anchor)
                    source_name = source_names[anchor]
                    source_days = reference_days(SOURCE / "ledger.jsonl.gz", source_name) if seed_index == 0 and scale == 1 else None
                    daily, coverage, branches, branch_flows = [], Counter(), Counter(), Counter()
                    for basket_index, basket in enumerate(baskets):
                        shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {stock: [0.0] * len(dates) for stock in basket}}
                        industry = subset_industry_shocks(shared["industry"], basket)
                        issuer = subset_issuer_valuation(seeded_issuer, basket)
                        simulated = simulate_portfolio({stock: joined[stock] for stock in basket}, agents, core, background,
                                                       base["venue"], base["feedback_parameters"], case, False,
                                                       scenario_shocks=shocks, background_response=response, industry_shocks=industry,
                                                       issuer_valuation=issuer, quantity_controls=controls, own_price_feedback_scale=scale)
                        original = source_paths[source_name, basket_index]
                        specs = simulated["participant_specs"]
                        verify_specs(specs, original["participant_specs"], scale)
                        if source_days is not None and (simulated["summary"] != original["summary"] or specs != original["participant_specs"]):
                            raise ValueError("new engine full-scale complete source object differs")
                        state = initial_from_specs(specs, basket, seed_base, case)
                        histories = {stock: [] for stock in basket}
                        streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
                        for session, day in enumerate(simulated["trace"]):
                            if (day["trade_date"] != dates[session] or day["signal_cutoff_date"] != joined[basket[0]][session]["signal_cutoff_date"]
                                    or day["execution_reference_date"] != joined[basket[0]][session]["execution_reference_date"]
                                    or day.get("quantity_control_parameters") != controls
                                    or day.get("own_price_feedback_scale") != (scale if scale != 1 else None)):
                                raise ValueError("feedback path clock/applied control differs")
                            verify_arrival(day, specs, core, session)
                            independent_covariance(day, histories, base["feedback_parameters"])
                            if source_days is not None:
                                saved = next(source_days, None)
                                if saved is None or saved["basket_index"] != basket_index or day != {key: value for key, value in saved.items() if key not in {"variant", "basket_index"}}:
                                    raise ValueError("new engine complete source trace differs")
                                checks["complete_original_daily_records_matched"] += 1
                            key = (basket_index, session)
                            receipt_hash = canonical_hash(day["issuer_information_receipts"])
                            if cell == CELLS[0]:
                                paired_receipts[key] = receipt_hash
                            elif paired_receipts[key] != receipt_hash:
                                raise ValueError("feedback or inventory intervention changed private message/receipt draws")
                            else:
                                checks["paired_private_receipt_values"] += sum(len(rows) for rows in day["issuer_information_receipts"].values())
                            if scale == 1:
                                for counter_scale in (1.0, 0.5, 0.0):
                                    payload = same_state_orders(day, state, specs, case, base["venue"], streaks, counter_scale)
                                    same_counts = verify_same_state(payload, day, state, specs, base["venue"], background, response,
                                                                    shocks, industry, issuer, controls, case, streaks, session, counter_scale)
                                    checks["same_state_strategy_allocations_audited"] += same_counts["strategy_allocation_checks"]
                                    if counter_scale == 1:
                                        checks["same_state_original_decision_order_matches"] += len(payload["decisions"])
                                        continue
                                    group_key = seed["seed_id"] + ":" + anchor + ":" + str(counter_scale)
                                    group = same_state_groups.setdefault(group_key, {"seed_id": seed["seed_id"], "background_anchor": anchor,
                                                                                   "scale": counter_scale, "records": 0, "requested_buy": 0, "requested_sell": 0})
                                    totals = order_totals(payload["orders"])
                                    group["records"] += 1
                                    group["requested_buy"] += totals["buy"]
                                    group["requested_sell"] += totals["sell"]
                                    same_ledger.write(record_bytes({"seed_id": seed["seed_id"], "background_anchor": anchor,
                                                                   "basket_index": basket_index, "session": session,
                                                                   "scale": counter_scale, "prior_state_sha256": canonical_hash(state),
                                                                   "prior_streaks_sha256": canonical_hash(streaks), **payload}))
                                    checks["same_state_scaled_records"] += 1
                            coverage.update(verify_quantity_day(day, state, specs, base["venue"], background, response,
                                                                shocks, industry, issuer, controls, case, streaks, session))
                            for name, decision in day["decisions"].items():
                                role = specs[name]["parameters"]["role"]
                                branches[role + ":" + decision_branch(decision)] += 1
                            for stock, call in day["portfolio_auction"]["asset_calls"].items():
                                for order in call["orders"]:
                                    if specs[order["owner"]]["kind"] == "strategy":
                                        label = specs[order["owner"]]["parameters"]["role"] + ":" + decision_branch(day["decisions"][order["owner"]]) + ":" + order["side"]
                                        for stage_name, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                                            branch_flows[label + ":" + stage_name] += order[field]
                                daily.append({"stock_code": stock, "trade_date": day["trade_date"], "signal_cutoff_date": day["signal_cutoff_date"],
                                              "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                                              "matched_volume": call["matched_volume"], "observed_return": joined[stock][session]["observed_return"]})
                                histories[stock].append({"trade_date": day["trade_date"], "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"]})
                            state = audit_portfolio_day(day, state, base["venue"], session, dense=session in cfg["dense_audit_sessions"])
                            coverage["settlement_asset_calls"] += len(basket)
                            coverage["dense_asset_price_checks"] += len(basket) if session in cfg["dense_audit_sessions"] else 0
                            ledger.write(record_bytes({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": basket_index, **day}))
                            checks["full_path_ledger_records"] += 1
                        verify_final_resources(state, simulated["summary"])
                        paths.append({"seed_id": seed["seed_id"], "variant": cell["name"], "basket_index": basket_index,
                                      "participant_specs": specs, "summary": simulated["summary"]})
                        if (basket_index + 1) % 10 == 0 or basket_index + 1 == len(baskets):
                            print(f"{seed['seed_id']} {cell['name']}: audited {basket_index + 1}/{len(baskets)} complete baskets", flush=True)
                    if source_days is not None and next(source_days, None) is not None:
                        raise ValueError("original feedback path source has extra records")
                    variants.append({"seed_id": seed["seed_id"], **cell, **metrics(daily, cfg["joint_limits"]),
                                     "coverage": dict(coverage), "total_matched_volume": sum(row["matched_volume"] for row in daily),
                                     "strategy_decision_branches": dict(branches), "strategy_branch_order_quantities": dict(branch_flows),
                                     "daily_asset_rows": daily})
        if (checks["full_path_ledger_records"] != 52890 or len(paths) != 1230
                or checks["complete_original_daily_records_matched"] != 3526
                or checks["same_state_scaled_records"] != 35260
                or checks["same_state_strategy_allocations_audited"] != 634680
                or checks["paired_private_receipt_values"] != 634680):
            raise ValueError("full feedback grid or paired audit coverage incomplete")
        result = {"pipeline_version": VERSION, "agent_signal_enabled": False, "sample": reference["sample"],
                  "market_dataset_id": market["market_dataset_id"], "seeds": cfg["seeds"], "limits": cfg["joint_limits"],
                  "shared_design": shared, "seed_issuer_parameters": [seeded_design(base, full_issuer, seed)[2]["parameters"] for seed in cfg["seeds"]],
                  "variants": variants, "path_summaries": paths, "seed_summary": seed_summary(variants),
                  "same_state_groups": same_state_groups, "checks": {**dict(checks), "source_feature_checks": feature_checks},
                  "interpretation": cfg["interpretation"]}
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
                    raise ValueError("feedback artifact differs byte-for-byte: " + name)
            print("Own-price feedback artifacts rebuilt byte-identically.", flush=True)
        else:
            stage.replace(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = run(args.output_dir, args.audit_existing)
    print(json.dumps({"checks": result["checks"], "seed_summary": result["seed_summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
