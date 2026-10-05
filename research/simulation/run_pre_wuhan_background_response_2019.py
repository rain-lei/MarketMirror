"""Run a frozen valuation-by-liquidity stress grid with complete audited ledgers."""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import tempfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .audit_background_response import verify_background_response, verify_strategy_quotes
from .audit_feedback import check_feedback
from .audit_pre_wuhan_common_factor_2019 import common_factor_metrics
from .audit_synthetic_observed_returns import compare_pairs
from .calibrate_pre_wuhan_market import accepted_order_flow, audit_scenario_delivery
from .historical_replay import prepare_steps
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import initial_portfolio, simulate_portfolio
from .scenario_shocks import build_scenario_shocks
from .semantic_signal_join import join_steps

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_background_response_2019.json"
BASE = ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json"
REPLAY = ROOT / "research/configs/wuhan_pre_event_pit_replay_2020.json"
MARKET = ROOT / "research_outputs/wuhan_pit_market_prepared_2020_v2/market_manifest.json"
DOWNLOAD = ROOT / "research_outputs/wuhan_pit_market_download_2020/download_manifest.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_background_response_2019_v1"
VERSION = "pre-wuhan-background-news-liquidity-2x2-v1"
CELLS = [
    {"name": "strategy_only", "valuation_response_bps": 0, "pulse_participation_bps": 10000},
    {"name": "background_valuation", "valuation_response_bps": 200, "pulse_participation_bps": 10000},
    {"name": "background_liquidity_fade", "valuation_response_bps": 0, "pulse_participation_bps": 5000},
    {"name": "background_valuation_and_fade", "valuation_response_bps": 200, "pulse_participation_bps": 5000},
]


def verify_hashes(bindings: dict) -> None:
    for name, expected in bindings.items():
        if file_sha256(Path(name)) != expected:
            raise ValueError(f"research source/code hash differs: {Path(name).name}")


def load_inputs() -> tuple:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (config["version"] != VERSION or config["variants"] != CELLS
            or (config["development_start"], config["development_end"]) != ("2019-11-01", "2019-12-31")
            or config["common_amplitude"] != 0.20 or config["pulse_sessions"] != [10, 11, 26, 27]
            or config["pulse_polarities"] != [1, 1, -1, -1]):
        raise ValueError("frozen background response experiment grid differs")
    source_path = (CONFIG.parent / config["reference_archive"]).resolve()
    if file_sha256(source_path) != config["reference_archive_sha256"]:
        raise ValueError("frozen numerical reference archive hash differs")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if source["result"]["pipeline_version"] != "pre-wuhan-common-shock-balanced-sensitivity-v1":
        raise ValueError("numerical reference is not the balanced common-shock experiment")
    verify_hashes(source["input_sha256"])
    upgrade_path = str((ROOT / "research/simulation/portfolio_market.py").resolve())
    upgrades = {name: {"archived": old, "current": file_sha256(Path(name))}
                for name, old in source["code_sha256"].items() if file_sha256(Path(name)) != old}
    if set(upgrades) - {upgrade_path}:
        raise ValueError("unrelated reference code changed; review the numerical baseline first")
    base = json.loads(BASE.read_text(encoding="utf-8"))
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    market = json.loads(MARKET.read_text(encoding="utf-8"))
    download = json.loads(DOWNLOAD.read_text(encoding="utf-8"))
    if base["background"]["mode"] != "active":
        raise ValueError("grid requires active inventory-target background")
    groups = _load_market(MARKET.parent / "market_daily.csv", market)
    codes = source["result"]["sample"]["selected_stock_codes"]
    if len(codes) != 123 or codes != sorted(set(codes)):
        raise ValueError("frozen company sample differs")
    reference = next(row for row in source["result"]["variants"] if row["name"] == "common_only")
    reference_rows = {(row["stock_code"], row["trade_date"]): row for row in reference["daily_asset_rows"]}
    dates = sorted({key[1] for key in reference_rows})
    if len(dates) != 43 or len(reference_rows) != 5289 or len(reference["daily_asset_rows"]) != 5289:
        raise ValueError("reference company-day coverage differs")
    quotes = {row["symbol"].split(".")[1]: row for row in download["quote_checks"] if row["symbol"] != download["benchmark_symbol"]}
    joined = {}
    for code in codes:
        steps = prepare_steps(groups[code], dates[0], dates[-1], replay["momentum_sessions"], replay["volatility_sessions"])
        if [row["trade_date"] for row in steps] != dates:
            raise ValueError("selected stock lacks its complete development clock")
        joined[code] = join_steps(steps, code, [])
        for row in joined[code]:
            row["execution_available"] = row["execution_reference_date"] not in quotes[code]["suspended_sessions"]
            if reference_rows[code, row["trade_date"]]["observed_return"] != row["observed_return"]:
                raise ValueError("market target differs from the frozen paired reference")
    inputs = {**source["input_sha256"], str(source_path): file_sha256(source_path), str(CONFIG): file_sha256(CONFIG)}
    paths = [*(Path(name) for name in source["code_sha256"]), Path(__file__),
             ROOT / "research/simulation/background_response.py", ROOT / "research/simulation/audit_background_response.py",
             ROOT / "research/simulation/audit_feedback.py", ROOT / "research/simulation/audit_pre_wuhan_common_factor_2019.py"]
    code_hashes = {str(path.resolve()): file_sha256(path) for path in paths}
    return config, base, replay, market, codes, dates, quotes, joined, reference_rows, inputs, code_hashes, upgrades


