"""Run a dose-matched industry/shared-background stress grid with complete ledgers."""

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
from .audit_background_response import verify_background_response, verify_strategy_quotes
from .audit_feedback import check_feedback
from .audit_industry_response import audit_industry_grid, verify_industry_delivery, verify_shared_background
from .audit_pre_wuhan_common_factor_2019 import common_factor_metrics
from .audit_pre_wuhan_industry_comovement_2019 import diagnose
from .audit_synthetic_observed_returns import compare_pairs
from .calibrate_pre_wuhan_market import accepted_order_flow
from .industry_shocks import build_industry_grid, subset_industry_shocks
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import simulate_portfolio
from .scenario_shocks import build_scenario_shocks
from .run_pre_wuhan_background_response_2019 import CONFIG as BACKGROUND_CONFIG
from .run_pre_wuhan_background_response_2019 import initial_state, load_inputs, quote_summary, verify_hashes

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_industry_response_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_industry_response_2019_v1"
VERSION = "pre-wuhan-industry-shared-response-dose-grid-v1"
NAMES = ["uniform_common", "industry_only_grouped", "industry_only_permuted", "common_plus_industry_grouped", "common_plus_industry_permuted"]


def inputs_and_design() -> tuple:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (config["version"] != VERSION or config["variants"] != NAMES
            or config["pulse_sessions"] != [10, 11, 26, 27] or config["pulse_polarities"] != [1, 1, -1, -1]
            or config["total_cross_stock_rms"] != 0.20
            or config["background_response"] != {"valuation_response_bps": 200, "pulse_participation_bps": 10000}):
        raise ValueError("declared industry experiment grid differs")
    _, base, replay, market, codes, dates, quotes, joined, _, inputs, code_hashes, upgrades = load_inputs()
    industry_dir = (CONFIG.parent / config["industry_directory"]).resolve()
    reference_dir = (CONFIG.parent / config["reference_directory"]).resolve()
    bindings = {industry_dir / "industry_memberships.json": config["industry_catalog_sha256"],
                industry_dir / "provenance.json": config["industry_provenance_sha256"],
                reference_dir / "results.json": config["reference_results_sha256"],
                reference_dir / "manifest.json": config["reference_manifest_sha256"],
                (CONFIG.parent / config["reference_portfolio_source"]).resolve(): config["reference_portfolio_source_sha256"]}
    verify_hashes({str(path): digest for path, digest in bindings.items()})
    inputs.update({str(path): digest for path, digest in bindings.items()})
    inputs[str(CONFIG)] = file_sha256(CONFIG)
    industry_provenance = json.loads((industry_dir / "provenance.json").read_text(encoding="utf-8"))
    for field in ("inputs", "code_sha256"):
        verify_hashes(industry_provenance[field])
        inputs.update(industry_provenance[field])
    catalog = json.loads((industry_dir / "industry_memberships.json").read_text(encoding="utf-8"))
    memberships = catalog["cohort_memberships"]
    if (len(memberships) != 123 or any(row["status"] != "verified_table_label" or row["agent_signal_enabled"] is not False
                                     or row["impact_direction"] != "unknown" or row["impact_magnitude"] is not None for row in memberships)):
        raise ValueError("industry labels require verified membership with impact coefficients unassigned")
    membership = {row["stock_code"]: row["industry_code"] for row in memberships}
    if sorted(membership) != codes or any(row["available_at_proxy"] > joined[row["stock_code"]][0]["signal_cutoff_date"] + "T00:00:00+08:00" for row in memberships):
        raise ValueError("industry cohort or first information cutoff differs")
    reference_manifest = json.loads((reference_dir / "manifest.json").read_text(encoding="utf-8"))
    for name, info in reference_manifest["artifacts"].items():
        path = reference_dir / name
        if path.parent != reference_dir or file_sha256(path) != info["sha256"]:
            raise ValueError("background reference ledger artifact hash differs")
        inputs[str(path)] = info["sha256"]
    verify_hashes(reference_manifest["inputs"])
    inputs.update(reference_manifest["inputs"])
    portfolio_path = str((ROOT / "research/simulation/portfolio_market.py").resolve())
    if reference_manifest["code_sha256"][portfolio_path] != config["reference_portfolio_source_sha256"]:
        raise ValueError("saved reference source differs from the background archive's source")
    verify_hashes({name: digest for name, digest in reference_manifest["code_sha256"].items() if name != portfolio_path})
    reference = json.loads((reference_dir / "results.json").read_text(encoding="utf-8"))["result"]
    if reference["sample"]["selected_stock_codes"] != codes:
        raise ValueError("industry experiment reference cohort differs")
    design = build_industry_grid(membership, len(dates), config["pulse_sessions"], config["pulse_polarities"],
                                 config["total_cross_stock_rms"], config["industry_seed"], config["permutation_seed"])
    dose_checks = audit_industry_grid(design, membership, len(dates), config)
    for name in ("industry_shocks.py", "audit_industry_response.py", "audit_pre_wuhan_industry_comovement_2019.py",
                 "run_pre_wuhan_industry_response_2019.py", "run_pre_wuhan_background_response_2019.py"):
        path = ROOT / "research/simulation" / name
        code_hashes[str(path)] = file_sha256(path)
    upgrades = {"balanced_common_reference": upgrades,
                "background_reference": {portfolio_path: {"archived": config["reference_portfolio_source_sha256"],
                                                          "current": file_sha256(Path(portfolio_path))}}}
    return config, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership, catalog, design, dose_checks, reference_dir, reference


