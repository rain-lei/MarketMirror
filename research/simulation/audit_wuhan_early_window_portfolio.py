"""Independently audit the saved early-window Wuhan portfolio ledger."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .audit_feedback import check_feedback
from .audit_portfolio import independent_covariance
from .portfolio_auction import PortfolioAccount
from .portfolio_market import initial_portfolio, portfolio_decision
from .semantic_memory_sensitivity import canonical_hash
from .wuhan_early_window_portfolio import (ARTIFACTS, LEDGER_NAME, VERSION,
                                            _load_warm_inputs, load_summary)


AUDIT_ARTIFACTS = {"early_window_audit.json", "early_window_audit.md"}
AUDIT_CODE_PATHS = {
    name: Path(__file__).resolve().parents[1] / name
    for name in (
        "simulation/audit_wuhan_early_window_portfolio.py",
        "simulation/wuhan_early_window_portfolio.py",
        "simulation/audit_portfolio.py",
        "simulation/audit_feedback.py",
        "simulation/portfolio_audit.py",
        "simulation/audit_background.py",
        "simulation/audit_auction.py",
        "simulation/call_auction.py",
        "simulation/feedback_auction.py",
        "simulation/portfolio_market.py",
        "simulation/portfolio_auction.py",
        "simulation/agents.py",
        "simulation/semantic_memory_sensitivity.py",
        "simulation/wuhan_portfolio_baseline.py",
        "simulation/historical_replay.py",
        "simulation/semantic_signal_join.py",
        "simulation/semantic_auction_experiment.py",
        "baselines/run_experiments.py",
        "data_pipeline/market_data.py",
        "data_pipeline/provenance.py",
    )
}


def audit_directory(directory: Path, config_path: Path) -> dict:
    directory, config_path = directory.resolve(), config_path.resolve()
    summary = load_summary(directory)
    manifest = json.loads((directory / "early_window_manifest.json").read_text(encoding="utf-8"))
    window = manifest["window"]
    cfg, replay, market, agents, joined, baskets, inputs, overlap = _load_warm_inputs(
        config_path, window["start"], window["end"])
    if (manifest["inputs"] != inputs or summary["run_id"] != cfg["run_id"]
            or summary["market_dataset_id"] != market["market_dataset_id"]
            or summary["dates"] != {"start": window["start"], "end": window["end"]}
            or summary["strict_overlap_step_parity"] is not True
            or not all(overlap.values())):
        raise ValueError("early-window audit source binding or warm-start parity differs")
    paths = json.loads((directory / "early_window_paths.json").read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in cfg["cases"]}
    expected = {f"{index}:{case_id}" for index in range(len(baskets)) for case_id in cases}
    by_id = {row["path_id"]: row for row in paths}
    if (len(by_id) != len(paths) or set(by_id) != expected
            or summary["paths"] != len(expected) or summary["baskets"] != baskets):
        raise ValueError("early-window path coverage differs from frozen baskets")

    states, counts, histories, cohorts, specs = {}, Counter(), {}, {}, {}
    initial_wealth, policy, trace_hashes = {}, {}, {}
    peaks, drawdowns = {}, {}
    rows = asset_calls = traded = halted = 0
    with gzip.open(directory / LEDGER_NAME, "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            key = record.pop("path_id", None)
            if key not in by_id:
                raise ValueError("early-window ledger contains an undeclared path")
            path = by_id[key]
            assets = baskets[path["basket_index"]]
            case = cases[path["case_id"]]
            if (key != f"{path['basket_index']}:{path['case_id']}"
                    or path["assets"] != assets or path["use_text"] is not False):
                raise ValueError("early-window path identity or text mode differs")
            if key not in states:
                cohort, accounts, specification, wealth = initial_portfolio(
                    agents, cfg["core"], cfg["background"], assets, case, cfg["venue"])
                cohorts[key], specs[key], initial_wealth[key] = cohort, specification, wealth
                states[key] = {"accounts": {name: asdict(account) for name, account in accounts.items()},
                               "prices": {asset: cfg["venue"]["price_start_minor"] for asset in assets},
                               "fee_pool_minor": 0,
                               "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
                               "initial_shares": {asset: sum(account.shares[asset] for account in accounts.values())
                                                  for asset in assets}}
                histories[key] = {asset: [] for asset in assets}
                policy[key] = {agent.name: {"sign": 0, "streak": 0} for agent, _ in cohort}
                trace_hashes[key] = hashlib.sha256(b"[")
                peaks[key] = dict(wealth)
                drawdowns[key] = {name: 0.0 for name in wealth}
            session = counts[key]
            if session >= len(joined[assets[0]]):
                raise ValueError("early-window ledger contains an extra session")
            source = joined[assets[0]][session]
            if any(record[name] != source[name] for name in
                   ("trade_date", "signal_cutoff_date", "execution_reference_date")):
                raise ValueError("early-window ledger date or information cutoff differs")
            if set(record["observations"]) != set(assets) or set(record["covariance"]) != set(assets):
                raise ValueError("early-window observation or covariance coverage differs")
            covariance = independent_covariance(histories[key], record["signal_cutoff_date"],
                                                cfg["feedback_parameters"])
            if any(set(record["covariance"][asset]) != set(assets) or any(
                    abs(record["covariance"][asset][other] - covariance[asset][other]) > 1e-12
                    for other in assets) for asset in assets):
                raise ValueError("early-window covariance differs from prior simulated prices")
            for asset in assets:
                observation = record["observations"][asset]
                check_feedback({"signal_cutoff_date": record["signal_cutoff_date"],
                                "feedback": observation}, histories[key][asset], cfg["feedback_parameters"])
                call = record["portfolio_auction"]["asset_calls"][asset]
                if (observation["text_signal"] != 0 or observation["text_uncertainty"] != 0
                        or observation["text_evidence"] != "text:disabled"
                        or call["execution_available"] != joined[asset][session]["execution_available"]):
                    raise ValueError("early-window no-text observation or suspension status differs")
            if set(record["decisions"]) != set(policy[key]):
                raise ValueError("early-window strategy decision coverage differs")
            for agent, profile in cohorts[key]:
                account = PortfolioAccount(**states[key]["accounts"][agent.name])
                decision = portfolio_decision(agent, profile, account, states[key]["prices"],
                                              record["observations"], record["covariance"], case,
                                              policy[key][agent.name], session, False)
                if record["decisions"][agent.name] != decision:
                    raise ValueError(f"early-window strategy decision differs: {key} session {session} {agent.name}")
            from .portfolio_audit import audit_portfolio_day
            states[key] = audit_portfolio_day(record, states[key], cfg["venue"], session)
            for asset in assets:
                call = record["portfolio_auction"]["asset_calls"][asset]
                histories[key][asset].append({"trade_date": record["trade_date"],
                                              "price_before_minor": call["price_before_minor"],
                                              "price_after_minor": call["price_after_minor"]})
                asset_calls += 1
                traded += int(call["matched_volume"] > 0)
                halted += int(not call["execution_available"])
            for name, account in states[key]["accounts"].items():
                wealth = sum(account["wallets"].values()) + sum(
                    account["shares"][asset] * states[key]["prices"][asset] for asset in assets)
                peaks[key][name] = max(peaks[key][name], wealth)
                drawdowns[key][name] = max(drawdowns[key][name], 1 - wealth / peaks[key][name])
            if session:
                trace_hashes[key].update(b", ")
            trace_hashes[key].update(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                                allow_nan=False).encode("utf-8"))
            counts[key] += 1
            rows += 1
    if (set(counts) != expected or rows != summary["ledger_rows"]
            or rows != summary["audited_portfolio_days"]
            or asset_calls != summary["asset_call_rows"]):
        raise ValueError("early-window ledger coverage or asset-call totals differ")
    for key, path in by_id.items():
        state = states[key]
        trace_hashes[key].update(b"]")
        if (counts[key] != len(joined[path["assets"][0]]) or path["sessions"] != counts[key]
                or path["trace_sha256"] != trace_hashes[key].hexdigest()
                or path["final_prices_minor"] != state["prices"]
                or path["fee_pool_minor"] != state["fee_pool_minor"]):
            raise ValueError("early-window trace or final prices differ")
        for name, account in state["accounts"].items():
            saved = path["accounts"][name]
            final_wealth = sum(account["wallets"].values()) + sum(
                account["shares"][asset] * state["prices"][asset] for asset in path["assets"])
            if (any(saved[field] != account[field] for field in ("wallets", "shares", "sellable"))
                    or saved["initial_wealth_minor"] != initial_wealth[key][name]
                    or saved["final_wealth_minor"] != final_wealth
                    or saved["wealth_multiple"] != final_wealth / initial_wealth[key][name]
                    or saved["max_drawdown"] != drawdowns[key][name]):
                raise ValueError("early-window account or wealth metrics differ")
        for role in ("aggressive", "conservative", "institutional"):
            names = [name for name, info in specs[key].items()
                     if info["kind"] == "strategy" and info["parameters"]["role"] == role]
            final = sum(path["accounts"][name]["final_wealth_minor"] for name in names)
            initial = sum(initial_wealth[key][name] for name in names)
            if path["role_wealth_multiple"][role] != final / initial:
                raise ValueError("early-window role wealth differs")
    result = {"pipeline_version": VERSION, "status": "passed",
              "experiment_id": summary["experiment_id"],
              "market_dataset_id": market["market_dataset_id"], "stocks": len(joined),
              "paths": len(paths), "audited_portfolio_days": rows,
              "audited_asset_calls": asset_calls, "traded_asset_calls": traded,
              "halted_asset_calls": halted, "source_files": len(inputs),
              "checks": {"source_code_artifact_hashes": True, "warm_start_and_full_cohort": True,
                         "no_text_and_as_of_cutoffs": True, "own_price_feedback_and_covariance": True,
                         "decision_reexecution": True, "wallets_shares_and_auction": True,
                         "path_trace_and_summary_metrics": True}}
    return result


def write_audit(directory: Path, config_path: Path, output_dir: Path) -> dict:
    directory, config_path, output_dir = (path.resolve() for path in (directory, config_path, output_dir))
    result = audit_directory(directory, config_path)
    if (output_dir in (directory, config_path) or output_dir in directory.parents
            or directory in output_dir.parents or output_dir in config_path.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("early-window audit output must be a new directory separate from inputs")
    sources = [directory / name for name in (
        "early_window_manifest.json", "early_window_summary.json", "early_window_paths.json",
        "early_window_report.md", LEDGER_NAME)] + [config_path]
    bindings = {str(path): file_sha256(path) for path in sources}
    code = {name: file_sha256(path) for name, path in AUDIT_CODE_PATHS.items()}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        data = stage / "early_window_audit.json"
        data.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = stage / "early_window_audit.md"
        report.write_text(
            "# 武汉早期窗口无文本组合独立审计\n\n"
            f"实验 ID：`{result['experiment_id']}`。重建 {result['stocks']} 家公司、"
            f"{result['paths']} 条路径、{result['audited_portfolio_days']:,} 个组合日和 "
            f"{result['audited_asset_calls']:,} 次资产竞价。\n\n"
            "来源与代码哈希、暖启动时点、无文本观测、内生反馈、Agent 决策、双边成交、"
            "钱包/股份守恒、完整路径和终值摘要均由独立入口重新核对通过。\n",
            encoding="utf-8")
        identity = {"inputs": bindings, "code_sha256": code}
        manifest = {"generated_at": datetime.now(timezone.utc).isoformat(),
                    "experiment_id": result["experiment_id"], **identity,
                    "audit_id": canonical_hash(identity),
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in (data, report)}}
        (stage / "early_window_audit_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def load_audit(directory: Path, audit_dir: Path) -> dict:
    """Load only an audit whose source files, code and report hashes still match."""
    directory, audit_dir = directory.resolve(), audit_dir.resolve()
    manifest = json.loads((audit_dir / "early_window_audit_manifest.json").read_text(encoding="utf-8"))
    result_path = audit_dir / "early_window_audit.json"
    report_path = audit_dir / "early_window_audit.md"
    if (manifest.get("experiment_id") != load_summary(directory)["experiment_id"]
            or manifest.get("code_sha256") != {
                name: file_sha256(path) for name, path in AUDIT_CODE_PATHS.items()}
            or any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items())
            or manifest.get("audit_id") != canonical_hash({
                "inputs": manifest["inputs"], "code_sha256": manifest["code_sha256"]})
            or manifest.get("artifacts") != {
                path.name: {"sha256": file_sha256(path)} for path in (result_path, report_path)}):
        raise ValueError("early-window audit source, code or artifact differs from its manifest")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if (result.get("status") != "passed" or result.get("experiment_id") != manifest["experiment_id"]
            or not result.get("checks") or any(value is not True for value in result["checks"].values())):
        raise ValueError("early-window audit did not pass every check")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = (audit_directory(args.directory, args.config) if args.output_dir is None
              else write_audit(args.directory, args.config, args.output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
