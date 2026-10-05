"""Run frozen lagged-risk issuer valuation and information coverage experiments."""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import tempfile
from collections import Counter
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .audit_background_response import verify_strategy_quotes
from .audit_feedback import check_feedback
from .audit_industry_response import verify_industry_delivery, verify_shared_background
from .audit_issuer_valuation import verify_risk_panel, verify_message_design, verify_issuer_day
from .audit_pre_wuhan_common_factor_2019 import common_factor_metrics
from .audit_pre_wuhan_industry_comovement_2019 import diagnose
from .audit_synthetic_observed_returns import compare_pairs
from .calibrate_pre_wuhan_market import accepted_order_flow
from .industry_shocks import subset_industry_shocks
from .issuer_valuation import build_lagged_risk, build_issuer_valuation, subset_issuer_valuation
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import simulate_portfolio
from .run_pre_wuhan_background_response_2019 import MARKET, initial_state, quote_summary, verify_hashes
from .run_pre_wuhan_industry_response_2019 import inputs_and_design as industry_inputs

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_issuer_valuation_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_issuer_valuation_2019_v1"
VERSION = "pre-wuhan-lagged-risk-private-issuer-valuation-v1"
CELLS = [{"name": "industry_reference", "risk_multiplier": None, "information_probability_bps": None},
         {"name": "issuer_quarter_sigma_full", "risk_multiplier": 0.25, "information_probability_bps": 10000},
         {"name": "issuer_quarter_sigma_half", "risk_multiplier": 0.25, "information_probability_bps": 5000},
         {"name": "issuer_one_sigma_full", "risk_multiplier": 1.0, "information_probability_bps": 10000},
         {"name": "issuer_one_sigma_half", "risk_multiplier": 1.0, "information_probability_bps": 5000}]


def load_inputs_and_design() -> tuple:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (config["version"] != VERSION or config["variants"] != CELLS or config["shared_variant"] != "common_plus_industry_grouped"
            or config["parameters"] != {"history_window": 20, "belief_scale_bps": 1000, "max_shift_bps": 500,
                                        "innovation_seed": "marketmirror-issuer-risk-innovation-v1",
                                        "information_seed": "marketmirror-issuer-information-coverage-v1"}):
        raise ValueError("frozen issuer valuation grid differs")
    (industry_config, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades,
     membership, catalog, industry_design, dose_checks, _, _) = industry_inputs()
    reference_dir = (CONFIG.parent / config["reference_directory"]).resolve()
    portfolio_source = (CONFIG.parent / config["reference_portfolio_source"]).resolve()
    bindings = {str(reference_dir / "results.json"): config["reference_results_sha256"],
                str(reference_dir / "manifest.json"): config["reference_manifest_sha256"],
                str(portfolio_source): config["reference_portfolio_source_sha256"], str(CONFIG): file_sha256(CONFIG)}
    verify_hashes(bindings)
    inputs.update(bindings)
    manifest = json.loads((reference_dir / "manifest.json").read_text(encoding="utf-8"))
    verify_hashes(manifest["inputs"])
    inputs.update(manifest["inputs"])
    current_portfolio = str((ROOT / "research/simulation/portfolio_market.py").resolve())
    if manifest["code_sha256"][current_portfolio] != config["reference_portfolio_source_sha256"]:
        raise ValueError("pre-issuer source snapshot differs from frozen industry source")
    verify_hashes({name: digest for name, digest in manifest["code_sha256"].items() if name != current_portfolio})
    for name, info in manifest["artifacts"].items():
        path = reference_dir / name
        if path.parent != reference_dir or file_sha256(path) != info["sha256"]:
            raise ValueError("issuer reference artifact differs")
        inputs[str(path)] = info["sha256"]
    reference = json.loads((reference_dir / "results.json").read_text(encoding="utf-8"))["result"]
    if reference["sample"]["selected_stock_codes"] != codes:
        raise ValueError("issuer cohort differs from frozen reference")
    shared = next(cell for cell in industry_design if cell["name"] == config["shared_variant"])
    groups = _load_market(MARKET.parent / "market_daily.csv", market)
    risk = build_lagged_risk(groups, joined, config["parameters"]["history_window"])
    checks = verify_risk_panel(risk, groups, joined, config["parameters"]["history_window"])
    cells = [{"name": CELLS[0]["name"], "issuer": None}]
    for declaration in CELLS[1:]:
        parameters = {**config["parameters"], **{key: declaration[key] for key in ("risk_multiplier", "information_probability_bps")}}
        cells.append({"name": declaration["name"], "issuer": build_issuer_valuation(risk, declaration["name"], parameters)})
    checks.update(verify_message_design(cells, risk, config))
    checks["shared_industry_dose_audit"] = dose_checks
    for name in ("issuer_valuation.py", "audit_issuer_valuation.py", "run_pre_wuhan_issuer_valuation_2019.py"):
        path = ROOT / "research/simulation" / name
        code_hashes[str(path)] = file_sha256(path)
    upgrades = {"industry_reference": {current_portfolio: {"archived": config["reference_portfolio_source_sha256"],
                                                          "current": file_sha256(Path(current_portfolio))}},
                "earlier_references": upgrades}
    return config, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership, catalog, shared, risk, cells, checks, reference_dir, reference, industry_config


