"""Rebuild archived Wuhan no-text portfolio paths from their saved ledger."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .audit_feedback import check_feedback
from .audit_portfolio import independent_covariance
from .portfolio_auction import PortfolioAccount
from .portfolio_market import initial_portfolio, portfolio_decision
from .wuhan_portfolio_baseline import _load_inputs, load_summary


def audit_directory(directory: Path, config_path: Path) -> dict:
    directory, config_path = directory.resolve(), config_path.resolve()
    summary = load_summary(directory)
    manifest = json.loads((directory / "portfolio_no_text_manifest.json").read_text(encoding="utf-8"))
    cfg, replay, market, agents, joined, baskets, inputs = _load_inputs(config_path)
    if manifest["inputs"] != inputs or summary["run_id"] != cfg["run_id"]:
        raise ValueError("portfolio audit source binding differs from the archived run")
    paths = json.loads((directory / "portfolio_no_text_paths.json").read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in cfg["cases"]}
    expected = {f"{index}:{case_id}" for index in range(len(baskets)) for case_id in cases}
    by_id = {row["path_id"]: row for row in paths}
    if len(by_id) != len(paths) or set(by_id) != expected or summary["paths"] != len(expected):
        raise ValueError("portfolio path coverage differs from the frozen baskets")
    states, counts, histories, cohorts, specs, initial_wealth, policy, hashes = {}, Counter(), {}, {}, {}, {}, {}, {}
    totals, peaks, drawdowns = {}, {}, {}
    rows, asset_calls, traded, halted = 0, 0, 0, 0
    from .portfolio_audit import audit_portfolio_day
    with gzip.open(directory / "portfolio_no_text_ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            key = record.pop("path_id")
            if key not in by_id:
                raise ValueError("ledger contains an undeclared portfolio path")
            path = by_id[key]
            assets = baskets[path["basket_index"]]
            case = cases[path["case_id"]]
            if key != f"{path['basket_index']}:{path['case_id']}" or path["assets"] != assets or path["use_text"] is not False:
                raise ValueError("portfolio path identity or text mode differs")
            if key not in states:
                from dataclasses import asdict
                cohort, accounts, specification, wealth = initial_portfolio(
                    agents, cfg["core"], cfg["background"], assets, case, cfg["venue"])
                cohorts[key], specs[key], initial_wealth[key] = cohort, specification, wealth
                states[key] = {"accounts": {name: asdict(account) for name, account in accounts.items()},
                               "prices": {asset: cfg["venue"]["price_start_minor"] for asset in assets},
                               "fee_pool_minor": 0,
                               "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
                               "initial_shares": {asset: sum(account.shares[asset] for account in accounts.values()) for asset in assets}}
                histories[key] = {asset: [] for asset in assets}
                policy[key] = {agent.name: {"sign": 0, "streak": 0} for agent, _ in cohort}
                hashes[key] = hashlib.sha256(b"[")
                totals[key] = Counter()
                peaks[key] = dict(wealth)
                drawdowns[key] = {name: 0.0 for name in wealth}
            session = counts[key]
            if session >= len(joined[assets[0]]):
                raise ValueError("portfolio ledger contains an extra session")
            source = joined[assets[0]][session]
            if any(record[name] != source[name] for name in
                   ("trade_date", "signal_cutoff_date", "execution_reference_date")):
                raise ValueError("portfolio ledger date or information cutoff differs from source")
            if set(record["observations"]) != set(assets) or set(record["covariance"]) != set(assets):
                raise ValueError("portfolio observation or covariance coverage differs")
            cov = independent_covariance(histories[key], record["signal_cutoff_date"], cfg["feedback_parameters"])
            if any(set(record["covariance"][asset]) != set(assets) or any(
                    not math.isclose(record["covariance"][asset][other], cov[asset][other],
                                     rel_tol=1e-12, abs_tol=1e-12) for other in assets)
                   for asset in assets):
                raise ValueError("portfolio covariance differs from prior own-price history")
            for asset in assets:
                observation = record["observations"][asset]
                check_feedback({"signal_cutoff_date": record["signal_cutoff_date"],
                                "feedback": observation}, histories[key][asset], cfg["feedback_parameters"])
                call = record["portfolio_auction"]["asset_calls"][asset]
                if (observation["text_signal"] != 0 or observation["text_uncertainty"] != 0
                        or observation["text_evidence"] != "text:disabled"
                        or call["execution_available"] != joined[asset][session]["execution_available"]):
                    raise ValueError("no-text observation or suspension status differs")
            if set(record["decisions"]) != set(policy[key]):
                raise ValueError("strategy decision coverage differs")
            for agent, profile in cohorts[key]:
                account = PortfolioAccount(**states[key]["accounts"][agent.name])
                decision = portfolio_decision(agent, profile, account, states[key]["prices"],
                                              record["observations"], record["covariance"], case,
                                              policy[key][agent.name], session, False)
                if record["decisions"][agent.name] != decision:
                    differing = [field for field in decision if record["decisions"][agent.name].get(field) != decision[field]]
                    raise ValueError(f"strategy decision differs from prior state and no-text observations: {key} session {session} {agent.name} {differing}")
            states[key] = audit_portfolio_day(record, states[key], cfg["venue"], session)
            for asset in assets:
                call = record["portfolio_auction"]["asset_calls"][asset]
                histories[key][asset].append({"trade_date": record["trade_date"],
                                              "price_before_minor": call["price_before_minor"],
                                              "price_after_minor": call["price_after_minor"]})
                asset_calls += 1
                traded += int(call["matched_volume"] > 0)
                halted += int(not call["execution_available"])
                for order in call["orders"]:
                    if specs[key][order["owner"]]["kind"] == "strategy":
                        for field in ("quantity", "accepted_quantity", "filled_quantity"):
                            totals[key][field] += order[field]
                        totals[key]["cash_clipped_orders"] += int("cash_and_fee_reservation" in order["reasons"])
            for name, account in states[key]["accounts"].items():
                wealth = sum(account["wallets"].values()) + sum(
                    account["shares"][asset] * states[key]["prices"][asset] for asset in assets)
                peaks[key][name] = max(peaks[key][name], wealth)
                drawdowns[key][name] = max(drawdowns[key][name], 1 - wealth / peaks[key][name])
            if session:
                hashes[key].update(b", ")
            hashes[key].update(json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
            counts[key] += 1
            rows += 1
    if set(counts) != expected or rows != summary["ledger_rows"] or asset_calls != summary["asset_call_rows"]:
        raise ValueError("portfolio ledger coverage or asset calls differ")
    for key, path in by_id.items():
        state = states[key]
        hashes[key].update(b"]")
        if (counts[key] != len(joined[path["assets"][0]]) or path["sessions"] != counts[key]
                or path["trace_sha256"] != hashes[key].hexdigest()
                or path["final_prices_minor"] != state["prices"]
                or path["fee_pool_minor"] != state["fee_pool_minor"]):
            raise ValueError("portfolio path trace or final prices differ")
        for field, total in (("strategy_requested", totals[key]["quantity"]),
                             ("strategy_accepted", totals[key]["accepted_quantity"]),
                             ("strategy_filled", totals[key]["filled_quantity"]),
                             ("cash_clipped_orders", totals[key]["cash_clipped_orders"])):
            if path[field] != total:
                raise ValueError("portfolio strategy order total differs")
        for name, account in state["accounts"].items():
            saved = path["accounts"][name]
            final_wealth = sum(account["wallets"].values()) + sum(
                account["shares"][asset] * state["prices"][asset] for asset in path["assets"])
            if (any(saved[field] != account[field] for field in ("wallets", "shares", "sellable"))
                    or saved["initial_wealth_minor"] != initial_wealth[key][name]
                    or saved["final_wealth_minor"] != final_wealth
                    or saved["wealth_multiple"] != final_wealth / initial_wealth[key][name]
                    or saved["max_drawdown"] != drawdowns[key][name]):
                raise ValueError("portfolio account or wealth metrics differ")
        for role in ("aggressive", "conservative", "institutional"):
            names = [name for name, info in specs[key].items()
                     if info["kind"] == "strategy" and info["parameters"]["role"] == role]
            final = sum(path["accounts"][name]["final_wealth_minor"] for name in names)
            initial = sum(initial_wealth[key][name] for name in names)
            if path["role_wealth_multiple"][role] != final / initial:
                raise ValueError("portfolio role wealth differs")
    for group in summary["grouped"]:
        selected = [path for path in paths if path["case_id"] == group["case_id"]]
        requested, accepted, filled = (sum(path[field] for path in selected) for field in
                                       ("strategy_requested", "strategy_accepted", "strategy_filled"))
        exact = {"baskets": len(selected), "strategy_requested": requested,
                 "strategy_accepted": accepted, "strategy_filled": filled,
                 "cash_clipped_orders": sum(path["cash_clipped_orders"] for path in selected)}
        if any(group[field] != value for field, value in exact.items()):
            raise ValueError("portfolio grouped integer totals differ")
        mean_price = statistics.mean(price / cfg["venue"]["price_start_minor"]
                                     for path in selected for price in path["final_prices_minor"].values())
        if (group["strategy_fill_fraction"] != (filled / accepted if accepted else 0.0)
                or group["mean_final_price_multiple"] != mean_price):
            raise ValueError("portfolio grouped rate or price differs")
        for role in ("aggressive", "conservative", "institutional"):
            if group["role_wealth_multiple"][role] != statistics.mean(
                    path["role_wealth_multiple"][role] for path in selected):
                raise ValueError("portfolio grouped role wealth differs")
    return {"experiment_id": summary["experiment_id"], "market_dataset_id": market["market_dataset_id"],
            "stocks": len(joined), "paths": len(paths), "audited_portfolio_days": rows,
            "audited_asset_calls": asset_calls, "traded_asset_calls": traded,
            "halted_asset_calls": halted, "source_files": len(inputs),
            "checks": {"source_and_artifact_hashes": True, "complete_fixed_cohort": True,
                       "no_text_and_as_of_cutoffs": True, "own_price_feedback_and_covariance": True,
                       "decision_reexecution": True, "wallets_shares_and_auction": True,
                       "path_and_grouped_metrics": True}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = audit_directory(args.directory, args.config)
    if args.output_dir is not None:
        directory, config_path, output_dir = args.directory.resolve(), args.config.resolve(), args.output_dir.resolve()
        if (output_dir == directory or output_dir in directory.parents or directory in output_dir.parents
                or output_dir == config_path or output_dir in config_path.parents
                or output_dir.exists() and any(output_dir.iterdir())):
            raise ValueError("audit output must be a new directory separate from inputs")
        sources = (directory / "portfolio_no_text_manifest.json",
                   directory / "portfolio_no_text_summary.json",
                   directory / "portfolio_no_text_paths.json",
                   directory / "portfolio_no_text_ledger.jsonl.gz", config_path)
        bindings = {str(path): file_sha256(path) for path in sources}
        code_hash = file_sha256(Path(__file__))
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
            staging = Path(temporary)
            saved = staging / "portfolio_no_text_audit.json"
            saved.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            report = staging / "portfolio_no_text_audit.md"
            report.write_text(
                "# 武汉无文本组合实验独立审计\n\n"
                f"实验 ID：`{result['experiment_id']}`。核验 {result['stocks']} 家公司、"
                f"{result['paths']} 条路径、{result['audited_portfolio_days']:,} 个组合日、"
                f"{result['audited_asset_calls']:,} 次资产竞价和 {result['source_files']} 份来源文件。\n\n"
                f"有成交竞价 {result['traded_asset_calls']:,} 次；停牌/不可执行竞价 "
                f"{result['halted_asset_calls']} 次。来源哈希、时点、无文本观测、反馈、决策、"
                "现金与股份重建、路径和分组汇总均通过。\n",
                encoding="utf-8")
            manifest = {"generated_at": datetime.now(timezone.utc).isoformat(),
                        "experiment_id": result["experiment_id"], "inputs": bindings,
                        "audit_code_sha256": code_hash,
                        "artifacts": {path.name: {"sha256": file_sha256(path)} for path in (saved, report)}}
            (staging / "portfolio_no_text_audit_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            for path in staging.iterdir():
                path.replace(output_dir / path.name)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
