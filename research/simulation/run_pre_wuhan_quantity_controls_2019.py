"""Run coherent full paths for background inventory anchors and strategy waits."""

from __future__ import annotations

import argparse
import gzip
import json
import tempfile
from collections import Counter
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .audit_feedback import check_feedback
from .audit_quantity_controls import verify_quantity_day
from .audit_pre_wuhan_common_factor_2019 import common_factor_metrics
from .audit_pre_wuhan_industry_comovement_2019 import diagnose
from .audit_synthetic_observed_returns import compare_pairs
from .industry_shocks import subset_industry_shocks
from .issuer_valuation import subset_issuer_valuation
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import simulate_portfolio
from .quantity_controls import BASELINE
from .run_pre_wuhan_background_response_2019 import initial_state, verify_hashes
from .run_pre_wuhan_issuer_valuation_2019 import load_inputs_and_design as issuer_inputs
from .run_pre_wuhan_issuer_valuation_2019 import daily_metric, reference_days

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_quantity_controls_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_quantity_controls_2019_v1"
VERSION = "pre-wuhan-inventory-anchor-strategy-waits-full-path-v1"
CELLS = [{"name": "original_quantity_reference", **BASELINE},
         {"name": "background_current_anchor", "background_anchor": "current_inventory", "strategy_wait": "original"},
         {"name": "strategy_immediate_daily", "background_anchor": "initial_inventory", "strategy_wait": "immediate_daily"},
         {"name": "current_anchor_and_daily", "background_anchor": "current_inventory", "strategy_wait": "immediate_daily"}]


def load_inputs() -> tuple:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (config["version"] != VERSION or config["variants"] != CELLS or config["issuer_variant"] != "issuer_one_sigma_half"
            or config["dense_audit_sessions"] != [0, 10]):
        raise ValueError("frozen full-path quantity controls differ")
    (issuer_config, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership,
     catalog, shared, risk, issuer_cells, feature_checks, _, _, industry_config) = issuer_inputs()
    reference_dir = (CONFIG.parent / config["reference_directory"]).resolve()
    snapshot = (CONFIG.parent / config["reference_portfolio_source"]).resolve()
    bindings = {str(reference_dir / "results.json"): config["reference_results_sha256"],
                str(reference_dir / "manifest.json"): config["reference_manifest_sha256"],
                str(snapshot): config["reference_portfolio_source_sha256"], str(CONFIG): file_sha256(CONFIG)}
    verify_hashes(bindings)
    inputs.update(bindings)
    manifest = json.loads((reference_dir / "manifest.json").read_text(encoding="utf-8"))
    verify_hashes(manifest["inputs"])
    inputs.update(manifest["inputs"])
    portfolio_source = str((ROOT / "research/simulation/portfolio_market.py").resolve())
    if manifest["code_sha256"][portfolio_source] != config["reference_portfolio_source_sha256"]:
        raise ValueError("quantity reference source snapshot differs from original issuer archive")
    verify_hashes({name: digest for name, digest in manifest["code_sha256"].items() if name != portfolio_source})
    for name, info in manifest["artifacts"].items():
        path = reference_dir / name
        if path.parent != reference_dir or file_sha256(path) != info["sha256"]:
            raise ValueError("quantity reference artifact hash differs")
        inputs[str(path)] = info["sha256"]
    reference = json.loads((reference_dir / "results.json").read_text(encoding="utf-8"))["result"]
    if reference["sample"]["selected_stock_codes"] != codes:
        raise ValueError("quantity reference cohort differs")
    issuer = next(cell["issuer"] for cell in issuer_cells if cell["name"] == config["issuer_variant"])
    if issuer != next(cell["issuer"] for cell in reference["design"] if cell["name"] == config["issuer_variant"]):
        raise ValueError("quantity source messages, risk or receipts changed")
    for name in ("quantity_controls.py", "audit_quantity_controls.py", "run_pre_wuhan_quantity_controls_2019.py"):
        path = ROOT / "research/simulation" / name
        code_hashes[str(path)] = file_sha256(path)
    upgrades = {"issuer_reference": {portfolio_source: {"archived": config["reference_portfolio_source_sha256"],
                                                       "current": file_sha256(Path(portfolio_source))}}, "earlier_references": upgrades}
    return config, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership, catalog, shared, issuer, feature_checks, reference_dir, reference, industry_config


def decision_branch(decision: dict) -> str:
    return "risk_liquidation" if decision["risk_liquidation"] else "confirmation_or_rebalance_wait" if "confirmation_or_rebalance_wait" in decision["reasons"] else "ordinary_allocation"