def initial_state(agents, base, assets, case) -> dict:
    _, accounts, _, _ = initial_portfolio(agents, base["core"], base["background"], assets, case, base["venue"])
    return {"accounts": {name: asdict(account) for name, account in accounts.items()},
            "prices": {asset: base["venue"]["price_start_minor"] for asset in assets}, "fee_pool_minor": 0,
            "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
            "initial_shares": {asset: sum(account.shares[asset] for account in accounts.values()) for asset in assets}}


def quote_summary(orders: list[dict], prior: int) -> dict:
    offsets = [(order["limit_price_minor"] / prior - 1) * 10000 for order in orders]
    return {"orders": len(orders), "min_offset_bps": min(offsets) if offsets else None,
            "max_offset_bps": max(offsets) if offsets else None,
            "mean_absolute_offset_bps": statistics.fmean(abs(value) for value in offsets) if offsets else None}


def run(output: Path = OUTPUT, audit_existing: bool = False) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("background response outputs must be direct children of research_outputs")
    if not audit_existing and output.exists() and any(output.iterdir()):
        raise ValueError("use a fresh output directory")
    config, base, replay, market, codes, dates, quotes, joined, reference, inputs, code_hashes, upgrades = load_inputs()
    agents = [AgentParameters(**row) for row in replay["agents"]]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    baskets = [codes[i:i + 3] for i in range(0, len(codes), 3)]
    variants, paths, record_count = [], [], 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="background-response-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        ledger_path = stage / "ledger.jsonl.gz"
        with ledger_path.open("wb") as raw_handle, gzip.GzipFile(fileobj=raw_handle, mode="wb", filename="", mtime=0) as ledger:
            for cell in config["variants"]:
                response = {key: cell[key] for key in ("valuation_response_bps", "pulse_participation_bps")}
                daily, coverage = [], Counter()
                for basket_index, basket in enumerate(baskets):
                    shocks = build_scenario_shocks(basket, len(dates), "common_only", config["pulse_sessions"],
                                                   config["pulse_polarities"], config["common_amplitude"], 0.0, config["issuer_seed"])
                    simulation = simulate_portfolio({stock: joined[stock] for stock in basket}, agents, base["core"],
                                                    base["background"], base["venue"], base["feedback_parameters"], case, False,
                                                    scenario_shocks=shocks, background_response=response)
                    audit_scenario_delivery(simulation, basket, shocks)
                    state = initial_state(agents, base, basket, case)
                    histories = {stock: [] for stock in basket}
                    for session, day in enumerate(simulation["trace"]):
                        if (day["trade_date"] != dates[session]
                                or any(day[key] != joined[basket[0]][session][key] for key in ("signal_cutoff_date", "execution_reference_date"))):
                            raise ValueError("response simulation clock differs from fixed source")
                        verify_strategy_quotes(day, state, base["venue"], simulation["participant_specs"])
                        for stock in basket:
                            check_feedback({"feedback": day["observations"][stock], "signal_cutoff_date": day["signal_cutoff_date"]},
                                           histories[stock], base["feedback_parameters"])
                            verify_background_response(day, state, stock, session, base["venue"], base["background"],
                                                       shocks["common"][session], response, simulation["participant_specs"])
                            coverage["background_demand_checks"] += base["background"]["participants"]
                        dense = session in {0, config["pulse_sessions"][0]}
                        state = audit_portfolio_day(day, state, base["venue"], session, dense=dense)
                        coverage["asset_ledger_checks"] += len(basket)
                        coverage["dense_asset_price_checks"] += len(basket) if dense else 0
                        ledger.write((json.dumps({"variant": cell["name"], "basket_index": basket_index, **day},
                                                 sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                        record_count += 1
                        for stock in basket:
                            call = day["portfolio_auction"]["asset_calls"][stock]
                            observation = day["observations"][stock]
                            row = {"stock_code": stock, "trade_date": day["trade_date"],
                                   "exchange": quotes[stock]["symbol"].split(".")[0], "applied_price_tie_break": "nearest_prior",
                                   "signal_cutoff_date": day["signal_cutoff_date"], "price_before_minor": call["price_before_minor"],
                                   "price_after_minor": call["price_after_minor"], "matched_volume": call["matched_volume"],
                                   "observed_return": joined[stock][session]["observed_return"],
                                   **accepted_order_flow(call, simulation["participant_specs"]),
                                   **{key: observation[key] for key in ("scenario_id", "common_shock", "issuer_specific_shock", "scenario_shock")}}
                            if cell["name"] == "strategy_only" and row != reference[stock, day["trade_date"]]:
                                raise ValueError("zero response fails exact frozen numerical row reproduction")
                            histories[stock].append(row)
                            row["quote_summaries"] = {kind: quote_summary([order for order in call["orders"]
                                                                          if simulation["participant_specs"][order["owner"]]["kind"] == kind],
                                                                         call["price_before_minor"]) for kind in ("strategy", "background")}
                            daily.append(row)
                            coverage["execution_unavailable"] += int(not call["execution_available"])
                            coverage["zero_matched_volume"] += int(call["matched_volume"] == 0)
                            for demand in day["background_demands"][stock].values():
                                detail = demand.get("common_news_response")
                                if detail:
                                    coverage["response_opportunities"] += 1
                                    coverage["response_participation_draws"] += int(detail["participates"])
                                    coverage["withheld_positive_base_orders"] += int(not detail["participates"] and detail["base_requested_quantity"] > 0)
                    if state["prices"] != simulation["summary"]["final_prices_minor"]:
                        raise ValueError("independent closing prices differ from simulation")
                    paths.append({"variant": cell["name"], "basket_index": basket_index,
                                  "participant_specs": simulation["participant_specs"], "summary": simulation["summary"]})
                    if (basket_index + 1) % 10 == 0 or basket_index + 1 == len(baskets):
                        print(f"{cell['name']}: audited {basket_index + 1}/{len(baskets)} baskets", flush=True)
                pairs = [(row["price_after_minor"] / row["price_before_minor"] - 1, row["observed_return"]) for row in daily]
                variants.append({**cell, "coverage": dict(coverage), "comparison": compare_pairs(pairs),
                                 "common_factor_metrics": common_factor_metrics(daily),
                                 "total_matched_volume": sum(row["matched_volume"] for row in daily), "daily_asset_rows": daily})
        result = {"pipeline_version": VERSION, "market_dataset_id": market["market_dataset_id"],
                  "sample": {"companies": len(codes), "selected_stock_codes": codes, "sessions": len(dates), "baskets": len(baskets),
                             "start_date": dates[0], "end_date": dates[-1], "company_days_per_variant": 5289},
                  "variants": variants, "path_summaries": paths,
                  "checks": {"zero_response_exact_reference_company_days": 5289, "portfolio_ledger_records": record_count,
                             "source_clock": "t-2 information, t-1 execution-reference availability, t-labelled synthetic step; not exchange same-day replication"},
                  "interpretation": config["interpretation"]}
        result_path = stage / "results.json"
        result_path.write_text(json.dumps({"result": result}, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        verify_hashes(inputs)
        verify_hashes(code_hashes)
        manifest = {"pipeline_version": VERSION, "inputs": inputs, "code_sha256": code_hashes,
                    "reviewed_reference_code_upgrades": upgrades,
                    "artifacts": {name: {"sha256": file_sha256(stage / name)} for name in ("results.json", "ledger.jsonl.gz")}}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if audit_existing:
            for name in ("results.json", "ledger.jsonl.gz", "manifest.json"):
                if file_sha256(stage / name) != file_sha256(output / name):
                    raise ValueError(f"archived background response artifact differs: {name}")
        else:
            if output.exists():
                output.rmdir()
            stage.replace(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = run(args.output_dir, args.audit_existing)
    print(json.dumps({"sample": {key: value for key, value in result["sample"].items() if key != "selected_stock_codes"},
                      "variants": [{"name": row["name"], **row["comparison"]["synthetic"], "total_matched_volume": row["total_matched_volume"]}
                                   for row in result["variants"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
