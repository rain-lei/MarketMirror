"""Replay illustrative long-only agents on observed returns without price impact."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agents import AgentParameters, AgentState, Observation, decide, finite_range
from ..baselines.event_study import DailyObservation
from ..baselines.run_experiments import _load_market
from ..data_pipeline.build_dataset import valid_stock_code
from ..data_pipeline.market_data import VERSION as MARKET_VERSION, strict_date
from ..data_pipeline.provenance import file_sha256

VERSION = "historical-price-taking-replay-v1"


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "data_kind", "market_manifest", "stock_codes", "start_date", "end_date",
                "momentum_sessions", "volatility_sessions", "transaction_cost_rate", "agents"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("historical replay config has missing or unknown fields")
    if (not isinstance(config["run_id"], str) or not config["run_id"].strip()
            or not isinstance(config["market_manifest"], str) or not config["market_manifest"].strip()
            or config["data_kind"] != "observed"):
        raise ValueError("replay needs an observed market manifest and run identifier")
    codes = config["stock_codes"]
    if (not isinstance(codes, list) or not codes
            or any(not isinstance(c, str) or valid_stock_code(c) != c for c in codes)
            or len(set(codes)) != len(codes)):
        raise ValueError("replay stock_codes must be unique six-digit identifiers")
    if strict_date(config["start_date"]) > strict_date(config["end_date"]):
        raise ValueError("replay date range is reversed")
    for name in ("momentum_sessions", "volatility_sessions"):
        if type(config[name]) is not int or not 2 <= config[name] <= 120:
            raise ValueError(f"{name} must be an integer between 2 and 120")
    if config["momentum_sessions"] > config["volatility_sessions"]:
        raise ValueError("momentum lookback cannot exceed volatility lookback")
    finite_range(config["transaction_cost_rate"], 0.0, 0.1, "transaction_cost_rate")
    raw = config["agents"]
    if not isinstance(raw, list) or not raw or any(not isinstance(a, dict) or set(a) != set(AgentParameters.__dataclass_fields__) for a in raw):
        raise ValueError("replay agents must declare all parameter fields")
    agents = [AgentParameters(**a) for a in raw]
    if len({a.name for a in agents}) != len(agents) or {a.role for a in agents} != {"aggressive", "conservative", "institutional"}:
        raise ValueError("replay requires unique agents covering all three roles")
    return config


def prepare_steps(rows: list[DailyObservation], start_date: str, end_date: str,
                  momentum_sessions: int, volatility_sessions: int) -> list[dict[str, Any]]:
    """At trade date t use only returns through t-2; execute at t-1 reference close."""
    ordered = sorted(rows, key=lambda row: row.trade_date)
    if not ordered or len({row.trade_date for row in ordered}) != len(ordered):
        raise ValueError("replay requires unique ordered market sessions")
    if any(not all(math.isfinite(v) and v > -1 for v in (r.stock_return, r.market_return)) for r in ordered):
        raise ValueError("replay needs finite simple returns strictly above -100 percent")
    steps = []
    for i, row in enumerate(ordered):
        day = row.trade_date.isoformat()
        if not start_date <= day <= end_date:
            continue
        if i < volatility_sessions + 1:
            raise ValueError("replay start lacks strictly lagged volatility history")
        stock_history = [r.stock_return for r in ordered[i - 1 - volatility_sessions:i - 1]]
        market_history = [r.market_return for r in ordered[i - 1 - volatility_sessions:i - 1]]
        stock_vol = max(1e-6, min(1.0, statistics.stdev(stock_history)))
        market_vol = max(1e-6, statistics.stdev(market_history))
        momentum = sum(market_history[-momentum_sessions:])
        signal = math.tanh(momentum / (market_vol * math.sqrt(momentum_sessions)))
        steps.append({"trade_date": day, "signal_cutoff_date": ordered[i - 2].trade_date.isoformat(),
                      "execution_reference_date": ordered[i - 1].trade_date.isoformat(),
                      "observed_return": row.stock_return, "market_signal": signal,
                      "estimated_volatility": stock_vol})
    if not steps:
        raise ValueError("replay date range has no market sessions")
    return steps


def replay_path(steps: list[dict[str, Any]], agents: list[AgentParameters], fee_rate: float,
                use_market_signal: bool) -> dict[str, Any]:
    if not steps or not agents:
        raise ValueError("replay requires steps and agents")
    price = 100.0  # Return index; the source supplies returns, not a verified continuous price.
    states = {a.name: AgentState(float(a.initial_cash)) for a in agents}
    peaks = {a.name: a.initial_cash for a in agents}
    drawdowns = {a.name: 0.0 for a in agents}
    risk_breach_days = {a.name: 0 for a in agents}
    external_cash, external_shares, fee_pool = 0.0, 0.0, 0.0
    initial_cash = sum(a.initial_cash for a in agents)
    trace = []
    for t, step in enumerate(steps):
        next_price = price * (1 + step["observed_return"])
        if not math.isfinite(next_price) or next_price <= 0:
            raise ValueError("observed return path produced an invalid price index")
        agent_rows = {}
        for agent in agents:
            state = states[agent.name]
            observation = Observation(t, price, step["estimated_volatility"],
                                      step["market_signal"] if use_market_signal else 0.0,
                                      0.0, 0.0,
                                      f"benchmark-through:{step['signal_cutoff_date']}", "text:none-unvalidated")
            decision = decide(agent, state, observation, use_text=False)
            requested = decision.requested_shares
            fill = min(requested, state.cash / (price * (1 + fee_rate))) if requested > 0 else max(requested, -state.shares)
            notional = fill * price
            fee = abs(notional) * fee_rate
            state.cash -= notional + fee
            state.shares += fill
            if state.cash < -1e-8 or state.shares < -1e-8:
                raise AssertionError("historical replay violated cash or long-only position")
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
            weight = state.shares * next_price / wealth
            risk_breach_days[agent.name] += int(weight > decision.risk_weight_cap + 1e-9)
            agent_rows[agent.name] = {"role": agent.role, "decision": asdict(decision),
                                      "filled_shares": fill, "fees_paid": fee,
                                      "cash": state.cash, "shares": state.shares,
                                      "closing_wealth": wealth, "closing_weight": weight}
        if not math.isclose(sum(s.cash for s in states.values()) + external_cash + fee_pool, initial_cash,
                            rel_tol=1e-12, abs_tol=1e-6):
            raise AssertionError("historical replay cash ledger did not conserve")
        if not math.isclose(sum(s.shares for s in states.values()) + external_shares, 0.0,
                            rel_tol=1e-12, abs_tol=1e-8):
            raise AssertionError("historical replay shares ledger did not conserve")
        trace.append({**step, "step": t, "price_index_before": price, "price_index_after": next_price,
                      "external_cash": external_cash, "external_shares": external_shares,
                      "fee_pool": fee_pool, "agents": agent_rows})
        price = next_price
    buy_hold_wealth = {a.name: a.initial_cash * price / (100.0 * (1 + fee_rate)) for a in agents}
    return {"signal_enabled": use_market_signal, "price_index_start": 100.0, "price_index_end": price,
            "trace": trace, "summary": {a.name: {"role": a.role, "final_wealth": states[a.name].wealth(price),
                                          "return": states[a.name].wealth(price) / a.initial_cash - 1,
                                          "cash_reference_wealth": a.initial_cash,
                                          "buy_hold_reference_wealth": buy_hold_wealth[a.name],
                                          "trades": states[a.name].trades,
                                          "cumulative_turnover": states[a.name].cumulative_turnover,
                                          "cumulative_fees": states[a.name].cumulative_fees,
                                          "max_drawdown": drawdowns[a.name],
                                          "risk_budget_breach_days": risk_breach_days[a.name]}
                                  for a in agents}}


def render_report(result: dict[str, Any]) -> str:
    lines = ["# 历史收益路径上的 Agent 规则回放", "", f"观察期：{result['start_date']} 至 {result['end_date']}。",
             "决策信号只使用执行参考收盘日前一交易日及更早的宽基收益；执行参考价是前一日收盘的归一化收益指数；其后才应用当日已实现收益。", "",
             "| 股票 | 角色 | 市场信号末值/初值 | 零信号末值/初值 | 持有基准末值/初值 | 市场信号最大回撤 | 市场信号交易次数 |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for code, paths in result["paths"].items():
        active, control = paths["market_signal"], paths["zero_signal"]
        for name, row in active["summary"].items():
            other = control["summary"][name]
            start = result["agents"][name]["initial_cash"]
            lines.append(f"| {code} | {row['role']} | {row['final_wealth']/start:.3f} | {other['final_wealth']/start:.3f} | {row['buy_hold_reference_wealth']/start:.3f} | {row['max_drawdown']:.1%} | {row['trades']} |")
    lines += ["", "所有策略参数均是示意值，未用持仓或订单数据校准；比较不构成策略表现优劣或交易建议。买入持有基准在首日按同一费率投入全部初始现金；现金基准始终为 1.000。", "",
              "价格路径只来自已存档的逐日收益，归一化指数不是交易所实际成交价。交易假设在参考收盘价全部成交并支付固定费率；没有盘口、净订单流、滑点或市场冲击，Agent 行为不会改变历史价格。回复、LLM 与财务字段均未进入信号。", "",
              "这个回放检验时序、账户约束和路径敏感性，不是已校准的历史市场仿真，也不能证明文本或 Agent 的预测能力。", ""]
    return "\n".join(lines)


def run_replay(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config = load_config(config_path)
    manifest_path = (config_path.parent / config["market_manifest"]).resolve()
    csv_path = manifest_path.parent / "market_daily.csv"
    inputs = {p: file_sha256(p) for p in (config_path, manifest_path, csv_path)}
    market_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (market_manifest["pipeline_version"] != MARKET_VERSION or market_manifest["data_kind"] != "observed"
            or market_manifest["artifacts"]["market_daily.csv"]["sha256"] != inputs[csv_path]):
        raise ValueError("replay market kind, version or CSV hash differs from manifest")
    groups = _load_market(csv_path, market_manifest)
    if not set(config["stock_codes"]) <= set(groups):
        raise ValueError("replay stock is absent from the observed market panel")
    agents = [AgentParameters(**a) for a in config["agents"]]
    paths = {}
    for code in config["stock_codes"]:
        steps = prepare_steps(groups[code], config["start_date"], config["end_date"],
                              config["momentum_sessions"], config["volatility_sessions"])
        paths[code] = {"market_signal": replay_path(steps, agents, config["transaction_cost_rate"], True),
                       "zero_signal": replay_path(steps, agents, config["transaction_cost_rate"], False)}
    if any(file_sha256(p) != h for p, h in inputs.items()):
        raise RuntimeError("replay inputs changed during calculation")
    if any(output_dir == p or output_dir in p.parents for p in inputs):
        raise ValueError("replay output must not contain input files")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty replay output directory")
    code_hashes = {name: file_sha256(Path(__file__).with_name(name)) for name in ("historical_replay.py", "agents.py")}
    code_hashes["run_experiments.py"] = file_sha256(Path(__file__).parents[1] / "baselines" / "run_experiments.py")
    identity = {"inputs": {str(p): h for p, h in inputs.items()}, "code_sha256": code_hashes}
    result = {"pipeline_version": VERSION, "run_id": config["run_id"], "data_kind": "observed",
              "replay_id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest(),
              "market_dataset_id": market_manifest["market_dataset_id"],
              "start_date": config["start_date"], "end_date": config["end_date"],
              "information_lag_sessions": 1,
              "signal_rule": "tanh(sum(last momentum benchmark returns through t-2) / (stdev(last volatility benchmark returns through t-2) * sqrt(momentum_sessions)))",
              "agents": {a.name: asdict(a) for a in agents}, "paths": paths}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        (staging / "historical_replay.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "historical_replay_report.md").write_text(render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "replay_id": result["replay_id"],
                    "generated_at": datetime.now(timezone.utc).isoformat(), **identity,
                    "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "historical_replay_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_replay(args.config, args.output_dir)
    print(json.dumps({"replay_id": result["replay_id"],
                      "stocks": {code: len(paths["market_signal"]["trace"]) for code, paths in result["paths"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
