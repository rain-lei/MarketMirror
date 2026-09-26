"""Small synthetic pressure market with a single explicit liquidity counterparty."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agents import AgentParameters, AgentState, Observation, decide, finite_range
from ..data_pipeline.provenance import file_sha256

VERSION = "synthetic-stress-v1"


def validate_config(config: dict[str, Any]) -> tuple[list[AgentParameters], list[dict[str, Any]]]:
    required = {"run_id", "data_kind", "price_start", "liquidity_notional", "impact_coefficient", "max_impact",
                "transaction_cost_rate", "use_text", "agents", "steps"}
    if not isinstance(config, dict) or set(config) != required or not isinstance(config["run_id"], str) or not config["run_id"]:
        raise ValueError("synthetic stress config has missing or unknown fields")
    if config["data_kind"] != "synthetic" or not isinstance(config["use_text"], bool):
        raise ValueError("stress runner accepts explicit synthetic data and boolean text ablation only")
    finite_range(config["price_start"], 1e-8, 1e15, "price_start")
    finite_range(config["liquidity_notional"], 1.0, 1e18, "liquidity_notional")
    finite_range(config["impact_coefficient"], 0.0, 1.0, "impact_coefficient")
    finite_range(config["max_impact"], 0.0, 0.5, "max_impact")
    finite_range(config["transaction_cost_rate"], 0.0, 0.1, "transaction_cost_rate")
    raw_agents, steps = config["agents"], config["steps"]
    if not isinstance(raw_agents, list) or not raw_agents or not isinstance(steps, list) or not steps:
        raise ValueError("agents and steps must be nonempty lists")
    agents = []
    for raw in raw_agents:
        if not isinstance(raw, dict) or set(raw) != set(AgentParameters.__dataclass_fields__):
            raise ValueError("agent parameter fields must be explicit")
        agents.append(AgentParameters(**raw))
    if len({a.name for a in agents}) != len(agents):
        raise ValueError("agent names must be unique")
    if {a.role for a in agents} != {"aggressive", "conservative", "institutional"}:
        raise ValueError("pilot requires one or more of every specified investor role")
    for i, step in enumerate(steps):
        fields = {"market_signal", "text_signal", "uncertainty", "estimated_volatility", "exogenous_return", "evidence_id"}
        if not isinstance(step, dict) or set(step) != fields or not isinstance(step["evidence_id"], str) or not step["evidence_id"]:
            raise ValueError("step fields and evidence_id must be explicit")
        for name in ("market_signal", "text_signal"):
            finite_range(step[name], -1.0, 1.0, name)
        finite_range(step["uncertainty"], 0.0, 1.0, "uncertainty")
        finite_range(step["estimated_volatility"], 1e-6, 1.0, "estimated_volatility")
        finite_range(step["exogenous_return"], -0.5, 0.5, "exogenous_return")
    return agents, steps


def simulate(config: dict[str, Any]) -> dict[str, Any]:
    agents, steps = validate_config(config)
    price = float(config["price_start"])
    initial_cash = sum(a.initial_cash for a in agents)
    states = {a.name: AgentState(float(a.initial_cash)) for a in agents}
    external_cash = 0.0
    external_shares = 0.0
    fee_pool = 0.0
    trace = []
    wealth_peaks = {a.name: a.initial_cash for a in agents}
    max_drawdowns = {a.name: 0.0 for a in agents}
    risk_breach_steps = {a.name: 0 for a in agents}
    closing_weights = {a.name: [] for a in agents}
    for t, step in enumerate(steps):
        observation = Observation(t, price, step["estimated_volatility"], step["market_signal"], step["text_signal"],
                                  step["uncertainty"], f"synthetic-market:{step['evidence_id']}",
                                  f"synthetic-text:{step['evidence_id']}")
        decisions = {p.name: decide(p, states[p.name], observation, config["use_text"]) for p in agents}
        # Cap buys against worst allowed execution price and sells against owned shares before clearing.
        worst_price = price * (1 + 0.5 * config["max_impact"])
        affordable = {}
        for p in agents:
            state = states[p.name]
            requested = decisions[p.name].requested_shares
            affordable[p.name] = (min(requested, state.cash / (worst_price * (1 + config["transaction_cost_rate"])))
                                  if requested > 0 else max(requested, -state.shares))
        gross = sum(abs(q) * price for q in affordable.values())
        fill_fraction = min(1.0, config["liquidity_notional"] / gross) if gross else 1.0
        fills = {name: q * fill_fraction for name, q in affordable.items()}
        net_notional = sum(fills.values()) * price
        impact = max(-config["max_impact"], min(config["max_impact"],
                     config["impact_coefficient"] * net_notional / config["liquidity_notional"]))
        execution_price = price * (1 + impact / 2)
        closing_price = price * (1 + step["exogenous_return"] + impact)
        if closing_price <= 0 or execution_price <= 0:
            raise ValueError("nonpositive simulated price")
        agent_rows = {}
        for p in agents:
            state, decision = states[p.name], decisions[p.name]
            fill = fills[p.name]
            notional = fill * execution_price
            fee = abs(notional) * config["transaction_cost_rate"]
            state.cash -= notional + fee
            state.shares += fill
            if state.cash < -1e-8 or state.shares < -1e-8:
                raise AssertionError("negative agent cash or long-only position")
            state.cash = max(0.0, state.cash)
            state.shares = max(0.0, state.shares)
            state.trades += int(abs(fill) > 1e-12)
            state.cumulative_turnover += abs(notional)
            state.cumulative_fees += fee
            external_cash += notional
            external_shares -= fill
            fee_pool += fee
            wealth = state.wealth(closing_price)
            wealth_peaks[p.name] = max(wealth_peaks[p.name], wealth)
            max_drawdowns[p.name] = max(max_drawdowns[p.name], 1 - wealth / wealth_peaks[p.name])
            closing_weight = state.shares * closing_price / wealth
            risk_excess = max(0.0, closing_weight - decision.risk_weight_cap)
            risk_breach_steps[p.name] += int(risk_excess > 1e-9)
            closing_weights[p.name].append(closing_weight)
            agent_rows[p.name] = {"role": p.role, "decision": asdict(decision), "filled_shares": fill,
                                  "cash": state.cash, "shares": state.shares, "closing_wealth": wealth,
                                  "closing_weight": closing_weight, "fees_paid": fee,
                                  "signal_streak": state.signal_streak,
                                  "closing_risk_cap_excess": risk_excess}
        if not math.isclose(sum(s.cash for s in states.values()) + external_cash + fee_pool, initial_cash,
                            rel_tol=1e-12, abs_tol=1e-6):
            raise AssertionError("cash ledger did not conserve")
        if not math.isclose(sum(s.shares for s in states.values()) + external_shares, 0.0,
                            rel_tol=1e-12, abs_tol=1e-8):
            raise AssertionError("shares ledger did not conserve")
        trace.append({"step": t, "evidence_id": step["evidence_id"], "price_before": price,
                      "exogenous_return": step["exogenous_return"], "net_order_notional_at_open": net_notional,
                      "gross_requested_notional": gross, "liquidity_fill_fraction": fill_fraction,
                      "price_impact": impact, "execution_price": execution_price, "price_after": closing_price,
                      "external_cash": external_cash, "external_shares": external_shares, "fee_pool": fee_pool,
                      "agents": agent_rows})
        price = closing_price
    return {"pipeline_version": VERSION, "data_kind": "synthetic", "run_id": config["run_id"],
            "use_text": config["use_text"], "steps": len(steps), "initial_cash": initial_cash,
            "final_price": price, "trace": trace,
            "summary": {p.name: {"role": p.role, "trades": states[p.name].trades,
                                 "cumulative_turnover": states[p.name].cumulative_turnover,
                                 "cumulative_fees": states[p.name].cumulative_fees,
                                 "final_cash": states[p.name].cash, "final_shares": states[p.name].shares,
                                 "final_wealth": states[p.name].wealth(price),
                                 "max_drawdown": max_drawdowns[p.name],
                                 "risk_budget_breach_steps": risk_breach_steps[p.name],
                                 "mean_closing_weight": sum(closing_weights[p.name]) / len(closing_weights[p.name])}
                        for p in agents},
            "limitations": ["Investor-type parameters are illustrative and have no empirical behavioral calibration.",
                            "Exogenous returns and signals are synthetic; impact and liquidity are assumed, not estimated.",
                            "The external counterparty closes the ledger but has no optimized decision rule.",
                            "This is a mechanism and invariant test, not evidence of actual price formation or predictive ability."]}


def run_stress(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    digest = file_sha256(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    result = simulate(config)
    no_text = simulate({**config, "use_text": False}) if config["use_text"] else None
    if file_sha256(config_path) != digest:
        raise RuntimeError("stress config changed during run")
    source = {"config_sha256": digest,
              "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in ("agents.py", "stress_market.py")}}
    result["experiment_id"] = hashlib.sha256(json.dumps(source, sort_keys=True).encode("utf-8")).hexdigest()
    if output_dir == config_path or config_path in output_dir.parents:
        raise ValueError("output cannot replace input config")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        (staging / "stress_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if no_text is not None:
            (staging / "stress_no_text_results.json").write_text(json.dumps(no_text, ensure_ascii=False, indent=2), encoding="utf-8")
        report = ["# 合成市场 Agent 压力机制", "", "所有信号、收益、交易深度和角色参数均为示意。仅用于验证状态转移、行为差异和账本约束。", "",
                  f"步数：{result['steps']}；启用文本：{result['use_text']}；期末价格：{result['final_price']:.4f}。", "",
                  "| Agent | 角色 | 交易次数 | 累计成交额 | 手续费 | 最大回撤 | 风险超限步数 |", "|---|---|---:|---:|---:|---:|---:|"]
        for name, row in result["summary"].items():
            report.append(f"| {name} | {row['role']} | {row['trades']} | {row['cumulative_turnover']:.2f} | {row['cumulative_fees']:.2f} | {row['max_drawdown'] * 100:.2f}% | {row['risk_budget_breach_steps']} |")
        if no_text is not None:
            report += ["", "## 去掉文本信号的机制消融", "", "两组使用相同合成外生收益、行情信号和参数。只有是否读取合成文本信号不同；内生价格路径可以随订单变化。", "",
                       "| Agent | 启用文本成交额 | 不用文本成交额 |", "|---|---:|---:|"]
            for name, row in result["summary"].items():
                report.append(f"| {name} | {row['cumulative_turnover']:.2f} | {no_text['summary'][name]['cumulative_turnover']:.2f} |")
        report += ["", "每一步的订单、成交、价格冲击、持仓、风险约束原因与外部流动性账户见 `stress_results.json`。", "",
                   "本实验未校准真实投资者行为，也未验证市场预测能力；不能将期末财富解释为策略收益。", ""]
        (staging / "stress_report.md").write_text("\n".join(report), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "experiment_id": result["experiment_id"],
                    "generated_at": datetime.now(timezone.utc).isoformat(), "python_version": platform.python_version(),
                    **source, "data_kind": "synthetic", "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "stress_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_stress(args.config, args.output_dir)
    print(json.dumps({"experiment_id": result["experiment_id"], "steps": result["steps"],
                      "summary": result["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
