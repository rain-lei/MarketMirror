"""Run explicitly assumed Agent impact scenarios on archived daily return paths."""

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

from .agents import AgentParameters, finite_range
from .historical_replay import load_config as load_replay_config, prepare_steps
from .stress_market import simulate
from .participation_replay import _read_activity
from ..baselines.run_experiments import _load_market, load_experiment, visibility_anchor
from ..data_pipeline.market_activity import VERSION as ACTIVITY_VERSION
from ..data_pipeline.market_data import VERSION as MARKET_VERSION, strict_date
from ..data_pipeline.provenance import file_sha256


VERSION = "observed-return-assumed-impact-v1"


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "data_kind", "replay_config", "event_config", "activity_manifest",
                "event_id", "stock_code",
                "aum_cny_per_agent", "liquidity_notional_cny", "impact_coefficients", "max_impact",
                "scenario_signal", "scenario_uncertainty", "signal_sessions"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("counterfactual config has missing or unknown fields")
    if config["data_kind"] != "observed_return_counterfactual":
        raise ValueError("counterfactual requires an explicit observed-return scenario kind")
    for field in ("run_id", "replay_config", "event_config", "activity_manifest", "event_id", "stock_code"):
        if not isinstance(config[field], str) or not config[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    finite_range(config["aum_cny_per_agent"], 1.0, 1e13, "aum_cny_per_agent")
    finite_range(config["liquidity_notional_cny"], 1.0, 1e18, "liquidity_notional_cny")
    finite_range(config["max_impact"], 0.0, 0.5, "max_impact")
    finite_range(config["scenario_signal"], -1.0, 1.0, "scenario_signal")
    finite_range(config["scenario_uncertainty"], 0.0, 1.0, "scenario_uncertainty")
    if config["scenario_signal"] == 0 and config["scenario_uncertainty"] == 0:
        raise ValueError("scenario must change signal or uncertainty")
    if type(config["signal_sessions"]) is not int or not 1 <= config["signal_sessions"] <= 30:
        raise ValueError("signal_sessions must be an integer between 1 and 30")
    coefficients = config["impact_coefficients"]
    if (not isinstance(coefficients, list) or not coefficients or coefficients[0] != 0
            or coefficients != sorted(coefficients) or len(coefficients) != len(set(coefficients))):
        raise ValueError("impact coefficients must be unique, increasing and start at zero")
    for coefficient in coefficients:
        finite_range(coefficient, 0.0, 1.0, "impact_coefficient")
    return config


def scenario_steps(steps: list[dict[str, Any]], available_on_date: str,
                   signal: float, uncertainty: float, signal_sessions: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """An event is first used when the full signal cutoff reaches its visibility date."""
    strict_date(available_on_date)
    if not steps:
        raise ValueError("scenario needs return-path steps")
    first = next((index for index, row in enumerate(steps)
                  if row["signal_cutoff_date"] >= available_on_date), None)
    if first is None or first + signal_sessions > len(steps):
        raise ValueError("event has no complete post-visibility scenario window")
    result = []
    for index, row in enumerate(steps):
        if not row["signal_cutoff_date"] < row["execution_reference_date"] < row["trade_date"]:
            raise ValueError("return-path information order is invalid")
        active = first <= index < first + signal_sessions
        result.append({"market_signal": row["market_signal"],
                       "text_signal": signal if active else 0.0,
                       "uncertainty": uncertainty if active else 0.0,
                       "estimated_volatility": row["estimated_volatility"],
                       "exogenous_return": row["observed_return"],
                       "evidence_id": row["trade_date"]})
    return result, {"first_signal_trade_date": steps[first]["trade_date"],
                    "first_signal_cutoff_date": steps[first]["signal_cutoff_date"],
                    "last_signal_trade_date": steps[first + signal_sessions - 1]["trade_date"]}


def compare_scenarios(steps: list[dict[str, Any]], agents: list[AgentParameters], fee_rate: float,
                      liquidity_notional: float, max_impact: float, coefficients: list[float],
                      signal: float, uncertainty: float, signal_sessions: int,
                      available_on_date: str) -> dict[str, Any]:
    scenario, timing = scenario_steps(steps, available_on_date, signal, uncertainty, signal_sessions)
    control = [{**row, "text_signal": 0.0, "uncertainty": 0.0} for row in scenario]
    paths = []
    for coefficient in coefficients:
        for enabled, path_steps in ((False, control), (True, scenario)):
            engine_config = {"run_id": "counterfactual-internal", "data_kind": "synthetic",
                             "price_start": 100.0, "liquidity_notional": liquidity_notional,
                             "impact_coefficient": coefficient, "max_impact": max_impact,
                             "transaction_cost_rate": fee_rate, "use_text": True,
                             "agents": [asdict(agent) for agent in agents], "steps": path_steps}
            output = simulate(engine_config)
            trace = [{**point, "trade_date": step["trade_date"],
                      "signal_cutoff_date": step["signal_cutoff_date"],
                      "execution_reference_date": step["execution_reference_date"],
                      "scenario_signal": path_steps[index]["text_signal"],
                      "scenario_uncertainty": path_steps[index]["uncertainty"]}
                     for index, (point, step) in enumerate(zip(output["trace"], steps, strict=True))]
            paths.append({"impact_coefficient": coefficient,
                          "scenario_enabled": enabled, "final_price_index": output["final_price"],
                          "summary": output["summary"], "trace": trace})
    active_indices = [index for index, row in enumerate(scenario)
                      if row["text_signal"] or row["uncertainty"]]
    paired = []
    for index, coefficient in enumerate(coefficients):
        baseline, shock = paths[2 * index:2 * index + 2]
        paired.append({"impact_coefficient": coefficient,
                       "event_window_net_order_delta_cny": sum(
                           shock["trace"][day]["net_order_notional_at_open"]
                           - baseline["trace"][day]["net_order_notional_at_open"]
                           for day in active_indices),
                       "event_end_price_delta": shock["trace"][active_indices[-1]]["price_after"]
                                                - baseline["trace"][active_indices[-1]]["price_after"],
                       "terminal_price_delta": shock["final_price_index"] - baseline["final_price_index"]})
    return {"timing": timing, "paths": paths, "paired_effects": paired,
            "observed_return_only_price_index": math.prod(1 + step["observed_return"] for step in steps) * 100}


def render_report(result: dict[str, Any]) -> str:
    timing = result["timing"]
    lines = ["# 已观察收益路径上的假设价格冲击情景", "",
             f"事件：`{result['event_id']}`；股票：`{result['stock_code']}`；回放：{result['start_date']} 至 {result['end_date']}。",
             f"事件日 {result['event_date']}；公开时间规则 `{result['visibility_rule']}`；"
             f"第一个可用信号截止日为 {timing['first_signal_cutoff_date']}；"
             f"首次受情景信号影响的收益日为 {timing['first_signal_trade_date']}，最后一次为 {timing['last_signal_trade_date']}。", "",
             "使用存档的逐日实际收益作为外生路径；三类 Agent 的订单、固定假设流动性与冲击系数可以改变模拟价格。"
             "事件方向、强度、不确定性、资金规模和冲击参数均为手设情景，既非 LLM 输出，也未经真实订单校准。", "",
             f"每类 Agent 每股假设资金 {result['aum_cny_per_agent']:,.0f} 元；固定假设流动性 "
             f"{result['liquidity_notional_cny']:,.0f} 元；事件信号 {result['scenario_signal']:+.2f}；"
             f"不确定性 {result['scenario_uncertainty']:.2f}；持续 {result['signal_sessions']} 个信号截止日。", "",
             "| 冲击系数 | 事件信号 | 期末模拟价格指数 | 累计冲击绝对值 | 激进型末值/初值 | 保守型末值/初值 | 机构型末值/初值 |",
             "|---:|---|---:|---:|---:|---:|---:|"]
    for path in result["paths"]:
        by_role = {row["role"]: row for row in path["summary"].values()}
        lines.append(f"| {path['impact_coefficient']:.3f} | {'有' if path['scenario_enabled'] else '无'} | "
                     f"{path['final_price_index']:.3f} | "
                     f"{sum(abs(row['price_impact']) for row in path['trace']):.3f} | "
                     f"{by_role['aggressive']['final_wealth']/result['aum_cny_per_agent']:.3f} | "
                     f"{by_role['conservative']['final_wealth']/result['aum_cny_per_agent']:.3f} | "
                     f"{by_role['institutional']['final_wealth']/result['aum_cny_per_agent']:.3f} |")
    lines += ["", "同一冲击系数下，相对于无事件信号的配对差异：", "",
              "| 冲击系数 | 情景期间净订单差（元） | 情景末日价格指数差 | 全期末价格指数差 |",
              "|---:|---:|---:|---:|"]
    for pair in result["paired_effects"]:
        lines.append(f"| {pair['impact_coefficient']:.3f} | "
                     f"{pair['event_window_net_order_delta_cny']:,.0f} | "
                     f"{pair['event_end_price_delta']:+.4f} | "
                     f"{pair['terminal_price_delta']:+.4f} |")
    lines += ["", f"纯观察收益连乘的期末归一化价格指数：{result['observed_return_only_price_index']:.3f}。"
              "冲击系数为零的两条路径应有相同模拟价格，且与这个指数一致；非零差异是模型假设造成的反事实差异，不是历史价格拟合误差。", "",
              "实际收益已经包含真实市场的全部交易作用，再叠加假设冲击会重复计入部分市场运动；只能用于参数敏感性，"
              "不能将模拟价格当作另一条可预测历史。归一化价格指数不是实际成交价。此引擎还假设固定流动性、"
              "单一外部对手方、无真实盘口与订单队列；实际成交价和冲击参数均未观测。"
              "2018/2020 事件附近还可能有预期和同期冲击。这里不能证明真实投资者行为、政策因果效果、预测或监管预警。", ""]
    return "\n".join(lines)


def run_counterfactual(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config = load_config(config_path)
    replay_path = (config_path.parent / config["replay_config"]).resolve()
    event_path = (config_path.parent / config["event_config"]).resolve()
    replay_config = load_replay_config(replay_path)
    event_config = load_experiment(event_path)
    event = next((item for item in event_config.get("events", []) if item.get("event_id") == config["event_id"]), None)
    if (event is None or event_config.get("data_kind") != "observed"
            or config["stock_code"] not in replay_config["stock_codes"]
            or config["stock_code"] not in event_config["stock_codes"]):
        raise ValueError("event or stock is absent from the selected observed inputs")
    market_path = (replay_path.parent / replay_config["market_manifest"]).resolve()
    event_market_path = (event_path.parent / event_config["market_manifest"]).resolve()
    if market_path != event_market_path:
        raise ValueError("event and replay must use the same market manifest")
    csv_path = market_path.parent / "market_daily.csv"
    activity_manifest_path = (config_path.parent / config["activity_manifest"]).resolve()
    activity_path = activity_manifest_path.parent / "market_activity.csv"
    evidence_manifest_path = (event_path.parent / event_config["evidence_manifest"]).resolve()
    evidence_manifest = json.loads(evidence_manifest_path.read_text(encoding="utf-8"))
    matching_evidence = [source for source in evidence_manifest["sources"]
                         if source["url"] == event["evidence_source"]]
    if len(matching_evidence) != 1:
        raise ValueError("event source is not uniquely archived")
    evidence_path = (evidence_manifest_path.parent / matching_evidence[0]["path"]).resolve()
    if evidence_path.parent != evidence_manifest_path.parent:
        raise ValueError("archived evidence must remain in its evidence directory")
    inputs = {path: file_sha256(path) for path in (config_path, replay_path, event_path, market_path,
                                                  csv_path, activity_manifest_path, activity_path,
                                                  evidence_manifest_path, evidence_path)}
    if inputs[evidence_path] != matching_evidence[0]["sha256"]:
        raise ValueError("event evidence differs from its archive manifest")
    market_manifest = json.loads(market_path.read_text(encoding="utf-8"))
    activity_manifest = json.loads(activity_manifest_path.read_text(encoding="utf-8"))
    if (market_manifest.get("pipeline_version") != MARKET_VERSION
            or market_manifest.get("data_kind") != "observed"
            or market_manifest["artifacts"][csv_path.name]["sha256"] != inputs[csv_path]):
        raise ValueError("observed market CSV differs from its manifest")
    if (activity_manifest.get("pipeline_version") != ACTIVITY_VERSION
            or activity_manifest.get("market_dataset_id") != market_manifest["market_dataset_id"]
            or activity_manifest["artifacts"][activity_path.name]["sha256"] != inputs[activity_path]):
        raise ValueError("observed activity differs from its manifest or market dataset")
    groups = _load_market(csv_path, market_manifest)
    steps = prepare_steps(groups[config["stock_code"]], replay_config["start_date"], replay_config["end_date"],
                          replay_config["momentum_sessions"], replay_config["volatility_sessions"])
    activity = _read_activity(activity_path)
    if any(activity.get((config["stock_code"], step["execution_reference_date"]), {}).get("trading_status")
           != "trading" for step in steps):
        raise ValueError("fixed-liquidity engine cannot execute on a missing or suspended reference day")
    agents = [replace(AgentParameters(**raw), initial_cash=float(config["aum_cny_per_agent"]))
              for raw in replay_config["agents"]]
    available_on, visibility_rule = visibility_anchor(event)
    comparison = compare_scenarios(steps, agents, replay_config["transaction_cost_rate"],
                                   config["liquidity_notional_cny"], config["max_impact"],
                                   config["impact_coefficients"], config["scenario_signal"],
                                   config["scenario_uncertainty"], config["signal_sessions"],
                                   available_on.isoformat())
    if any(file_sha256(path) != digest for path, digest in inputs.items()):
        raise RuntimeError("counterfactual inputs changed during calculation")
    if any(output_dir == path or output_dir in path.parents for path in inputs):
        raise ValueError("output must not contain source files")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty output directory")
    code_hashes = {name: file_sha256(path) for name, path in {
        "observed_counterfactual.py": Path(__file__),
        "stress_market.py": Path(__file__).with_name("stress_market.py"),
        "agents.py": Path(__file__).with_name("agents.py"),
        "historical_replay.py": Path(__file__).with_name("historical_replay.py"),
        "participation_replay.py": Path(__file__).with_name("participation_replay.py"),
        "run_experiments.py": Path(__file__).parents[1] / "baselines" / "run_experiments.py",
        "market_data.py": Path(__file__).parents[1] / "data_pipeline" / "market_data.py",
        "market_activity.py": Path(__file__).parents[1] / "data_pipeline" / "market_activity.py"}.items()}
    identity = {"inputs": {str(path): digest for path, digest in inputs.items()}, "code_sha256": code_hashes}
    result = {"pipeline_version": VERSION, "data_kind": config["data_kind"],
              "run_id": config["run_id"], "scenario_id": hashlib.sha256(
                  json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest(),
              "market_dataset_id": market_manifest["market_dataset_id"], "event_id": config["event_id"],
              "event_date": event["event_date"], "stock_code": config["stock_code"],
              "visibility_rule": visibility_rule, "available_on_date": available_on.isoformat(),
              "activity_id": activity_manifest["activity_id"],
              "start_date": replay_config["start_date"], "end_date": replay_config["end_date"],
              "aum_cny_per_agent": config["aum_cny_per_agent"],
              "liquidity_notional_cny": config["liquidity_notional_cny"],
              "max_impact": config["max_impact"],
              "transaction_cost_rate": replay_config["transaction_cost_rate"],
              "agents": {agent.name: asdict(agent) for agent in agents},
              "scenario_signal": config["scenario_signal"],
              "scenario_uncertainty": config["scenario_uncertainty"],
              "signal_sessions": config["signal_sessions"], **comparison}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        (staging / "counterfactual_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "counterfactual_report.md").write_text(render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "scenario_id": result["scenario_id"],
                    "generated_at": datetime.now(timezone.utc).isoformat(), **identity,
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()}}
        (staging / "counterfactual_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_counterfactual(args.config, args.output_dir)
    print(json.dumps({"scenario_id": result["scenario_id"], "event_id": result["event_id"],
                      "first_signal_trade_date": result["timing"]["first_signal_trade_date"],
                      "paths": len(result["paths"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
