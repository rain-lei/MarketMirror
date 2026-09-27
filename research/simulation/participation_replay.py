"""Replay illustrative agents with a capacity cap known before the reference close."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import tempfile
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agents import AgentParameters, AgentState, Observation, decide
from .historical_replay import (VERSION as LEGACY_VERSION, load_config as load_replay_config,
                                prepare_steps)
from ..baselines.run_experiments import _load_market
from ..data_pipeline.market_activity import VERSION as ACTIVITY_VERSION
from ..data_pipeline.market_data import VERSION as MARKET_VERSION
from ..data_pipeline.provenance import file_sha256


VERSION = "historical-participation-replay-v1"


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "data_kind", "replay_config", "replay_manifest",
                "activity_manifest", "aum_cny_per_agent", "participation_rates"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("participation replay config has missing or unknown fields")
    if config["data_kind"] != "observed_sensitivity" or not isinstance(config["run_id"], str) or not config["run_id"]:
        raise ValueError("participation replay requires an observed sensitivity run")
    for name in ("replay_config", "replay_manifest", "activity_manifest"):
        if not isinstance(config[name], str) or not config[name]:
            raise ValueError(f"{name} must point to an input")
    for key, upper in (("aum_cny_per_agent", 1e13), ("participation_rates", 1.0)):
        values = config[key]
        if (not isinstance(values, list) or not values or values != sorted(values)
                or len(values) != len(set(values))
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       or not 0 < value <= upper for value in values)):
            raise ValueError(f"{key} must be a unique increasing list of positive finite values")
    return config


def _read_activity(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != {"trade_date", "stock_code", "provider_symbol",
                                            "volume_shares", "amount_cny", "trading_status"}:
            raise ValueError("activity CSV has an unexpected schema")
        for row in reader:
            key = (row["stock_code"], row["trade_date"])
            if key in result or row["trading_status"] not in {"trading", "suspended"}:
                raise ValueError("activity has duplicate dates or invalid status")
            amount = float(row["amount_cny"])
            if (not math.isfinite(amount) or amount < 0
                    or (row["trading_status"] == "trading" and amount <= 0)
                    or (row["trading_status"] == "suspended" and amount != 0)):
                raise ValueError("activity amount contradicts trading status")
            result[key] = {"amount_cny": amount, "trading_status": row["trading_status"]}
    return result


def simulate_path(steps: list[dict[str, Any]], agents: list[AgentParameters],
                  activity: dict[tuple[str, str], dict[str, Any]], stock_code: str,
                  fee_rate: float, use_market_signal: bool,
                  participation_rate: float | None) -> dict[str, Any]:
    """Use turnover through t-2 to bound gross fills at the t-1 reference close."""
    if not steps or not agents:
        raise ValueError("replay needs steps and agents")
    if participation_rate is not None and (not math.isfinite(participation_rate)
                                           or not 0 < participation_rate <= 1):
        raise ValueError("participation rate must be in (0, 1]")
    price = 100.0
    states = {agent.name: AgentState(float(agent.initial_cash)) for agent in agents}
    peaks = {agent.name: float(agent.initial_cash) for agent in agents}
    drawdowns = {agent.name: 0.0 for agent in agents}
    external_cash = external_shares = fee_pool = 0.0
    initial_cash = sum(agent.initial_cash for agent in agents)
    trace = []
    for index, step in enumerate(steps):
        cutoff_date, reference_date = step["signal_cutoff_date"], step["execution_reference_date"]
        if not cutoff_date < reference_date < step["trade_date"]:
            raise ValueError("capacity information or execution reference is not strictly prior to return date")
        cutoff = activity.get((stock_code, cutoff_date))
        reference = activity.get((stock_code, reference_date))
        if cutoff is None or reference is None:
            raise ValueError("replay lacks observed activity for a cutoff or execution reference date")
        observation = Observation(index, price, step["estimated_volatility"],
                                  step["market_signal"] if use_market_signal else 0.0,
                                  0.0, 0.0, f"benchmark-through:{cutoff_date}", "text:none-unvalidated")
        decisions = {agent.name: decide(agent, states[agent.name], observation, use_text=False)
                     for agent in agents}
        affordable = {}
        for agent in agents:
            state = states[agent.name]
            requested = decisions[agent.name].requested_shares
            affordable[agent.name] = (min(requested, state.cash / (price * (1 + fee_rate)))
                                      if requested > 0 else max(requested, -state.shares))
        gross_requested = sum(abs(fill) * price for fill in affordable.values())
        planning_cap = (None if participation_rate is None else
                        participation_rate * cutoff["amount_cny"])
        # A suspended reference date cannot support an execution, regardless of past activity.
        executable_cap = (math.inf if planning_cap is None else planning_cap)
        if reference["trading_status"] == "suspended":
            executable_cap = 0.0
        fill_fraction = min(1.0, executable_cap / gross_requested) if gross_requested else 1.0
        next_price = price * (1 + step["observed_return"])
        if not math.isfinite(next_price) or next_price <= 0:
            raise ValueError("observed return produced an invalid normalized price")
        agent_rows = {}
        for agent in agents:
            state = states[agent.name]
            fill = affordable[agent.name] * fill_fraction
            notional = fill * price
            fee = abs(notional) * fee_rate
            state.cash -= notional + fee
            state.shares += fill
            if state.cash < -1e-8 or state.shares < -1e-8:
                raise AssertionError("capacity replay violated cash or long-only position")
            state.cash = max(0.0, state.cash)
            state.shares = max(0.0, state.shares)
            state.trades += int(abs(fill) > 1e-12)
            state.cumulative_turnover += abs(notional)
            state.cumulative_fees += fee
            external_cash += notional
            external_shares -= fill
            fee_pool += fee
            wealth = state.wealth(next_price)
            peaks[agent.name] = max(peaks[agent.name], wealth)
            drawdowns[agent.name] = max(drawdowns[agent.name], 1 - wealth / peaks[agent.name])
            agent_rows[agent.name] = {"role": agent.role, "decision": asdict(decisions[agent.name]),
                                      "requested_shares_after_affordability": affordable[agent.name],
                                      "filled_shares": fill, "fees_paid": fee,
                                      "cash": state.cash, "shares": state.shares,
                                      "closing_wealth": wealth,
                                      "closing_weight": state.shares * next_price / wealth}
        if not math.isclose(sum(state.cash for state in states.values()) + external_cash + fee_pool,
                            initial_cash, rel_tol=1e-12, abs_tol=1e-5):
            raise AssertionError("capacity replay cash ledger did not conserve")
        if not math.isclose(sum(state.shares for state in states.values()) + external_shares,
                            0.0, rel_tol=1e-12, abs_tol=1e-7):
            raise AssertionError("capacity replay shares ledger did not conserve")
        trace.append({"step": index, "trade_date": step["trade_date"],
                      "signal_cutoff_date": cutoff_date,
                      "execution_reference_date": reference_date,
                      "cutoff_observed_amount_cny": cutoff["amount_cny"],
                      "reference_trading_status": reference["trading_status"],
                      "participation_rate": participation_rate,
                      "planning_cap_cny": planning_cap,
                      "gross_requested_cny": gross_requested,
                      "gross_filled_cny": gross_requested * fill_fraction,
                      "fill_fraction": fill_fraction,
                      "price_index_before": price, "observed_return": step["observed_return"],
                      "price_index_after": next_price,
                      "external_cash": external_cash, "external_shares": external_shares,
                      "fee_pool": fee_pool, "agents": agent_rows})
        price = next_price
    gross_requested_total = sum(row["gross_requested_cny"] for row in trace)
    gross_filled_total = sum(row["gross_filled_cny"] for row in trace)
    return {"stock_code": stock_code, "use_market_signal": use_market_signal,
            "participation_rate": participation_rate, "aum_cny_per_agent": agents[0].initial_cash,
            "sessions": len(trace), "gross_requested_cny": gross_requested_total,
            "gross_filled_cny": gross_filled_total,
            "aggregate_fill_rate": gross_filled_total / gross_requested_total if gross_requested_total else 1.0,
            "binding_days": sum(row["gross_requested_cny"] > 0 and row["fill_fraction"] < 1 - 1e-12
                                for row in trace),
            "suspended_reference_days": sum(row["reference_trading_status"] == "suspended" for row in trace),
            "trace": trace,
            "summary": {agent.name: {"role": agent.role,
                                      "final_wealth": states[agent.name].wealth(price),
                                      "return": states[agent.name].wealth(price) / agent.initial_cash - 1,
                                      "trades": states[agent.name].trades,
                                      "cumulative_turnover": states[agent.name].cumulative_turnover,
                                      "cumulative_fees": states[agent.name].cumulative_fees,
                                      "max_drawdown": drawdowns[agent.name]}
                        for agent in agents}}


def _render_report(result: dict[str, Any]) -> str:
    lines = ["# 已知成交额约束下的 Agent 历史收益路径", "",
             "每个交易日的容量预算只来自信号截止日（执行参考日前一交易日）的实际总成交额，乘以假设参与上限。各 Agent 按绝对订单额同比例成交，未完成部分保留在原状态并在以后重新决策。", "",
             "| 股票 | 信号 | 每类 Agent 每股假设资金 | 截止日成交额比例上限 | 容量触发日 | 总成交比例 | 激进型末值/初值 | 保守型末值/初值 | 机构型末值/初值 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["scenarios"]:
        summary = row["summary"]
        role_returns = {value["role"]: value["return"] for value in summary.values()}
        rate_text = "无限制" if row["participation_rate"] is None else f"{row['participation_rate']:.0%}"
        lines.append(f"| {row['stock_code']} | {'市场' if row['use_market_signal'] else '零'} | "
                     f"{row['aum_cny_per_agent']:,.0f} | "
                     f"{rate_text} | "
                     f"{row['binding_days']}/{row['sessions']} | {row['aggregate_fill_rate']:.1%} | "
                     f"{1 + role_returns['aggressive']:.3f} | {1 + role_returns['conservative']:.3f} | "
                     f"{1 + role_returns['institutional']:.3f} |")
    lines += ["", "同日实际成交额只在独立的事后容量诊断中使用；本回放下单时没有读取当日或执行参考日的成交额。", "",
              "参与上限是情景假设，不是从盘口拟合出的可执行容量；价格仍沿用真实已实现收益且不会随订单改变。参考收盘指数不是实际成交价，未建模价差、队列、滑点或市场冲击。角色参数亦未用真实投资者持仓/订单校准。", ""]
    return "\n".join(lines)


def run_participation_replay(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config = load_config(config_path)
    replay_config_path = (config_path.parent / config["replay_config"]).resolve()
    replay_manifest_path = (config_path.parent / config["replay_manifest"]).resolve()
    activity_manifest_path = (config_path.parent / config["activity_manifest"]).resolve()
    replay_config = load_replay_config(replay_config_path)
    market_manifest_path = (replay_config_path.parent / replay_config["market_manifest"]).resolve()
    market_path = market_manifest_path.parent / "market_daily.csv"
    legacy_path = replay_manifest_path.parent / "historical_replay.json"
    activity_path = activity_manifest_path.parent / "market_activity.csv"
    inputs = {path: file_sha256(path) for path in
              (config_path, replay_config_path, replay_manifest_path, legacy_path,
               activity_manifest_path, activity_path, market_manifest_path, market_path)}
    legacy_manifest = json.loads(replay_manifest_path.read_text(encoding="utf-8"))
    activity_manifest = json.loads(activity_manifest_path.read_text(encoding="utf-8"))
    market_manifest = json.loads(market_manifest_path.read_text(encoding="utf-8"))
    if (legacy_manifest["pipeline_version"] != LEGACY_VERSION
            or activity_manifest["pipeline_version"] != ACTIVITY_VERSION
            or market_manifest["pipeline_version"] != MARKET_VERSION
            or inputs[legacy_path] != legacy_manifest["artifacts"][legacy_path.name]["sha256"]
            or inputs[activity_path] != activity_manifest["artifacts"][activity_path.name]["sha256"]
            or inputs[market_path] != market_manifest["artifacts"][market_path.name]["sha256"]
            or legacy_manifest["inputs"][str(replay_config_path)] != inputs[replay_config_path]
            or legacy_manifest["inputs"][str(market_manifest_path)] != inputs[market_manifest_path]
            or legacy_manifest["inputs"][str(market_path)] != inputs[market_path]):
        raise ValueError("source replay, market or activity differs from its declared manifest")
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    if (legacy["market_dataset_id"] != market_manifest["market_dataset_id"]
            or legacy["market_dataset_id"] != activity_manifest["market_dataset_id"]
            or legacy["replay_id"] != legacy_manifest["replay_id"]
            or legacy["data_kind"] != "observed"
            or activity_manifest["data_kind"] != "observed"):
        raise ValueError("all sources must describe the same observed market dataset")
    groups = _load_market(market_path, market_manifest)
    activity = _read_activity(activity_path)
    if not set(replay_config["stock_codes"]) <= set(groups):
        raise ValueError("replay stock is missing from market data")
    scenarios = []
    for code in replay_config["stock_codes"]:
        steps = prepare_steps(groups[code], replay_config["start_date"], replay_config["end_date"],
                              replay_config["momentum_sessions"], replay_config["volatility_sessions"])
        for aum in config["aum_cny_per_agent"]:
            agents = [replace(AgentParameters(**raw), initial_cash=float(aum)) for raw in replay_config["agents"]]
            for signal in (True, False):
                control = simulate_path(steps, agents, activity, code,
                                        replay_config["transaction_cost_rate"], signal, None)
                legacy_path_name = "market_signal" if signal else "zero_signal"
                old = legacy["paths"][code][legacy_path_name]["summary"]
                for agent in agents:
                    old_ratio = old[agent.name]["final_wealth"] / legacy["agents"][agent.name]["initial_cash"]
                    new_ratio = control["summary"][agent.name]["final_wealth"] / agent.initial_cash
                    if not math.isclose(old_ratio, new_ratio, rel_tol=1e-9, abs_tol=1e-9):
                        raise ValueError("uncapped control no longer matches the fixed historical replay")
                scenarios.append(control)
                for rate in config["participation_rates"]:
                    scenarios.append(simulate_path(steps, agents, activity, code,
                                                   replay_config["transaction_cost_rate"], signal, rate))
    if any(file_sha256(path) != digest for path, digest in inputs.items()):
        raise RuntimeError("participation replay input changed during simulation")
    if any(output_dir == path or output_dir in path.parents or path.parent in output_dir.parents
           for path in inputs):
        raise ValueError("participation replay output must be separate from inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty participation replay output directory")
    code_hashes = {"participation_replay.py": file_sha256(Path(__file__)),
                   "historical_replay.py": file_sha256(Path(__file__).with_name("historical_replay.py")),
                   "agents.py": file_sha256(Path(__file__).with_name("agents.py")),
                   "market_activity.py": file_sha256(Path(__file__).parents[1] / "data_pipeline/market_activity.py"),
                   "run_experiments.py": file_sha256(Path(__file__).parents[1] / "baselines/run_experiments.py")}
    identity = {"input_sha256": {str(path): digest for path, digest in inputs.items()},
                "code_sha256": code_hashes}
    result = {"pipeline_version": VERSION, "run_id": config["run_id"],
              "data_kind": "observed_sensitivity", "market_dataset_id": legacy["market_dataset_id"],
              "start_date": replay_config["start_date"], "end_date": replay_config["end_date"],
              "information_rule": "signals and capacity use sessions through t-2; reference execution at t-1 close; realized return at t",
              "capacity_rule": "pro-rata gross-order cap = assumed participation rate times observed t-2 traded amount; no fills if t-1 reference session is suspended",
              "replay_id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest(),
              "scenarios": scenarios,
              "limitations": ["AUM and participation rates are sensitivity assumptions, not investor or liquidity estimates.",
                              "Observed t-2 total turnover is only a planning proxy, not executable order-book depth.",
                              "Reference-close fills and fees omit spread, queue, slippage and endogenous price impact.",
                              "Agent roles and parameters are illustrative and have no observed investor-type calibration."]}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        (staging / "participation_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / "participation_report.md").write_text(_render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "replay_id": result["replay_id"], **identity,
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()}}
        (staging / "participation_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_participation_replay(args.config, args.output_dir)
    print(json.dumps({"replay_id": result["replay_id"], "scenarios": len(result["scenarios"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
