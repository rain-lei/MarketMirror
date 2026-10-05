"""Independently inspect archived reply-keyword portfolio observations and ledgers."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .audit_feedback import check_feedback
from .audit_portfolio import independent_covariance
from .portfolio_audit import audit_portfolio_day
from .portfolio_auction import PortfolioAccount
from .portfolio_market import initial_portfolio, portfolio_decision
from .semantic_memory_sensitivity import canonical_hash
from .semantic_signal_join import join_steps
from .wuhan_portfolio_keyword import ARTIFACTS, CODE_PATHS, VERSION, _load


def audit_directory(directory: Path, config_path: Path) -> dict:
    directory, config_path = directory.resolve(), config_path.resolve()
    manifest = json.loads((directory / "keyword_portfolio_manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((directory / "keyword_portfolio_summary.json").read_text(encoding="utf-8"))
    (settings, cfg, replay, market, agents, joined, baskets, controls, rows,
     question_only, reply_items, sources, source_dirs) = _load(config_path)
    if (manifest.get("pipeline_version") != VERSION or summary.get("pipeline_version") != VERSION
            or manifest.get("inputs") != sources
            or manifest.get("code_sha256") != {name: file_sha256(path) for name, path in CODE_PATHS.items()}
            or canonical_hash({key: manifest[key] for key in ("inputs", "code_sha256", "runtime")}) != manifest.get("experiment_id")
            or summary.get("experiment_id") != manifest["experiment_id"]
            or summary.get("baseline_experiment_id") != json.loads(
                (source_dirs["baseline_directory"] / "portfolio_no_text_summary.json").read_text(encoding="utf-8"))["experiment_id"]
            or summary.get("market_dataset_id") != market["market_dataset_id"]
            or summary.get("stocks") != len(joined) or summary.get("reply_items") != reply_items
            or summary.get("reply_keyword_companies") != len(rows["0.25"])
            or summary.get("question_only_hits_excluded") != question_only
            or summary.get("companies_without_reply") != len(joined) - reply_items
            or summary.get("baskets") != baskets or summary.get("cases") != cfg["cases"]
            or set(manifest.get("artifacts", {})) != ARTIFACTS
            or any(file_sha256(directory / name) != info["sha256"]
                   for name, info in manifest["artifacts"].items())):
        raise ValueError("keyword portfolio identity, source or artifact differs")
    paths = json.loads((directory / "keyword_portfolio_paths.json").read_text(encoding="utf-8"))
    by_id = {path["path_id"]: path for path in paths}
    expected = [(index, case, level, f"{index}:{case['case_id']}:attention_{level}")
                for index in range(len(baskets)) for case in cfg["cases"]
                for level in settings["attention_levels"]]
    if len(paths) != len(by_id) or set(by_id) != {key for _, _, _, key in expected} or summary["paths"] != len(expected):
        raise ValueError("keyword path coverage differs from fixed cohort")
    day_count, asset_calls, traded, halted = 0, 0, 0, 0
    with gzip.open(directory / "keyword_portfolio_ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
        for index, case, level, key in expected:
            assets, path = baskets[index], by_id[key]
            baseline_id = f"{index}:{case['case_id']}"
            control = controls[baseline_id]
            if (path["assets"] != assets or path["basket_index"] != index
                    or path["case_id"] != case["case_id"] or path["attention_level"] != level
                    or path["baseline_path_id"] != baseline_id or path["use_text"] is not True):
                raise ValueError("keyword path metadata differs")
            active = {asset: join_steps(joined[asset], asset, rows[str(level)]) for asset in assets}
            cohort, accounts, specs, initial_wealth = initial_portfolio(
                agents, cfg["core"], cfg["background"], assets, case, cfg["venue"])
            state = {"accounts": {name: asdict(account) for name, account in accounts.items()},
                     "prices": {asset: cfg["venue"]["price_start_minor"] for asset in assets},
                     "fee_pool_minor": 0,
                     "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
                     "initial_shares": {asset: sum(account.shares[asset] for account in accounts.values()) for asset in assets}}
            histories = {asset: [] for asset in assets}
            policy = {agent.name: {"sign": 0, "streak": 0} for agent, _ in cohort}
            peaks = dict(initial_wealth)
            drawdowns = {name: 0.0 for name in accounts}
            totals = Counter()
            digest = hashlib.sha256(b"[")
            for session in range(len(active[assets[0]])):
                line = stream.readline()
                if not line:
                    raise ValueError("keyword ledger ended before all paths")
                record = json.loads(line)
                if record.pop("path_id") != key:
                    raise ValueError("keyword ledger path order differs")
                source_step = active[assets[0]][session]
                if any(record[field] != source_step[field] for field in
                       ("trade_date", "signal_cutoff_date", "execution_reference_date")):
                    raise ValueError("keyword ledger time cutoff differs")
                if set(record["observations"]) != set(assets):
                    raise ValueError("keyword observation coverage differs")
                expected_cov = independent_covariance(histories, record["signal_cutoff_date"], cfg["feedback_parameters"])
                if any(set(record["covariance"].get(asset, {})) != set(assets) or any(
                        not math.isclose(record["covariance"][asset][other], expected_cov[asset][other],
                                         rel_tol=1e-12, abs_tol=1e-12) for other in assets)
                       for asset in assets):
                    raise ValueError("keyword covariance differs from prior simulated prices")
                for asset in assets:
                    observation = record["observations"][asset]
                    step = active[asset][session]
                    check_feedback({"signal_cutoff_date": record["signal_cutoff_date"],
                                    "feedback": observation}, histories[asset], cfg["feedback_parameters"])
                    if (observation["text_signal"] != 0 or observation["text_uncertainty"] != step["text_uncertainty"]
                            or observation["text_evidence"] != step["text_evidence"]
                            or observation["text_uncertainty"] not in (0.0, level)
                            or record["portfolio_auction"]["asset_calls"][asset]["execution_available"] != step["execution_available"]):
                        raise ValueError("keyword attention, time or suspension differs from source")
                if set(record["decisions"]) != set(policy):
                    raise ValueError("keyword strategy decision coverage differs")
                for agent, profile in cohort:
                    decision = portfolio_decision(agent, profile,
                                                  PortfolioAccount(**state["accounts"][agent.name]),
                                                  state["prices"], record["observations"], record["covariance"],
                                                  case, policy[agent.name], session, True)
                    if record["decisions"][agent.name] != decision:
                        raise ValueError("keyword strategy decision differs from prior state")
                state = audit_portfolio_day(record, state, cfg["venue"], session)
                for asset in assets:
                    call = record["portfolio_auction"]["asset_calls"][asset]
                    histories[asset].append({"trade_date": record["trade_date"],
                                             "price_before_minor": call["price_before_minor"],
                                             "price_after_minor": call["price_after_minor"]})
                    asset_calls += 1
                    traded += int(call["matched_volume"] > 0)
                    halted += int(not call["execution_available"])
                    for order in call["orders"]:
                        if specs[order["owner"]]["kind"] == "strategy":
                            for field in ("quantity", "accepted_quantity", "filled_quantity"):
                                totals[field] += order[field]
                            totals["cash_clipped_orders"] += int("cash_and_fee_reservation" in order["reasons"])
                for name, account in state["accounts"].items():
                    wealth = sum(account["wallets"].values()) + sum(
                        account["shares"][asset] * state["prices"][asset] for asset in assets)
                    peaks[name] = max(peaks[name], wealth)
                    drawdowns[name] = max(drawdowns[name], 1 - wealth / peaks[name])
                if session:
                    digest.update(b", ")
                digest.update(json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
                day_count += 1
            digest.update(b"]")
            if (path["sessions"] != len(active[assets[0]]) or path["trace_sha256"] != digest.hexdigest()
                    or path["final_prices_minor"] != state["prices"]
                    or path["fee_pool_minor"] != state["fee_pool_minor"]):
                raise ValueError("keyword path trace, final price or fee pool differs")
            for field, value in (("strategy_requested", totals["quantity"]),
                                 ("strategy_accepted", totals["accepted_quantity"]),
                                 ("strategy_filled", totals["filled_quantity"]),
                                 ("cash_clipped_orders", totals["cash_clipped_orders"])):
                if path[field] != value:
                    raise ValueError("keyword path order totals differ")
            for name, account in state["accounts"].items():
                saved = path["accounts"][name]
                final_wealth = sum(account["wallets"].values()) + sum(
                    account["shares"][asset] * state["prices"][asset] for asset in assets)
                if (any(saved[field] != account[field] for field in ("wallets", "shares", "sellable"))
                        or saved["initial_wealth_minor"] != initial_wealth[name]
                        or saved["final_wealth_minor"] != final_wealth
                        or saved["wealth_multiple"] != final_wealth / initial_wealth[name]
                        or saved["max_drawdown"] != drawdowns[name]):
                    raise ValueError("keyword account and wealth measures differ")
            for role in ("aggressive", "conservative", "institutional"):
                names = [name for name, info in specs.items()
                         if info["kind"] == "strategy" and info["parameters"]["role"] == role]
                final = sum(path["accounts"][name]["final_wealth_minor"] for name in names)
                initial = sum(initial_wealth[name] for name in names)
                if (path["role_wealth_multiple"][role] != final / initial
                        or path["role_wealth_multiple_delta"][role] != final / initial - control["role_wealth_multiple"][role]):
                    raise ValueError("keyword role wealth or paired delta differs")
            if (path["final_price_delta_minor"] != {
                    a: path["final_prices_minor"][a] - control["final_prices_minor"][a] for a in assets}
                    or path["strategy_filled_delta"] != path["strategy_filled"] - control["strategy_filled"]):
                raise ValueError("keyword path paired baseline delta differs")
        if stream.readline():
            raise ValueError("keyword ledger has undeclared extra rows")
    if (day_count != summary["ledger_rows"] or asset_calls != summary["asset_call_rows"]
            or day_count != summary["audited_portfolio_days"]):
        raise ValueError("keyword ledger coverage differs")
    for group in summary["grouped"]:
        selected = [p for p in paths if p["case_id"] == group["case_id"]
                    and p["attention_level"] == group["attention_level"]]
        requested = sum(p["strategy_requested"] for p in selected)
        accepted = sum(p["strategy_accepted"] for p in selected)
        filled = sum(p["strategy_filled"] for p in selected)
        exact = {"baskets": len(selected), "strategy_requested": requested,
                 "strategy_accepted": accepted, "strategy_filled": filled,
                 "strategy_filled_delta_vs_no_text": sum(p["strategy_filled_delta"] for p in selected),
                 "cash_clipped_orders": sum(p["cash_clipped_orders"] for p in selected),
                 "strategy_fill_fraction": filled / accepted if accepted else 0.0,
                 "mean_final_price_delta_multiple": statistics.mean(
                     delta / cfg["venue"]["price_start_minor"] for p in selected
                     for delta in p["final_price_delta_minor"].values()),
                 "mean_role_wealth_multiple_delta": {role: statistics.mean(
                     p["role_wealth_multiple_delta"][role] for p in selected)
                     for role in ("aggressive", "conservative", "institutional")}}
        if any(group[field] != value for field, value in exact.items()):
            raise ValueError("keyword grouped measure differs from archived paths")
    return {"experiment_id": summary["experiment_id"], "baseline_experiment_id": summary["baseline_experiment_id"],
            "stocks": len(joined), "paths": len(paths), "audited_portfolio_days": day_count,
            "audited_asset_calls": asset_calls, "traded_asset_calls": traded,
            "halted_asset_calls": halted, "source_files": len(sources),
            "checks": {"source_code_and_artifact_hashes": True, "fixed_cohort_and_reply_only": True,
                       "as_of_attention_and_suspensions": True, "own_price_feedback_and_covariance": True,
                       "decision_reexecution": True, "wallets_shares_and_auction": True,
                       "trace_path_and_grouped_metrics": True}}


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
        source_paths = [directory / name for name in (*sorted(ARTIFACTS), "keyword_portfolio_manifest.json")]
        bindings = {str(path): file_sha256(path) for path in (*source_paths, config_path)}
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
            staging = Path(temporary)
            saved = staging / "keyword_portfolio_audit.json"
            saved.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            report = staging / "keyword_portfolio_audit.md"
            report.write_text("# 武汉回复关键词组合实验独立审计\n\n"
                              f"实验 ID：`{result['experiment_id']}`。覆盖 {result['stocks']} 家公司、"
                              f"{result['paths']} 条路径、{result['audited_portfolio_days']:,} 个组合日、"
                              f"{result['audited_asset_calls']:,} 次资产竞价。\n\n"
                              "来源、回复限定、时点、内生反馈、策略重执行、资金股份、配对差值和汇总均通过。\n",
                              encoding="utf-8")
            manifest = {"generated_at": datetime.now(timezone.utc).isoformat(),
                        "experiment_id": result["experiment_id"], "inputs": bindings,
                        "audit_code_sha256": file_sha256(Path(__file__)),
                        "artifacts": {path.name: {"sha256": file_sha256(path)} for path in (saved, report)}}
            (staging / "keyword_portfolio_audit_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            for path in staging.iterdir():
                path.replace(output_dir / path.name)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