def reference_days(path: Path, name: str):
    started = False
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["variant"] == name:
                started = True
                yield row
            elif started:
                break


def daily_metric(day: dict, stock: str, session: int, joined: dict, quotes: dict, specs: dict, membership: dict) -> dict:
    call, observation = day["portfolio_auction"]["asset_calls"][stock], day["observations"][stock]
    return {"stock_code": stock, "trade_date": day["trade_date"], "exchange": quotes[stock]["symbol"].split(".")[0],
            "applied_price_tie_break": "nearest_prior", "signal_cutoff_date": day["signal_cutoff_date"],
            "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
            "matched_volume": call["matched_volume"], "observed_return": joined[stock][session]["observed_return"],
            **accepted_order_flow(call, specs),
            **{key: observation[key] for key in ("scenario_id", "common_shock", "issuer_specific_shock", "scenario_shock")},
            "quote_summaries": {kind: quote_summary([order for order in call["orders"] if specs[order["owner"]]["kind"] == kind],
                                                      call["price_before_minor"]) for kind in ("strategy", "background")},
            "industry_code": membership[stock], "industry_shock": observation["industry_shock"],
            "industry_assignment_mode": observation["industry_assignment_mode"], "industry_path_group": observation["industry_path_group"]}


def run(output: Path = OUTPUT, audit_existing: bool = False) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or (not audit_existing and output.exists()):
        raise ValueError("issuer experiments require a fresh direct research_outputs directory")
    (config, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership,
     catalog, shared, risk, cells, checks, reference_dir, reference, industry_config) = load_inputs_and_design()
    response = industry_config["background_response"]
    agents = [AgentParameters(**row) for row in replay["agents"]]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    baskets = [codes[index:index + 3] for index in range(0, len(codes), 3)]
    basket_ids = {stock: index // 3 for index, stock in enumerate(codes)}
    saved_paths = {row["basket_index"]: row for row in reference["path_summaries"] if row["variant"] == config["shared_variant"]}
    saved_variant = next(row for row in reference["variants"] if row["name"] == config["shared_variant"])
    saved_daily = {(row["stock_code"], row["trade_date"]): row for row in saved_variant["daily_asset_rows"]}
    frozen_days = reference_days(reference_dir / "ledger.jsonl.gz", config["shared_variant"])
    variants, paths, records, reference_count, mask_checks = [], [], 0, 0, 0
    half_masks = {}
    with tempfile.TemporaryDirectory(prefix="issuer-valuation-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        with (stage / "ledger.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as ledger:
            for cell in cells:
                daily, coverage = [], Counter()
                for basket_index, basket in enumerate(baskets):
                    baseline = cell["issuer"] is None
                    shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {stock: [0.0] * len(dates) for stock in basket}}
                    industry = subset_industry_shocks(shared["industry"], basket)
                    issuer = subset_issuer_valuation(cell["issuer"], basket) if not baseline else None
                    simulation = simulate_portfolio({stock: joined[stock] for stock in basket}, agents, base["core"], base["background"],
                                                    base["venue"], base["feedback_parameters"], case, False,
                                                    scenario_shocks=shocks, background_response=response, industry_shocks=industry, issuer_valuation=issuer)
                    if baseline and (simulation["summary"] != saved_paths[basket_index]["summary"] or simulation["participant_specs"] != saved_paths[basket_index]["participant_specs"]):
                        raise ValueError("issuer-disabled full industry summary or specs differ")
                    state = initial_state(agents, base, basket, case)
                    histories = {stock: [] for stock in basket}
                    for session, day in enumerate(simulation["trace"]):
                        if (day["trade_date"] != dates[session]
                                or any(day[key] != joined[basket[0]][session][key] for key in ("signal_cutoff_date", "execution_reference_date"))):
                            raise ValueError("issuer decision clock differs from frozen source")
                        if baseline:
                            previous = next(frozen_days, None)
                            if (previous is None or previous["basket_index"] != basket_index
                                    or day != {key: value for key, value in previous.items() if key not in {"variant", "basket_index"}}):
                                raise ValueError("issuer-disabled full daily trace differs from industry reference")
                            reference_count += 1
                            verify_industry_delivery(day, simulation["participant_specs"], shocks, industry, session)
                            verify_strategy_quotes(day, state, base["venue"], simulation["participant_specs"])
                            for stock in basket:
                                group = industry["path_groups"][stock]
                                verify_shared_background(day, state, stock, session, base["venue"], base["background"], shocks["common"][session],
                                                         industry["by_group"][group][session], membership[stock], group, response, simulation["participant_specs"])
                                coverage["background_demand_checks"] += base["background"]["participants"]
                        else:
                            coverage.update(verify_issuer_day(day, state, simulation["participant_specs"], base["venue"], base["background"], response,
                                                              shocks, industry, issuer, session))
                            if issuer["parameters"]["information_probability_bps"] == 5000:
                                mask = {name: {stock: (receipt["received"], receipt["receipt_sha256"]) for stock, receipt in receipts.items()}
                                        for name, receipts in day["issuer_information_receipts"].items()}
                                key = (basket_index, session)
                                if cell["name"] == "issuer_quarter_sigma_half":
                                    half_masks[key] = mask
                                elif mask != half_masks[key]:
                                    raise ValueError("actual recipient masks changed with message strength")
                                else:
                                    mask_checks += sum(len(values) for values in mask.values())
                        for stock in basket:
                            check_feedback({"feedback": day["observations"][stock], "signal_cutoff_date": day["signal_cutoff_date"]}, histories[stock], base["feedback_parameters"])
                        dense = session in {0, industry_config["pulse_sessions"][0]}
                        state = audit_portfolio_day(day, state, base["venue"], session, dense=dense)
                        coverage["asset_ledger_checks"] += len(basket)
                        coverage["dense_asset_price_checks"] += len(basket) if dense else 0
                        ledger.write((json.dumps({"variant": cell["name"], "basket_index": basket_index, **day}, ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                        records += 1
                        for stock in basket:
                            row = daily_metric(day, stock, session, joined, quotes, simulation["participant_specs"], membership)
                            if baseline and row != saved_daily[stock, day["trade_date"]]:
                                raise ValueError("issuer-disabled full daily metrics differ from industry reference")
                            histories[stock].append(row)
                            if not baseline:
                                message = issuer["by_stock"][stock][session]
                                row.update(lagged_stock_volatility=message["lagged_stock_volatility"],
                                           hypothetical_issuer_valuation_bps=message["valuation_shift_bps"], issuer_message_capped=message["was_capped"],
                                           issuer_information_received_by_kind={kind: sum(receipts.get(stock, {}).get("received", False)
                                                                                          for name, receipts in day["issuer_information_receipts"].items()
                                                                                          if simulation["participant_specs"][name]["kind"] == kind) for kind in ("strategy", "background")})
                            daily.append(row)
                            call = day["portfolio_auction"]["asset_calls"][stock]
                            coverage["execution_unavailable"] += int(not call["execution_available"])
                            coverage["zero_matched_volume"] += int(call["matched_volume"] == 0)
                    if state["prices"] != simulation["summary"]["final_prices_minor"]:
                        raise ValueError("independently reconstructed issuer final prices differ")
                    paths.append({"variant": cell["name"], "basket_index": basket_index, "participant_specs": simulation["participant_specs"], "summary": simulation["summary"]})
                    if (basket_index + 1) % 10 == 0 or basket_index + 1 == len(baskets):
                        print(f"{cell['name']}: audited {basket_index + 1}/{len(baskets)} baskets", flush=True)
                industry_metrics, _, _, _ = diagnose(daily, membership, basket_ids)
                variants.append({"name": cell["name"], "coverage": dict(coverage),
                                 "comparison": compare_pairs([(row["price_after_minor"] / row["price_before_minor"] - 1, row["observed_return"]) for row in daily]),
                                 "common_factor_metrics": common_factor_metrics(daily), "industry_metrics": industry_metrics,
                                 "total_matched_volume": sum(row["matched_volume"] for row in daily), "daily_asset_rows": daily,
                                 "hypothetical_message_metrics": None if cell["issuer"] is None else {
                                     "pooled_shift_std_bps": statistics.pstdev(row["hypothetical_issuer_valuation_bps"] for row in daily),
                                     "capped_company_days": sum(row["issuer_message_capped"] for row in daily)}})
        if next(frozen_days, None) is not None or reference_count != 1763 or mask_checks != 126936:
            raise ValueError("reference or paired actual recipient coverage differs")
        result = {"pipeline_version": VERSION, "market_dataset_id": market["market_dataset_id"], "sample": reference["sample"],
                  "industry_source": catalog["source"], "shared_design": shared, "lagged_risk_panel": risk, "design": cells,
                  "background_response": response, "variants": variants, "path_summaries": paths,
                  "checks": {**checks, "portfolio_ledger_records": records, "issuer_disabled_full_reference_objects": len(baskets),
                             "issuer_disabled_full_daily_reference_records": reference_count, "paired_actual_recipient_mask_checks": mask_checks,
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
                    raise ValueError(f"issuer experiment artifact differs byte-for-byte: {name}")
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
                       "industry_gap": row["industry_metrics"]["synthetic"]["raw"]["within_minus_between"],
                       "total_matched_volume": row["total_matched_volume"]} for row in result["variants"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
