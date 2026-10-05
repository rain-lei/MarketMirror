"""Sensitivity paths with t-2 turnover-based capacity and assumed price impact."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agents import AgentParameters, AgentState, Observation, decide, finite_range
from .historical_replay import load_config as load_replay_config, prepare_steps
from .observed_counterfactual import scenario_steps
from .participation_replay import _read_activity
from ..baselines.run_experiments import _load_market, load_experiment, visibility_anchor
from ..data_pipeline.market_activity import VERSION as ACTIVITY_VERSION
from ..data_pipeline.market_data import VERSION as MARKET_VERSION
from ..data_pipeline.provenance import file_sha256


VERSION = "lagged-turnover-assumed-impact-v1"


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "data_kind", "replay_config", "event_config", "activity_manifest",
                "event_id", "stock_code", "aum_cny_per_agent", "participation_rates",
                "impact_depth_fractions", "impact_coefficients", "max_impact",
                "scenario_signal", "scenario_uncertainty", "signal_sessions"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("lagged impact config has missing or unknown fields")
    if config["data_kind"] != "observed_return_sensitivity":
        raise ValueError("lagged impact requires an explicit observed-return sensitivity kind")
    for key in ("run_id", "replay_config", "event_config", "activity_manifest", "event_id", "stock_code"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    finite_range(config["aum_cny_per_agent"], 1.0, 1e13, "aum_cny_per_agent")
    finite_range(config["max_impact"], 0.0, 0.5, "max_impact")
    finite_range(config["scenario_signal"], -1.0, 1.0, "scenario_signal")
    finite_range(config["scenario_uncertainty"], 0.0, 1.0, "scenario_uncertainty")
    if config["scenario_signal"] == config["scenario_uncertainty"] == 0:
        raise ValueError("scenario must change signal or uncertainty")
    if type(config["signal_sessions"]) is not int or not 1 <= config["signal_sessions"] <= 30:
        raise ValueError("signal_sessions must be an integer between 1 and 30")
    for key, lower, upper in (("participation_rates", 0.0, 1.0),
                              ("impact_depth_fractions", 0.0, 1.0),
                              ("impact_coefficients", -1.0, 1.0)):
        values = config[key]
        if (not isinstance(values, list) or not values or values != sorted(values)
                or len(values) != len(set(values))):
            raise ValueError(f"{key} must be a unique increasing list")
        for value in values:
            finite_range(value, lower, upper, key)
        if key != "impact_coefficients" and values[0] <= 0:
            raise ValueError(f"{key} must be positive")
        if key == "impact_coefficients" and values[0] != 0:
            raise ValueError("impact coefficients must start at zero")
    return config


def simulate_path(steps: list[dict[str, Any]], signals: list[dict[str, Any]],
                  agents: list[AgentParameters], activity: dict[tuple[str, str], dict[str, Any]],
                  stock_code: str, fee_rate: float, participation_rate: float,
                  impact_depth_fraction: float, impact_coefficient: float,
                  max_impact: float) -> dict[str, Any]:
    """At t-1 reference close, use only the t-2 amount for cap and impact scale."""
    if not steps or len(steps) != len(signals) or not agents or len({a.name for a in agents}) != len(agents):
        raise ValueError("paths require aligned steps, signals and unique agents")
    for name, value, lower, upper in (("fee_rate", fee_rate, 0.0, 0.1),
                                      ("participation_rate", participation_rate, 1e-12, 1.0),
                                      ("impact_depth_fraction", impact_depth_fraction, 1e-12, 1.0),
                                      ("impact_coefficient", impact_coefficient, 0.0, 1.0),
                                      ("max_impact", max_impact, 0.0, 0.5)):
        finite_range(value, lower, upper, name)
    price = 100.0
    states = {agent.name: AgentState(float(agent.initial_cash)) for agent in agents}
    peaks = {agent.name: float(agent.initial_cash) for agent in agents}
    drawdowns = {agent.name: 0.0 for agent in agents}
    risk_breaches = {agent.name: 0 for agent in agents}
    external_cash = external_shares = fee_pool = 0.0
    initial_cash = sum(agent.initial_cash for agent in agents)
    trace = []
    for index, (step, signal) in enumerate(zip(steps, signals, strict=True)):
        cutoff_date = step["signal_cutoff_date"]
        reference_date = step["execution_reference_date"]
        if not cutoff_date < reference_date < step["trade_date"]:
            raise ValueError("capacity information or execution reference is not strictly prior to return date")
        cutoff = activity.get((stock_code, cutoff_date))
        reference = activity.get((stock_code, reference_date))
        if cutoff is None or reference is None:
            raise ValueError("lagged impact lacks cutoff or reference activity")
        amount = cutoff["amount_cny"]
        if (not isinstance(amount, (int, float)) or isinstance(amount, bool)
                or not math.isfinite(amount) or amount < 0
                or cutoff["trading_status"] not in {"trading", "suspended"}
                or reference["trading_status"] not in {"trading", "suspended"}
                or (cutoff["trading_status"] == "suspended" and amount != 0)):
            raise ValueError("activity amount or trading status is invalid")
        if any(signal[key] != step[key] for key in ("market_signal", "estimated_volatility")):
            raise ValueError("signal and return steps are misaligned")
        if signal["evidence_id"] != step["trade_date"] or signal["exogenous_return"] != step["observed_return"]:
            raise ValueError("signal and return dates are misaligned")
        finite_range(step["observed_return"], -0.5, 0.5, "observed_return")
        observation = Observation(index, price, step["estimated_volatility"], signal["market_signal"],
                                  signal["text_signal"], signal["uncertainty"],
                                  f"benchmark-through:{cutoff_date}", f"scenario-through:{cutoff_date}")
        decisions = {agent.name: decide(agent, states[agent.name], observation) for agent in agents}
        worst_price = price * (1 + 0.5 * max_impact)
        affordable = {}
        for agent in agents:
            state = states[agent.name]
            requested = decisions[agent.name].requested_shares
            affordable[agent.name] = (min(requested, state.cash / (worst_price * (1 + fee_rate)))
                                      if requested > 0 else max(requested, -state.shares))
        gross_requested = sum(abs(quantity) * price for quantity in affordable.values())
        planning_cap = participation_rate * amount
        executable_cap = 0.0 if reference["trading_status"] == "suspended" else planning_cap
        fill_fraction = min(1.0, executable_cap / gross_requested) if gross_requested else 1.0
        fills = {name: quantity * fill_fraction for name, quantity in affordable.items()}
        gross_filled = sum(abs(quantity) * price for quantity in fills.values())
        if gross_filled > executable_cap + max(1e-5, executable_cap * 1e-12):
            raise AssertionError("fills exceeded the lagged planning cap")
        net_notional = sum(fills.values()) * price
        assumed_impact_depth = impact_depth_fraction * amount
        raw_impact = (impact_coefficient * net_notional / assumed_impact_depth
                      if assumed_impact_depth > 0 else 0.0)
        impact = max(-max_impact, min(max_impact, raw_impact))
        execution_price = price * (1 + impact / 2)
        closing_price = price * (1 + step["observed_return"] + impact)
        if not math.isfinite(closing_price) or closing_price <= 0:
            raise ValueError("observed return and assumed impact produced an invalid price")
        agent_rows = {}
        for agent in agents:
            state = states[agent.name]
            fill = fills[agent.name]
            notional = fill * execution_price
            fee = abs(notional) * fee_rate
            state.cash -= notional + fee
            state.shares += fill
            if state.cash < -1e-5 or state.shares < -1e-8:
                raise AssertionError("lagged impact violated cash or long-only position")
            state.cash = max(0.0, state.cash)
            state.shares = max(0.0, state.shares)
            state.trades += int(abs(fill) > 1e-12)
            state.cumulative_turnover += abs(notional)
            state.cumulative_fees += fee
            external_cash += notional
            external_shares -= fill
            fee_pool += fee
            wealth = state.wealth(closing_price)
            peaks[agent.name] = max(peaks[agent.name], wealth)
            drawdowns[agent.name] = max(drawdowns[agent.name], 1 - wealth / peaks[agent.name])
            closing_weight = state.shares * closing_price / wealth
            risk_excess = max(0.0, closing_weight - decisions[agent.name].risk_weight_cap)
            risk_breaches[agent.name] += int(risk_excess > 1e-9)
            agent_rows[agent.name] = {"role": agent.role, "decision": asdict(decisions[agent.name]),
                                      "requested_shares_after_affordability": affordable[agent.name],
                                      "filled_shares": fill, "cash": state.cash, "shares": state.shares,
                                      "fees_paid": fee, "closing_wealth": wealth,
                                      "closing_weight": closing_weight,
                                      "closing_risk_cap_excess": risk_excess}
        if not math.isclose(sum(state.cash for state in states.values()) + external_cash + fee_pool,
                            initial_cash, rel_tol=1e-12, abs_tol=1e-5):
            raise AssertionError("lagged impact cash ledger did not conserve")
        if not math.isclose(sum(state.shares for state in states.values()) + external_shares,
                            0.0, rel_tol=1e-12, abs_tol=1e-7):
            raise AssertionError("lagged impact shares ledger did not conserve")
        trace.append({"step": index, "trade_date": step["trade_date"],
                      "signal_cutoff_date": cutoff_date, "execution_reference_date": reference_date,
                      "scenario_signal": signal["text_signal"], "scenario_uncertainty": signal["uncertainty"],
                      "cutoff_observed_amount_cny": amount,
                      "reference_trading_status": reference["trading_status"],
                      "planning_cap_cny": planning_cap,
                      "assumed_impact_depth_cny": assumed_impact_depth,
                      "gross_requested_cny": gross_requested, "gross_filled_cny": gross_filled,
                      "fill_fraction": fill_fraction, "net_order_notional_at_open": net_notional,
                      "price_before": price, "observed_return": step["observed_return"],
                      "price_impact": impact, "execution_price": execution_price,
                      "price_after": closing_price, "external_cash": external_cash,
                      "external_shares": external_shares, "fee_pool": fee_pool, "agents": agent_rows})
        price = closing_price
    return {"final_price_index": price, "trace": trace,
            "binding_days": sum(row["gross_requested_cny"] > 0 and row["fill_fraction"] < 1 - 1e-12
                                for row in trace),
            "gross_requested_cny": sum(row["gross_requested_cny"] for row in trace),
            "gross_filled_cny": sum(row["gross_filled_cny"] for row in trace),
            "summary": {agent.name: {"role": agent.role,
                                     "final_wealth": states[agent.name].wealth(price),
                                     "trades": states[agent.name].trades,
                                     "cumulative_turnover": states[agent.name].cumulative_turnover,
                                     "cumulative_fees": states[agent.name].cumulative_fees,
                                     "max_drawdown": drawdowns[agent.name],
                                     "risk_budget_breach_steps": risk_breaches[agent.name]}
                        for agent in agents}}


def compare_scenarios(steps: list[dict[str, Any]], agents: list[AgentParameters],
                      activity: dict[tuple[str, str], dict[str, Any]], stock_code: str,
                      fee_rate: float, participation_rates: list[float],
                      impact_depth_fractions: list[float], impact_coefficients: list[float],
                      max_impact: float, signal: float, uncertainty: float,
                      signal_sessions: int, available_on_date: str) -> dict[str, Any]:
    event_steps, timing = scenario_steps(steps, available_on_date, signal, uncertainty, signal_sessions)
    control_steps = [{**row, "text_signal": 0.0, "uncertainty": 0.0} for row in event_steps]
    active = [index for index, row in enumerate(event_steps)
              if row["text_signal"] or row["uncertainty"]]
    paths = []
    paired = []
    for rate in participation_rates:
        for depth in impact_depth_fractions:
            for coefficient in impact_coefficients:
                pair = []
                for enabled, signals in ((False, control_steps), (True, event_steps)):
                    path = simulate_path(steps, signals, agents, activity, stock_code, fee_rate,
                                         rate, depth, coefficient, max_impact)
                    path.update({"participation_rate": rate, "impact_depth_fraction": depth,
                                 "impact_coefficient": coefficient, "scenario_enabled": enabled})
                    paths.append(path)
                    pair.append(path)
                baseline, event = pair
                paired.append({"participation_rate": rate, "impact_depth_fraction": depth,
                               "impact_coefficient": coefficient,
                               "event_window_net_order_delta_cny": sum(
                                   event["trace"][index]["net_order_notional_at_open"]
                                   - baseline["trace"][index]["net_order_notional_at_open"]
                                   for index in active),
                               "event_end_price_delta": event["trace"][active[-1]]["price_after"]
                                                        - baseline["trace"][active[-1]]["price_after"],
                               "terminal_price_delta": event["final_price_index"]
                                                       - baseline["final_price_index"],
                               "control_binding_days": baseline["binding_days"],
                               "event_binding_days": event["binding_days"]})
    observed = 100 * math.prod(1 + step["observed_return"] for step in steps)
    for path in paths:
        if path["impact_coefficient"] == 0 and not math.isclose(
                path["final_price_index"], observed, rel_tol=1e-12, abs_tol=1e-10):
            raise AssertionError("zero-impact path must reproduce observed return chaining")
    return {"timing": timing, "observed_return_only_price_index": observed,
            "paths": paths, "paired_effects": paired}


def render_report(result: dict[str, Any]) -> str:
    timing = result["timing"]
    lines = ["# 滞后成交额容量与假设冲击敏感性", "",
             f"事件 `{result['event_id']}`，股票 `{result['stock_code']}`，"
             f"回放 {result['start_date']} 至 {result['end_date']}。",
             f"首次可用信号截止日 {timing['first_signal_cutoff_date']}；"
             f"首次受情景信号影响的收益日 {timing['first_signal_trade_date']}。", "",
             "每天的成交容量预算与价格冲击分母分别等于信号截止日（t-2）的实际总成交额乘以两个不同的**假设比例**。"
             "在 t-1 参考收盘价附近分配成交，随后叠加 t 日已观察收益和假设冲击。"
             "若 t-1 参考日停牌则当步不成交。成交额是双向历史总额，绝非可执行盘口深度。", "",
             f"每类 Agent 假设资金 {result['aum_cny_per_agent']:,.0f} 元；"
             f"信号 {result['scenario_signal']:+.2f}，不确定性 {result['scenario_uncertainty']:.2f}，"
             f"持续 {result['signal_sessions']} 日。以下事件信号均为手设，不是 LLM 输出。", "",
             "| 容量比例 | 冲击分母比例 | 冲击系数 | 事件期净订单差（元） | 情景末日价格差 | 期末价格差 | 容量触发日（无/有信号） |",
             "|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["paired_effects"]:
        lines.append(f"| {row['participation_rate']:.0%} | {row['impact_depth_fraction']:.0%} | "
                     f"{row['impact_coefficient']:.3f} | {row['event_window_net_order_delta_cny']:,.0f} | "
                     f"{row['event_end_price_delta']:+.4f} | {row['terminal_price_delta']:+.4f} | "
                     f"{row['control_binding_days']}/{row['event_binding_days']} |")
    lines += ["", f"纯已观察收益连乘期末指数：{result['observed_return_only_price_index']:.4f}；"
              "所有零冲击系数路径均按程序断言与此一致。", "",
              "容量和冲击分母的比例、冲击系数、信号、Agent 资金与行为参数都未经真实订单校准。"
              "已实现收益本身包含真实交易作用，再叠加模型冲击可能重复计数；"
              "归一化参考价格、外部对手方和线性冲击也只是机制假设。"
              "结果只能比较模型对假设的敏感性，不能证明事件因果、历史拟合、预测或监管预警。", ""]
    return "\r\n".join(lines)


def run_lagged_impact(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config = load_config(config_path)
    replay_path = (config_path.parent / config["replay_config"]).resolve()
    event_path = (config_path.parent / config["event_config"]).resolve()
    activity_manifest_path = (config_path.parent / config["activity_manifest"]).resolve()
    replay_config = load_replay_config(replay_path)
    event_config = load_experiment(event_path)
    event = next((row for row in event_config["events"] if row["event_id"] == config["event_id"]), None)
    if (event is None or event_config["data_kind"] != "observed"
            or config["stock_code"] not in replay_config["stock_codes"]
            or config["stock_code"] not in event_config["stock_codes"]):
        raise ValueError("event or stock is absent from the selected observed inputs")
    market_manifest_path = (replay_path.parent / replay_config["market_manifest"]).resolve()
    if market_manifest_path != (event_path.parent / event_config["market_manifest"]).resolve():
        raise ValueError("event and replay must use the same market manifest")
    market_path = market_manifest_path.parent / "market_daily.csv"
    activity_path = activity_manifest_path.parent / "market_activity.csv"
    evidence_manifest_path = (event_path.parent / event_config["evidence_manifest"]).resolve()
    evidence_manifest = json.loads(evidence_manifest_path.read_text(encoding="utf-8"))
    matching = [source for source in evidence_manifest["sources"] if source["url"] == event["evidence_source"]]
    if len(matching) != 1:
        raise ValueError("event evidence is not uniquely archived")
    evidence_path = (evidence_manifest_path.parent / matching[0]["path"]).resolve()
    if evidence_path.parent != evidence_manifest_path.parent:
        raise ValueError("event evidence must remain in its archive directory")
    inputs = {path: file_sha256(path) for path in
              (config_path, replay_path, event_path, activity_manifest_path, activity_path,
               market_manifest_path, market_path, evidence_manifest_path, evidence_path)}
    if inputs[evidence_path] != matching[0]["sha256"]:
        raise ValueError("event evidence differs from its archive manifest")
    market_manifest = json.loads(market_manifest_path.read_text(encoding="utf-8"))
    activity_manifest = json.loads(activity_manifest_path.read_text(encoding="utf-8"))
    if (market_manifest.get("pipeline_version") != MARKET_VERSION
            or market_manifest.get("data_kind") != "observed"
            or market_manifest["artifacts"][market_path.name]["sha256"] != inputs[market_path]
            or activity_manifest.get("pipeline_version") != ACTIVITY_VERSION
            or activity_manifest.get("market_dataset_id") != market_manifest["market_dataset_id"]
            or activity_manifest.get("data_kind") != "observed"
            or activity_manifest["artifacts"][activity_path.name]["sha256"] != inputs[activity_path]):
        raise ValueError("observed market or activity differs from its manifest")
    groups = _load_market(market_path, market_manifest)
    steps = prepare_steps(groups[config["stock_code"]], replay_config["start_date"],
                          replay_config["end_date"], replay_config["momentum_sessions"],
                          replay_config["volatility_sessions"])
    activity = _read_activity(activity_path)
    agents = [replace(AgentParameters(**raw), initial_cash=float(config["aum_cny_per_agent"]))
              for raw in replay_config["agents"]]
    available_on, visibility_rule = visibility_anchor(event)
    comparison = compare_scenarios(steps, agents, activity, config["stock_code"],
                                   replay_config["transaction_cost_rate"],
                                   config["participation_rates"], config["impact_depth_fractions"],
                                   config["impact_coefficients"], config["max_impact"],
                                   config["scenario_signal"], config["scenario_uncertainty"],
                                   config["signal_sessions"], available_on.isoformat())
    if any(file_sha256(path) != digest for path, digest in inputs.items()):
        raise RuntimeError("lagged impact inputs changed during simulation")
    if any(output_dir == path or output_dir in path.parents for path in inputs):
        raise ValueError("output must not contain source files")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty lagged impact output directory")
    code_hashes = {name: file_sha256(path) for name, path in {
        "lagged_impact.py": Path(__file__),
        "observed_counterfactual.py": Path(__file__).with_name("observed_counterfactual.py"),
        "participation_replay.py": Path(__file__).with_name("participation_replay.py"),
        "historical_replay.py": Path(__file__).with_name("historical_replay.py"),
        "agents.py": Path(__file__).with_name("agents.py"),
        "run_experiments.py": Path(__file__).parents[1] / "baselines/run_experiments.py",
        "market_data.py": Path(__file__).parents[1] / "data_pipeline/market_data.py",
        "market_activity.py": Path(__file__).parents[1] / "data_pipeline/market_activity.py"}.items()}
    identity = {"inputs": {str(path): digest for path, digest in inputs.items()},
                "code_sha256": code_hashes}
    result = {"pipeline_version": VERSION, "data_kind": config["data_kind"],
              "run_id": config["run_id"],
              "scenario_id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest(),
              "market_dataset_id": market_manifest["market_dataset_id"],
              "activity_id": activity_manifest["activity_id"],
              "event_id": config["event_id"], "event_date": event["event_date"],
              "stock_code": config["stock_code"], "visibility_rule": visibility_rule,
              "available_on_date": available_on.isoformat(),
              "start_date": replay_config["start_date"], "end_date": replay_config["end_date"],
              "aum_cny_per_agent": config["aum_cny_per_agent"],
              "participation_rates": config["participation_rates"],
              "impact_depth_fractions": config["impact_depth_fractions"],
              "impact_coefficients": config["impact_coefficients"],
              "max_impact": config["max_impact"],
              "transaction_cost_rate": replay_config["transaction_cost_rate"],
              "agents": {agent.name: asdict(agent) for agent in agents},
              "scenario_signal": config["scenario_signal"],
              "scenario_uncertainty": config["scenario_uncertainty"],
              "signal_sessions": config["signal_sessions"], **comparison}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        (staging / "lagged_impact_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\r\n", encoding="utf-8")
        (staging / "lagged_impact_report.md").write_text(render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "scenario_id": result["scenario_id"], **identity,
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()}}
        (staging / "lagged_impact_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_lagged_impact(args.config, args.output_dir)
    print(json.dumps({"scenario_id": result["scenario_id"], "event_id": result["event_id"],
                      "paths": len(result["paths"]),
                      "first_signal_trade_date": result["timing"]["first_signal_trade_date"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