def run(output: Path = OUTPUT, audit_existing: bool = False) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or (not audit_existing and output.exists()):
        raise ValueError("quantity controls require a fresh direct research_outputs directory")
    (config, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership,
     catalog, shared, full_issuer, feature_checks, reference_dir, reference, industry_config) = load_inputs()
    response = industry_config["background_response"]
    agents = [AgentParameters(**row) for row in replay["agents"]]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    baskets = [codes[index:index + 3] for index in range(0, len(codes), 3)]
    basket_ids = {stock: index // 3 for index, stock in enumerate(codes)}
    saved_paths = {row["basket_index"]: row for row in reference["path_summaries"] if row["variant"] == config["issuer_variant"]}
    saved_variant = next(row for row in reference["variants"] if row["name"] == config["issuer_variant"])
    saved_daily = {(row["stock_code"], row["trade_date"]): row for row in saved_variant["daily_asset_rows"]}
    frozen_days = reference_days(reference_dir / "ledger.jsonl.gz", config["issuer_variant"])
    variants, paths, records, reference_count = [], [], 0, 0
    expected_receipt_flags = {}
    coupled_receipt_checks = 0
    with tempfile.TemporaryDirectory(prefix="quantity-controls-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        with (stage / "ledger.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as ledger:
            for cell in CELLS:
                control_values = {key: cell[key] for key in BASELINE}
                controls = None if control_values == BASELINE else control_values
                daily, coverage, branches, branch_orders = [], Counter(), Counter(), Counter()
                for basket_index, basket in enumerate(baskets):
                    shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {stock: [0.0] * len(dates) for stock in basket}}
                    industry = subset_industry_shocks(shared["industry"], basket)
                    issuer = subset_issuer_valuation(full_issuer, basket)
                    simulation = simulate_portfolio({stock: joined[stock] for stock in basket}, agents, base["core"], base["background"],
                                                    base["venue"], base["feedback_parameters"], case, False,
                                                    scenario_shocks=shocks, background_response=response, industry_shocks=industry,
                                                    issuer_valuation=issuer, quantity_controls=controls)
                    if controls is None and (simulation["summary"] != saved_paths[basket_index]["summary"]
                                             or simulation["participant_specs"] != saved_paths[basket_index]["participant_specs"]):
                        raise ValueError("quantity-disabled complete summary or specs differ from issuer archive")
                    original_specs = saved_paths[basket_index]["participant_specs"]
                    for name, spec in simulation["participant_specs"].items():
                        if spec["kind"] != "strategy":
                            continue
                        expected = original_specs[name]["parameters"]
                        if cell["strategy_wait"] == "immediate_daily":
                            expected = {**expected, "confirmation_steps": 1, "rebalance_interval": 1}
                        if spec["parameters"] != expected:
                            raise ValueError("quantity control altered a risk, turnover, sensitivity or resource parameter")
                    state = initial_state(agents, base, basket, case)
                    histories = {stock: [] for stock in basket}
                    strategy_states = {name: {"sign": 0, "streak": 0} for name, spec in simulation["participant_specs"].items() if spec["kind"] == "strategy"}
                    for session, day in enumerate(simulation["trace"]):
                        if (day["trade_date"] != dates[session] or any(day[key] != joined[basket[0]][session][key] for key in ("signal_cutoff_date", "execution_reference_date"))
                                or day.get("quantity_control_parameters") != controls):
                            raise ValueError("quantity control clock or applied modes differ")
                        if controls is None:
                            previous = next(frozen_days, None)
                            if previous is None or previous["basket_index"] != basket_index or day != {key: value for key, value in previous.items() if key not in {"variant", "basket_index"}}:
                                raise ValueError("quantity-disabled complete daily trace differs from issuer archive")
                            reference_count += 1
                        coverage.update(verify_quantity_day(day, state, simulation["participant_specs"], base["venue"], base["background"], response,
                                                            shocks, industry, issuer, controls, case, strategy_states, session))
                        mask = {name: {stock: (receipt["received"], receipt["receipt_sha256"]) for stock, receipt in stocks.items()}
                                for name, stocks in day["issuer_information_receipts"].items()}
                        key = (basket_index, session)
                        if controls is None:
                            expected_receipt_flags[key] = mask
                        elif mask != expected_receipt_flags[key]:
                            raise ValueError("quantity modes changed private information receipt draws")
                        else:
                            coupled_receipt_checks += sum(len(stocks) for stocks in mask.values())
                        for name, decision in day["decisions"].items():
                            role = simulation["participant_specs"][name]["parameters"]["role"]
                            branches[f"{role}:{decision_branch(decision)}"] += 1
                        for stock in basket:
                            check_feedback({"feedback": day["observations"][stock], "signal_cutoff_date": day["signal_cutoff_date"]}, histories[stock], base["feedback_parameters"])
                            for order in day["portfolio_auction"]["asset_calls"][stock]["orders"]:
                                spec = simulation["participant_specs"][order["owner"]]
                                if spec["kind"] == "strategy":
                                    label = f"{spec['parameters']['role']}:{decision_branch(day['decisions'][order['owner']])}:{order['side']}"
                                    branch_orders[label + ":requested"] += order["quantity"]
                                    branch_orders[label + ":accepted"] += order["accepted_quantity"]
                                    branch_orders[label + ":filled"] += order["filled_quantity"]
                        dense = session in config["dense_audit_sessions"]
                        state = audit_portfolio_day(day, state, base["venue"], session, dense=dense)
                        coverage["asset_ledger_checks"] += len(basket)
                        coverage["dense_asset_price_checks"] += len(basket) if dense else 0
                        ledger.write((json.dumps({"variant": cell["name"], "basket_index": basket_index, **day}, ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                        records += 1
                        for stock in basket:
                            row = daily_metric(day, stock, session, joined, quotes, simulation["participant_specs"], membership)
                            histories[stock].append(row)
                            message = issuer["by_stock"][stock][session]
                            row.update(lagged_stock_volatility=message["lagged_stock_volatility"], hypothetical_issuer_valuation_bps=message["valuation_shift_bps"],
                                       issuer_message_capped=message["was_capped"],
                                       issuer_information_received_by_kind={kind: sum(stocks.get(stock, {}).get("received", False) for name, stocks in day["issuer_information_receipts"].items()
                                                                                      if simulation["participant_specs"][name]["kind"] == kind) for kind in ("strategy", "background")})
                            if controls is None and row != saved_daily[stock, day["trade_date"]]:
                                raise ValueError("quantity-disabled complete daily metrics differ from issuer archive")
                            demands = list(day["background_demands"][stock].values())
                            row["background_inventory_diagnostic"] = {"current_shares": sum(item["current_shares"] for item in demands),
                                                                      "target_shares": sum(item["target_shares"] for item in demands),
                                                                      "reference_initial_shares": base["background"]["initial_shares"] * len(demands),
                                                                      "requested_buy": sum(item["requested_quantity"] for item in demands if item["side"] == "buy"),
                                                                      "requested_sell": sum(item["requested_quantity"] for item in demands if item["side"] == "sell")}
                            daily.append(row)
                            call = day["portfolio_auction"]["asset_calls"][stock]
                            coverage["execution_unavailable"] += int(not call["execution_available"])
                            coverage["zero_matched_volume"] += int(call["matched_volume"] == 0)
                    if state["prices"] != simulation["summary"]["final_prices_minor"]:
                        raise ValueError("quantity final prices differ from independent ledger reconstruction")
                    paths.append({"variant": cell["name"], "basket_index": basket_index, "participant_specs": simulation["participant_specs"], "summary": simulation["summary"]})
                    if (basket_index + 1) % 10 == 0 or basket_index + 1 == len(baskets):
                        print(f"{cell['name']}: audited {basket_index + 1}/{len(baskets)} full baskets", flush=True)
                industry_metrics, _, _, _ = diagnose(daily, membership, basket_ids)
                final_background_shares = sum(sum(account["shares"].values()) for path in paths if path["variant"] == cell["name"]
                                              for account in path["summary"]["accounts"].values() if account["kind"] == "background")
                variants.append({"name": cell["name"], "quantity_control_parameters": control_values, "coverage": dict(coverage),
                                 "comparison": compare_pairs([(row["price_after_minor"] / row["price_before_minor"] - 1, row["observed_return"]) for row in daily]),
                                 "common_factor_metrics": common_factor_metrics(daily), "industry_metrics": industry_metrics,
                                 "total_matched_volume": sum(row["matched_volume"] for row in daily), "daily_asset_rows": daily,
                                 "strategy_decision_branches": dict(branches), "strategy_branch_order_quantities": dict(branch_orders),
                                 "initial_background_shares": len(codes) * base["background"]["participants"] * base["background"]["initial_shares"],
                                 "final_background_shares": final_background_shares})
        if next(frozen_days, None) is not None or reference_count != 1763 or coupled_receipt_checks != 380808:
            raise ValueError("quantity baseline or paired receipt coverage differs")
        result = {"pipeline_version": VERSION, "market_dataset_id": market["market_dataset_id"], "sample": reference["sample"],
                  "industry_source": catalog["source"], "shared_design": shared, "issuer_design": full_issuer,
                  "background_response": response, "variants": variants, "path_summaries": paths,
                  "checks": {"source_feature_checks": feature_checks, "portfolio_ledger_records": records,
                             "quantity_disabled_full_reference_objects": len(baskets), "quantity_disabled_full_daily_reference_records": reference_count,
                             "paired_actual_recipient_mask_checks": coupled_receipt_checks,
                             "clock": "t-2 information, t-1 execution reference, t-labelled synthetic step; not same exchange-day replication"},
                  "interpretation": config["interpretation"]}
        (stage / "results.json").write_text(json.dumps({"result": result}, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        verify_hashes(inputs)
        verify_hashes(code_hashes)
        manifest = {"pipeline_version": VERSION, "inputs": inputs, "code_sha256": code_hashes, "reviewed_reference_code_upgrades": upgrades,
                    "artifacts": {name: {"sha256": file_sha256(stage / name)} for name in ("results.json", "ledger.jsonl.gz")}}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if audit_existing:
            for name in ("results.json", "ledger.jsonl.gz", "manifest.json"):
                if file_sha256(stage / name) != file_sha256(output / name):
                    raise ValueError(f"quantity artifact differs byte-for-byte: {name}")
        else:
            stage.replace(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = run(args.output_dir, args.audit_existing)
    print(json.dumps({"checks": result["checks"], "variants": [{"name": row["name"], **row["comparison"]["synthetic"],
                       "mean_stock_correlation": row["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"],
                       "total_matched_volume": row["total_matched_volume"], "final_background_shares": row["final_background_shares"]} for row in result["variants"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
