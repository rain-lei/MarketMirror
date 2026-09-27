"""Run a source-bound, exploratory semantic ablation on matching observed stocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..baselines.run_experiments import _load_market
from ..data_pipeline.build_dataset import valid_stock_code
from ..data_pipeline.market_data import VERSION as MARKET_VERSION, strict_date
from ..data_pipeline.provenance import file_sha256
from ..semantic.agent_signal_adapter import AI_GATE_PIPELINE, verify_gate, build_signal_rows
from .agents import AgentParameters, finite_range
from .historical_replay import prepare_steps, replay_path
from .semantic_replay import replay_semantic_path
from .semantic_signal_join import join_steps, load_verified_signal_stream


VERSION = "semantic-observed-ablation-v1"
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = {name: RESEARCH_ROOT / name for name in (
    "simulation/semantic_historical_replay.py", "simulation/semantic_replay.py",
    "simulation/semantic_signal_join.py", "simulation/historical_replay.py", "simulation/agents.py",
    "semantic/agent_signal_adapter.py", "semantic/signal_validation.py", "semantic/compare_holdout.py",
    "semantic/assistant_review.py", "baselines/run_experiments.py", "data_pipeline/market_data.py")}
REQUIRED = {"run_id", "data_kind", "market_manifest", "download_manifest", "signal_directory",
            "selection_rule", "stock_codes", "start_date", "end_date", "momentum_sessions", "volatility_sessions",
            "transaction_cost_rate", "agents"}


def _read_config(path: Path) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or set(cfg) != REQUIRED or cfg["data_kind"] != "observed":
        raise ValueError("semantic ablation config must declare the observed input and all parameters")
    if cfg["selection_rule"] not in {"all_signal_stocks", "nonzero_signal_stocks"}:
        raise ValueError("selection_rule must identify the complete or nonzero-signal cohort")
    for key in ("run_id", "market_manifest", "download_manifest", "signal_directory"):
        if not isinstance(cfg[key], str) or not cfg[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    codes = cfg["stock_codes"]
    if (not isinstance(codes, list) or not codes or len(set(codes)) != len(codes)
            or any(valid_stock_code(code) != code for code in codes)):
        raise ValueError("stock_codes must be unique six-digit identifiers")
    if strict_date(cfg["start_date"]) > strict_date(cfg["end_date"]):
        raise ValueError("reversed ablation date range")
    for key in ("momentum_sessions", "volatility_sessions"):
        if type(cfg[key]) is not int or not 2 <= cfg[key] <= 120:
            raise ValueError(f"invalid {key}")
    if cfg["momentum_sessions"] > cfg["volatility_sessions"]:
        raise ValueError("momentum lookback exceeds volatility lookback")
    finite_range(cfg["transaction_cost_rate"], 0.0, 0.1, "transaction_cost_rate")
    raw = cfg["agents"]
    if not isinstance(raw, list) or any(not isinstance(row, dict)
            or set(row) != set(AgentParameters.__dataclass_fields__) for row in raw):
        raise ValueError("all Agent parameters must be explicit")
    agents = [AgentParameters(**row) for row in raw]
    if len({a.name for a in agents}) != len(agents) or {a.role for a in agents} != {
            "aggressive", "conservative", "institutional"}:
        raise ValueError("three unique Agent roles are required")
    return cfg


def _verified_inputs(config_path: Path, cfg: dict[str, Any]) -> tuple[dict, list[dict], dict, dict[str, str]]:
    parent = config_path.parent
    market_path = (parent / cfg["market_manifest"]).resolve()
    download_path = (parent / cfg["download_manifest"]).resolve()
    signal_dir = (parent / cfg["signal_directory"]).resolve()
    market_csv = market_path.parent / "market_daily.csv"
    market = json.loads(market_path.read_text(encoding="utf-8"))
    if (market.get("pipeline_version") != MARKET_VERSION or market.get("data_kind") != "observed"
            or market.get("artifacts", {}).get("market_daily.csv", {}).get("sha256") != file_sha256(market_csv)
            or set(market.get("series", {})) != set(cfg["stock_codes"])):
        raise ValueError("observed market panel does not match selected signal stocks")
    download = json.loads(download_path.read_text(encoding="utf-8"))
    symbols = {f"{'sh' if code[0] == '6' else 'sz'}.{code}" for code in cfg["stock_codes"]}
    if (download.get("provider") != "BaoStock" or set(download.get("source_symbols", [])) != symbols
            or download.get("benchmark_symbol") != market["settings"]["benchmark_id"]
            or download.get("end_date") < cfg["end_date"]):
        raise ValueError("download identity or range differs from the observed market panel")
    if (Path(market.get("config_path", "")).resolve() != (download_path.parent / "market_import.json").resolve()
            or market.get("config_sha256") != file_sha256(download_path.parent / "market_import.json")):
        raise ValueError("prepared market settings do not match the verified download")
    source_paths = [config_path, market_path, market_csv, download_path]
    for name, info in download["artifacts"].items():
        artifact = download_path.parent / name
        if file_sha256(artifact) != info["sha256"]:
            raise ValueError(f"download artifact changed: {name}")
        source_paths.append(artifact)
    for info in market["inputs"].values():
        source = Path(info["path"])
        if source.resolve().parent != download_path.parent or file_sha256(source) != info["sha256"]:
            raise ValueError("prepared market source has changed")
        source_paths.append(source)
    rows, signal_result = load_verified_signal_stream(signal_dir)
    gate = signal_result.get("gate", {})
    if (gate.get("pipeline_version") != AI_GATE_PIPELINE
            or gate.get("review_basis", {}).get("protocol") != "assistant_review_v1"):
        raise ValueError("current AI review gate is required")
    comparisons = [Path(name).parent for name in signal_result.get("input_sha256", {})
                   if Path(name).name == "comparison_manifest.json"]
    if len(comparisons) != 1 or verify_gate(comparisons[0], signal_result["pack_experiment_id"]) != gate:
        raise ValueError("signal stream gate differs from current review and scoring")
    for name, digest in signal_result["input_sha256"].items():
        source = Path(name)
        if file_sha256(source) != digest:
            raise ValueError("Agent signal source has changed")
        source_paths.append(source)
    comparison_manifest = json.loads((comparisons[0] / "comparison_manifest.json").read_text(encoding="utf-8"))
    source_paths.extend(Path(name) for name in comparison_manifest["input_sha256"])
    for name in comparison_manifest["input_sha256"]:
        if Path(name).name == "assistant_review_manifest.json":
            reference = json.loads(Path(name).read_text(encoding="utf-8"))
            source_paths.extend(Path(source) for source in reference["inputs"])
            source_paths.extend(Path(name).parent / artifact for artifact in reference["artifacts"])
    for name in ("agent_signal_manifest.json", "agent_signal_result.json", "agent_signal_rows.jsonl"):
        source_paths.append(signal_dir / name)
    packs = [Path(name).parent for name in signal_result["input_sha256"] if Path(name).name == "annotation_manifest.json"]
    predictions = [Path(name) for name in signal_result["input_sha256"] if Path(name).name == "model_predictions.jsonl"]
    if len(packs) != 1 or len(predictions) != 1 or build_signal_rows(packs[0], predictions[0], comparisons[0])[0] != rows:
        raise ValueError("signal rows differ from the current deterministic adapter mapping")
    selected = ({row["stock_code"] for row in rows} if cfg["selection_rule"] == "all_signal_stocks"
                else {row["stock_code"] for row in rows if row["text_signal"] != 0})
    if selected != set(cfg["stock_codes"]):
        raise ValueError("selected stocks differ from the declared complete cohort")
    if not rows or any(row["stock_code"] not in market["series"] and row["text_signal"] != 0 for row in rows):
        raise ValueError("nonzero text signal lacks matching market data")
    hashes = {str(path): file_sha256(path) for path in source_paths}
    return market, rows, gate, hashes


def run_ablation(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    cfg = _read_config(config_path)
    market, rows, gate, inputs = _verified_inputs(config_path, cfg)
    if any(output_dir == Path(name) or output_dir in Path(name).parents for name in inputs):
        raise ValueError("semantic ablation output must be separate from all input files")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty semantic ablation output directory")
    market_path = (config_path.parent / cfg["market_manifest"]).resolve()
    groups = _load_market(market_path.parent / "market_daily.csv", market)
    agents = [AgentParameters(**row) for row in cfg["agents"]]
    download_path = (config_path.parent / cfg["download_manifest"]).resolve()
    download = json.loads(download_path.read_text(encoding="utf-8"))
    suspended = {row["symbol"].split(".")[1]: set(row["suspended_sessions"])
                 for row in download["quote_checks"] if row["symbol"] != download["benchmark_symbol"]}
    paths, comparisons = {}, []
    for code in cfg["stock_codes"]:
        steps = prepare_steps(groups[code], cfg["start_date"], cfg["end_date"],
                              cfg["momentum_sessions"], cfg["volatility_sessions"])
        # First verify arithmetic parity under the archived unrestricted execution assumption.
        joined = join_steps(steps, code, rows)
        unrestricted_control = replay_semantic_path(joined, agents, cfg["transaction_cost_rate"], use_text=False)
        market_only = replay_path(steps, agents, cfg["transaction_cost_rate"], use_market_signal=True)
        if unrestricted_control["summary"] != market_only["summary"]:
            raise AssertionError("no-text arithmetic differs from existing market-only replay")
        for step in joined:
            step["execution_available"] = step["execution_reference_date"] not in suspended[code]
        active = replay_semantic_path(joined, agents, cfg["transaction_cost_rate"], use_text=True)
        control = replay_semantic_path(joined, agents, cfg["transaction_cost_rate"], use_text=False)
        first_signal = next((step["trade_date"] for step in joined if step["text_signal"] != 0), None)
        first_effect = next((step["trade_date"] for step in joined
                             if step["text_signal"] != 0 or step["text_uncertainty"] != 0), None)
        if first_effect is None and active["summary"] != control["summary"]:
            raise AssertionError("stock without a text event changed Agent outcomes")
        for agent in agents:
            a, b = active["summary"][agent.name], control["summary"][agent.name]
            comparisons.append({"stock_code": code, "role": agent.role,
                                "text_wealth_multiple": a["final_wealth"] / agent.initial_cash,
                                "no_text_wealth_multiple": b["final_wealth"] / agent.initial_cash,
                                "wealth_difference_multiple": (a["final_wealth"] - b["final_wealth"]) / agent.initial_cash,
                                "text_trades": a["trades"], "no_text_trades": b["trades"]})
        paths[code] = {"sessions": len(joined), "first_nonzero_trade_date": first_signal,
                       "blocked_execution_days": sum(not step["execution_available"] for step in joined),
                       "first_text_effect_trade_date": first_effect,
                       "nonzero_trade_days": sum(step["text_signal"] != 0 for step in joined),
                       "uncertainty_only_trade_days": sum(step["text_signal"] == 0 and step["text_uncertainty"] > 0
                                                          for step in joined),
                       "text": active, "no_text": control}
    if any(file_sha256(Path(name)) != digest for name, digest in inputs.items()):
        raise RuntimeError("semantic ablation input changed during calculation")
    codes = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
    identity = {"inputs": inputs, "code_sha256": codes}
    result = {"pipeline_version": VERSION, "run_id": cfg["run_id"], "data_kind": "observed",
              "replay_id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
              "market_dataset_id": market["market_dataset_id"], "review_basis": gate["review_basis"],
              "selection": cfg["selection_rule"],
              "nonzero_signal_stocks": sum(path["first_nonzero_trade_date"] is not None for path in paths.values()),
              "uncertainty_only_stocks": sum(path["first_nonzero_trade_date"] is None
                                             and path["first_text_effect_trade_date"] is not None for path in paths.values()),
              "no_effect_stocks": sum(path["first_text_effect_trade_date"] is None for path in paths.values()),
              "dates": {"start": cfg["start_date"], "end": cfg["end_date"]},
              "information_rule": "trade-date t uses market returns and text available through t-2 session",
              "baseline_parity": "no-text arithmetic equals archived market-only replay before applying shared suspension constraints",
              "comparison_rows": comparisons, "paths": paths}
    categories = {code: ("directional" if path["first_nonzero_trade_date"] else
                         "uncertainty_only" if path["first_text_effect_trade_date"] else "no_effect")
                  for code, path in paths.items()}
    grouped = []
    for category in ("directional", "uncertainty_only", "no_effect"):
        for agent in agents:
            values = [row["wealth_difference_multiple"] for row in comparisons
                      if categories[row["stock_code"]] == category and row["role"] == agent.role]
            if not values:
                continue
            grouped.append({"category": category, "role": agent.role, "stocks": len(values),
                            "mean_difference_multiple": statistics.mean(values),
                            "positive": sum(value > 0 for value in values),
                            "negative": sum(value < 0 for value in values),
                            "minimum": min(values), "maximum": max(values)})
    summary = {"pipeline_version": VERSION, "run_id": cfg["run_id"],
               "replay_id": result["replay_id"], "market_dataset_id": market["market_dataset_id"],
               "stocks": len(paths), "sessions_per_stock": sorted({path["sessions"] for path in paths.values()}),
               "comparison_rows": len(comparisons),
               "directional_stocks": result["nonzero_signal_stocks"],
               "uncertainty_only_stocks": result["uncertainty_only_stocks"],
               "no_effect_stocks": result["no_effect_stocks"],
               "blocked_execution_days": sum(path["blocked_execution_days"] for path in paths.values()),
               "baseline_parity": result["baseline_parity"], "grouped": grouped}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        data = staging / "semantic_ablation.json"
        data.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        summary_path = staging / "semantic_ablation_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ["# 真实收益路径上的语义信号消融", "",
                 f"样本含 {len(paths)} 家公司：{result['nonzero_signal_stocks']} 家有方向信号，"
                 f"{result['uncertainty_only_stocks']} 家只有事件不确定性，"
                 f"{result['no_effect_stocks']} 家没有进入 Agent 的文本作用；所用 2020 年行情为当前下载版本。",
                 "两条路径共用同一真实收益、Agent 参数、交易成本和停牌约束。施加停牌约束前，无文本计算与原市场回放一致。",
                 f"按执行参考日状态阻止 {summary['blocked_execution_days']} 个公司日的成交，手续费同时为零；正常交易日仍假设参考价满额成交。",
                 "决策使用截至交易日 t-2 的行情和已可见文本；并非真实投资者行为、历史价格形成或监管预测的校准。",
                 "", "| 作用类别 | 角色 | 公司数 | 平均末值差/初值 | 差值为正 | 差值为负 |",
                 "|---|---|---:|---:|---:|---:|"]
        for row in grouped:
            lines.append(f"| {row['category']} | {row['role']} | {row['stocks']} | "
                         f"{row['mean_difference_multiple']:+.4f} | {row['positive']} | {row['negative']} |")
        lines += ["", "| 股票 | 角色 | 有文本末值/初值 | 无文本末值/初值 | 差值/初值 | 有文本交易数 | 无文本交易数 |",
                 "|---|---|---:|---:|---:|---:|---:|"]
        for row in comparisons:
            lines.append(f"| {row['stock_code']} | {row['role']} | {row['text_wealth_multiple']:.4f} | "
                         f"{row['no_text_wealth_multiple']:.4f} | {row['wealth_difference_multiple']:+.4f} | "
                         f"{row['text_trades']} | {row['no_text_trades']} |")
        lines += ["", "方向为零但存在事件的记录仍可通过不确定性影响 Agent；无事件记录不施加不确定性惩罚。",
                  "信号只影响示意 Agent 决策，不改变已观察的市场收益。文本事件按当前合并规则持续保留，未估计衰减期限。",
                  ("当前仅选非零信号公司，属于事后选择；不能推广到全部来源公司。" if cfg["selection_rule"] == "nonzero_signal_stocks"
                   else "覆盖固定样本全部公司；样本按话题分层且复核协议已变更，仍属探索性分析。"),
                  "末值差异不是模型预测收益能力的证据。", ""]
        report = staging / "semantic_ablation_report.md"
        report.write_text("\n".join(lines), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "replay_id": result["replay_id"], **identity,
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in (data, summary_path, report)}}
        (staging / "semantic_ablation_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def load_verified_summary(directory: Path) -> dict[str, Any]:
    """Read only a source- and code-verified summary for display or export."""
    manifest = json.loads((directory / "semantic_ablation_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("pipeline_version") != VERSION or manifest.get("code_sha256") != {
            name: file_sha256(path) for name, path in CODE_PATHS.items()}:
        raise ValueError("semantic ablation code differs from the recorded run")
    if set(manifest.get("artifacts", {})) != {
            "semantic_ablation.json", "semantic_ablation_summary.json", "semantic_ablation_report.md"}:
        raise ValueError("semantic ablation artifact set is invalid")
    for name, info in manifest["artifacts"].items():
        if file_sha256(directory / name) != info["sha256"]:
            raise ValueError("semantic ablation artifact changed")
    if not manifest.get("inputs") or any(file_sha256(Path(name)) != digest
                                        for name, digest in manifest["inputs"].items()):
        raise ValueError("semantic ablation source changed")
    summary = json.loads((directory / "semantic_ablation_summary.json").read_text(encoding="utf-8"))
    if summary.get("replay_id") != manifest["replay_id"] or summary.get("pipeline_version") != VERSION:
        raise ValueError("semantic ablation summary identity is invalid")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_ablation(args.config, args.output_dir)
    values = [row["wealth_difference_multiple"] for row in result["comparison_rows"]]
    print(json.dumps({"stocks": len(result["paths"]), "comparisons": len(values),
                      "difference_min": min(values), "difference_max": max(values),
                      "baseline_parity": result["baseline_parity"]}))


if __name__ == "__main__":
    main()