def reference_days(path: Path):
    started = False
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["variant"] == "background_valuation":
                started = True
                yield row
            elif started:
                break


def numeric_prefix(day: dict) -> dict:
    result = copy.deepcopy(day)
    for observation in result["observations"].values():
        for key in list(observation):
            if key.startswith("industry_") or key in {"scenario_id", "scenario_evidence"}:
                observation.pop(key)
    return result


def run(output: Path = OUTPUT, audit_existing: bool = False) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or (not audit_existing and output.exists()):
        raise ValueError("industry response outputs require a fresh direct research_outputs directory")
    (config, base, replay, market, codes, dates, quotes, joined, inputs, code_hashes, upgrades, membership,
     catalog, design, dose_checks, reference_dir, reference) = inputs_and_design()
    agents = [AgentParameters(**row) for row in replay["agents"]]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    baskets = [codes[index:index + 3] for index in range(0, len(codes), 3)]
    basket_ids = {code: index // 3 for index, code in enumerate(codes)}
    saved_paths = {row["basket_index"]: row for row in reference["path_summaries"] if row["variant"] == "background_valuation"}
    saved_daily = {(row["stock_code"], row["trade_date"]): row for row in reference["variants"][1]["daily_asset_rows"]}
    frozen_days = reference_days(reference_dir / "ledger.jsonl.gz")
    legacy_config = json.loads(BACKGROUND_CONFIG.read_text(encoding="utf-8"))
    prefixes, variants, paths = {}, [], []
    record_count, full_reference_days, prefix_checks = 0, 0, 0
    with tempfile.TemporaryDirectory(prefix="industry-response-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        with (stage / "ledger.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as ledger:
            for cell in design:
                daily, coverage = [], Counter()
                for basket_index, basket in enumerate(baskets):
                    uniform = cell["name"] == "uniform_common"
                    shocks = {"scenario_id": "common_only" if uniform else "common_component", "common": cell["common"],
                              "asset_specific": {code: [0.0] * len(dates) for code in basket}}
                    if uniform:
                        # Retain legacy signed zeros as well as numeric values so
                        # the canonical trace hash exactly matches its archive.
                        shocks = build_scenario_shocks(basket, len(dates), "common_only", config["pulse_sessions"],
                                                       config["pulse_polarities"], config["total_cross_stock_rms"],
                                                       0.0, legacy_config["issuer_seed"])
                    industry = subset_industry_shocks(cell["industry"], basket) if cell["industry"] else None
                    simulation = simulate_portfolio({code: joined[code] for code in basket}, agents, base["core"], base["background"],
                                                    base["venue"], base["feedback_parameters"], case, False,
                                                    scenario_shocks=shocks, background_response=config["background_response"], industry_shocks=industry)
                    if uniform and (simulation["summary"] != saved_paths[basket_index]["summary"]
                                    or simulation["participant_specs"] != saved_paths[basket_index]["participant_specs"]):
                        raise ValueError("uniform reference full summary or participant specs differ")
                    if industry and simulation["summary"]["industry_scenario"] != {key: industry[key] for key in ("scenario_id", "assignment_mode", "membership", "path_groups")}:
                        raise ValueError("simulation summary industry assignment differs")
                    state = initial_state(agents, base, basket, case)
                    histories = {code: [] for code in basket}
                    for session, day in enumerate(simulation["trace"]):
                        if (day["trade_date"] != dates[session]
                                or any(day[key] != joined[basket[0]][session][key] for key in ("signal_cutoff_date", "execution_reference_date"))):
                            raise ValueError("industry simulation clock differs from the frozen development source")
                        if uniform:
                            previous_day = next(frozen_days, None)
                            if previous_day is None or previous_day["basket_index"] != basket_index:
                                raise ValueError("full reference daily ledger coverage differs")
                            expected_day = {key: value for key, value in previous_day.items() if key not in {"variant", "basket_index"}}
                            if day != expected_day:
                                raise ValueError("uniform control full daily simulation differs from frozen source")
                            full_reference_days += 1
                        if session < config["pulse_sessions"][0]:
                            key = (basket_index, session)
                            if uniform:
                                prefixes[key] = numeric_prefix(day)
                            elif numeric_prefix(day) != prefixes[key]:
                                raise ValueError("industry scenarios changed the full pre-pulse numeric trace")
                            else:
                                prefix_checks += 1
                        verify_industry_delivery(day, simulation["participant_specs"], shocks, industry, session)
                        verify_strategy_quotes(day, state, base["venue"], simulation["participant_specs"])
                        for code in basket:
                            check_feedback({"feedback": day["observations"][code], "signal_cutoff_date": day["signal_cutoff_date"]}, histories[code], base["feedback_parameters"])
                            if industry is None:
                                verify_background_response(day, state, code, session, base["venue"], base["background"], shocks["common"][session],
                                                           config["background_response"], simulation["participant_specs"])
                            else:
                                group = industry["path_groups"][code]
                                verify_shared_background(day, state, code, session, base["venue"], base["background"], shocks["common"][session],
                                                         industry["by_group"][group][session], membership[code], group,
                                                         config["background_response"], simulation["participant_specs"])
                            coverage["background_demand_checks"] += base["background"]["participants"]
                        dense = session in {0, config["pulse_sessions"][0]}
                        state = audit_portfolio_day(day, state, base["venue"], session, dense=dense)
                        coverage["asset_ledger_checks"] += len(basket)
                        coverage["dense_asset_price_checks"] += len(basket) if dense else 0
                        ledger.write((json.dumps({"variant": cell["name"], "basket_index": basket_index, **day}, sort_keys=True,
                                                 ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                        record_count += 1
                        for code in basket:
                            call, observation = day["portfolio_auction"]["asset_calls"][code], day["observations"][code]
                            row = {"stock_code": code, "trade_date": day["trade_date"], "exchange": quotes[code]["symbol"].split(".")[0],
                                   "applied_price_tie_break": "nearest_prior", "signal_cutoff_date": day["signal_cutoff_date"],
                                   "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                                   "matched_volume": call["matched_volume"], "observed_return": joined[code][session]["observed_return"],
                                   **accepted_order_flow(call, simulation["participant_specs"]),
                                   **{key: observation[key] for key in ("scenario_id", "common_shock", "issuer_specific_shock", "scenario_shock")}}
                            histories[code].append(row)
                            row["quote_summaries"] = {kind: quote_summary([order for order in call["orders"] if simulation["participant_specs"][order["owner"]]["kind"] == kind],
                                                                         call["price_before_minor"]) for kind in ("strategy", "background")}
                            if uniform and row != saved_daily[code, day["trade_date"]]:
                                raise ValueError("uniform control full daily metric row differs from prior archive")
                            row.update({"industry_code": membership[code], "industry_shock": observation.get("industry_shock", 0.0),
                                        "industry_assignment_mode": observation.get("industry_assignment_mode"),
                                        "industry_path_group": observation.get("industry_path_group")})
                            daily.append(row)
                            coverage["execution_unavailable"] += int(not call["execution_available"])
                            coverage["zero_matched_volume"] += int(call["matched_volume"] == 0)
                    if state["prices"] != simulation["summary"]["final_prices_minor"]:
                        raise ValueError("independently reconstructed final industry prices differ")
                    paths.append({"variant": cell["name"], "basket_index": basket_index, "participant_specs": simulation["participant_specs"], "summary": simulation["summary"]})
                    if (basket_index + 1) % 10 == 0 or basket_index + 1 == len(baskets):
                        print(f"{cell['name']}: audited {basket_index + 1}/{len(baskets)} baskets", flush=True)
                industry_metrics, _, _, _ = diagnose(daily, membership, basket_ids)
                variants.append({"name": cell["name"], "dose_by_session": cell["dose_by_session"], "coverage": dict(coverage),
                                 "comparison": compare_pairs([(row["price_after_minor"] / row["price_before_minor"] - 1, row["observed_return"]) for row in daily]),
                                 "common_factor_metrics": common_factor_metrics(daily), "industry_metrics": industry_metrics,
                                 "total_matched_volume": sum(row["matched_volume"] for row in daily), "daily_asset_rows": daily})
        if next(frozen_days, None) is not None or full_reference_days != 1763 or prefix_checks != 1640:
            raise ValueError("reference or pre-pulse comparison count differs")
        result = {"pipeline_version": VERSION, "market_dataset_id": market["market_dataset_id"],
                  "sample": {"companies": len(codes), "selected_stock_codes": codes, "sessions": len(dates), "baskets": len(baskets),
                             "start_date": dates[0], "end_date": dates[-1], "company_days_per_variant": len(codes) * len(dates),
                             "industry_divisions": len(set(membership.values()))},
                  "industry_source": catalog["source"], "design": design, "background_response": config["background_response"],
                  "variants": variants, "path_summaries": paths,
                  "checks": {**dose_checks, "portfolio_ledger_records": record_count, "uniform_full_reference_objects": len(baskets),
                             "uniform_full_daily_reference_records": full_reference_days, "pre_pulse_full_numeric_trace_comparisons": prefix_checks,
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
                    raise ValueError(f"industry response artifact differs byte-for-byte: {name}")
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
                         "total_matched_volume": row["total_matched_volume"],
                         "raw_industry_correlation_gap": row["industry_metrics"]["synthetic"]["raw"]["within_minus_between"]} for row in result["variants"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
