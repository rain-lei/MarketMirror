"""Test text memory and availability assumptions on a fixed observed replay cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import tempfile
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .historical_replay import prepare_steps
from .semantic_historical_replay import (
    CODE_PATHS as BASE_CODE_PATHS, _read_config, _verified_inputs, load_verified_summary,
)
from .semantic_replay import replay_semantic_path
from .semantic_signal_join import join_steps

VERSION = "semantic-memory-sensitivity-v1"
CODE_PATHS = {**BASE_CODE_PATHS, "simulation/semantic_memory_sensitivity.py": Path(__file__)}
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
ARTIFACTS = {"semantic_memory_results.json", "semantic_memory_summary.json", "semantic_memory_report.md"}


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_scenario(value: Any) -> dict[str, Any]:
    required = {"scenario_id", "memory_mode", "memory_sessions", "lag_days"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("memory scenario requires explicit mode, sessions, delay and identity")
    if not isinstance(value["scenario_id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value["scenario_id"]):
        raise ValueError("invalid scenario_id")
    if type(value["lag_days"]) is not int or not 0 <= value["lag_days"] <= 30:
        raise ValueError("lag_days must be an integer between 0 and 30")
    mode, sessions = value["memory_mode"], value["memory_sessions"]
    if mode == "cumulative":
        if sessions is not None:
            raise ValueError("cumulative memory must declare null sessions")
    elif mode in {"rolling", "exponential"}:
        if type(sessions) is not int or not 1 <= sessions <= 120:
            raise ValueError("memory_sessions must be an integer between 1 and 120")
    else:
        raise ValueError("unknown memory_mode")
    return dict(value)


def local_visible_date(value: str) -> date:
    """Daily information cutoffs use China time; naive source times assume that zone."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("availability time must be an ISO date or timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("availability time must be an ISO date or timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
    return parsed.astimezone(LOCAL_TIMEZONE).date()


def memory_join(steps: list[dict], stock_code: str, rows: list[dict],
                session_dates: list[str], scenario: dict) -> list[dict]:
    """Only already visible records enter a daily cutoff; age counts open sessions.

    The first open session on/after delayed availability has age zero. Rolling
    N retains ages 0..N-1 and averages retained records. Exponential H uses
    weight 2**(-age/H), divided by the count of all visible records, NOT the
    sum of weights: a single event therefore actually halves after H sessions.
    Valid empty records remain in denominators, as in the cumulative baseline.
    """
    scenario = validate_scenario(scenario)
    calendar = [date.fromisoformat(value) for value in session_dates]
    if not calendar or calendar != sorted(set(calendar)):
        raise ValueError("memory age requires a unique increasing trading calendar")
    base = join_steps(steps, stock_code, rows)
    candidates = [(row, local_visible_date(row["available_at"]) + timedelta(days=scenario["lag_days"]))
                  for row in rows if row["stock_code"] == stock_code]
    output = []
    for step in base:
        cutoff = date.fromisoformat(step["signal_cutoff_date"])
        cutoff_index = bisect_left(calendar, cutoff)
        if cutoff_index == len(calendar) or calendar[cutoff_index] != cutoff:
            raise ValueError("signal cutoff is absent from the trading calendar")
        visible = [(row, day) for row, day in candidates if day <= cutoff]
        weighted = []
        for row, day in visible:
            if day < calendar[0]:
                raise ValueError("trading calendar starts after an available source record")
            age = cutoff_index - bisect_left(calendar, day)
            if age < 0:
                raise AssertionError("future text entered a memory scenario")
            mode, horizon = scenario["memory_mode"], scenario["memory_sessions"]
            if mode == "rolling" and age >= horizon:
                continue
            weight = 2.0 ** (-age / horizon) if mode == "exponential" else 1.0
            weighted.append((row, weight, age))
        denominator = len(visible) if scenario["memory_mode"] == "exponential" else len(weighted)
        joined = dict(step)
        joined["text_signal"] = sum(row["text_signal"] * weight for row, weight, _ in weighted) / denominator if denominator else 0.0
        joined["text_uncertainty"] = sum(row["uncertainty"] * weight for row, weight, _ in weighted) / denominator if denominator else 0.0
        joined["semantic_item_count"] = len(weighted)
        joined["semantic_visible_item_count"] = len(visible)
        joined["semantic_expired_item_count"] = len(visible) - len(weighted)
        if scenario["memory_mode"] == "cumulative" and scenario["lag_days"] == 0:
            if any(joined[key] != step[key] for key in ("text_signal", "text_uncertainty", "semantic_item_count")):
                raise ValueError("China-time cumulative join differs from the archived daily join")
            # Preserve baseline evidence identities for exact trajectory parity.
        else:
            joined["text_evidence"] = "semantic-memory:" + canonical_hash({
                "scenario": scenario, "cutoff": step["signal_cutoff_date"],
                "weighted_items": [(row["item_id"], weight, age) for row, weight, age in weighted],
                "denominator": denominator,
            })
        output.append(joined)
    return output


def trajectory_hash(path: dict) -> str:
    """Economic state, every decision and ledger movement, excluding join metadata."""
    return canonical_hash([{key: step[key] for key in ("agents", "price_index_before", "price_index_after",
                           "external_cash", "external_shares", "fee_pool")} for step in path["trace"]])


def _config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "base_config", "baseline_directory", "scenarios"}
    if not isinstance(cfg, dict) or set(cfg) != required or any(
            not isinstance(cfg[key], str) or not cfg[key].strip() for key in required - {"scenarios"}):
        raise ValueError("memory sensitivity requires a complete configuration")
    if not isinstance(cfg["scenarios"], list) or not cfg["scenarios"]:
        raise ValueError("memory sensitivity needs a nonempty fixed scenario list")
    scenarios = [validate_scenario(row) for row in cfg["scenarios"]]
    keys = [(row["memory_mode"], row["memory_sessions"], row["lag_days"]) for row in scenarios]
    if len(set(keys)) != len(keys) or len({row["scenario_id"] for row in scenarios}) != len(scenarios):
        raise ValueError("memory scenarios must be unique")
    if ("cumulative", None, 0) not in keys:
        raise ValueError("the cumulative zero-delay baseline must be included")
    return cfg


def run_sensitivity(config_path: Path, output_dir: Path) -> dict:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    cfg = _config(config_path)
    base_path = (config_path.parent / cfg["base_config"]).resolve()
    base = _read_config(base_path)
    if base["selection_rule"] != "all_signal_stocks":
        raise ValueError("memory sensitivity requires the entire signal cohort")
    market, rows, gate, inputs = _verified_inputs(base_path, base)
    baseline_dir = (config_path.parent / cfg["baseline_directory"]).resolve()
    baseline_summary = load_verified_summary(baseline_dir)
    baseline_manifest_path = baseline_dir / "semantic_ablation_manifest.json"
    baseline_manifest = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))
    if (baseline_manifest["inputs"].get(str(base_path)) != file_sha256(base_path)
            or baseline_summary["market_dataset_id"] != market["market_dataset_id"]
            or baseline_summary["stocks"] != len(base["stock_codes"])):
        raise ValueError("archived baseline does not match the fixed configuration and market")
    for path in [config_path, baseline_manifest_path] + [baseline_dir / name for name in baseline_manifest["artifacts"]]:
        inputs[str(path)] = file_sha256(path)
    if any(output_dir == Path(name) or output_dir in Path(name).parents for name in inputs):
        raise ValueError("memory output must be separate from all input files")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty memory sensitivity output directory")
    codes = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
    identity = {"inputs": inputs, "code_sha256": codes}
    replay_id = canonical_hash(identity)
    archived = json.loads((baseline_dir / "semantic_ablation.json").read_text(encoding="utf-8"))
    if set(archived["paths"]) != set(base["stock_codes"]):
        raise ValueError("archived baseline path coverage differs from the selected cohort")
    market_path = (base_path.parent / base["market_manifest"]).resolve()
    groups = _load_market(market_path.parent / "market_daily.csv", market)
    download = json.loads((base_path.parent / base["download_manifest"]).read_text(encoding="utf-8"))
    suspended = {row["symbol"].split(".")[1]: set(row["suspended_sessions"])
                 for row in download["quote_checks"] if row["symbol"] != download["benchmark_symbol"]}
    agents = [AgentParameters(**row) for row in base["agents"]]
    comparisons, diagnostics, grouped = [], [], []
    controls, controls_hashes, baseline_hashes, steps_by_code = {}, {}, {}, {}
    for code in base["stock_codes"]:
        steps = prepare_steps(groups[code], base["start_date"], base["end_date"],
                              base["momentum_sessions"], base["volatility_sessions"])
        for step in steps:
            step["execution_available"] = step["execution_reference_date"] not in suspended[code]
        steps_by_code[code] = steps
        controls[code] = archived["paths"][code]["no_text"]["summary"]
        controls_hashes[code] = trajectory_hash(archived["paths"][code]["no_text"])
        baseline_hashes[code] = trajectory_hash(archived["paths"][code]["text"])
    # The archived detailed ledger stays in its original directory, avoiding duplicate huge traces.
    archived_summaries = {code: archived["paths"][code]["text"]["summary"] for code in base["stock_codes"]}
    del archived
    for scenario in cfg["scenarios"]:
        scenario_rows, scenario_diagnostics = [], []
        is_baseline = scenario["memory_mode"] == "cumulative" and scenario["lag_days"] == 0
        for code, steps in steps_by_code.items():
            joined = memory_join(steps, code, rows, market["session_dates"], scenario)
            original = join_steps(steps, code, rows)
            active = replay_semantic_path(joined, agents, base["transaction_cost_rate"], use_text=True)
            control = replay_semantic_path(joined, agents, base["transaction_cost_rate"], use_text=False)
            if control["summary"] != controls[code] or trajectory_hash(control) != controls_hashes[code]:
                raise AssertionError("changing text memory or visibility changed the no-text trajectory")
            if is_baseline and (active["summary"] != archived_summaries[code]
                                or trajectory_hash(active) != baseline_hashes[code]):
                raise AssertionError("cumulative zero-delay scenario failed full archived trajectory parity")
            effect = [step for step in joined if step["text_signal"] != 0 or step["text_uncertainty"] != 0]
            if not effect and (active["summary"] != control["summary"] or trajectory_hash(active) != trajectory_hash(control)):
                raise AssertionError("stock without a text effect changed its economic trajectory")
            for path in (active, control):
                for step in path["trace"]:
                    if not step["execution_available"] and any(
                            row["filled_shares"] != 0 or row["fees_paid"] != 0 for row in step["agents"].values()):
                        raise AssertionError("a suspended execution reference day incurred a fill or fee")
            diagnostic = {"scenario_id": scenario["scenario_id"], "stock_code": code,
                          "sessions": len(joined), "text_effect_days": len(effect),
                          "first_text_effect_trade_date": effect[0]["trade_date"] if effect else None,
                          "changed_signal_days": sum(a["text_signal"] != b["text_signal"]
                                                     or a["text_uncertainty"] != b["text_uncertainty"]
                                                     for a, b in zip(joined, original)),
                          "direction_exposure": sum(abs(s["text_signal"]) for s in joined),
                          "uncertainty_exposure": sum(s["text_uncertainty"] for s in joined),
                          "expired_item_days": sum(s["semantic_expired_item_count"] for s in joined),
                          "signal_schedule_sha256": canonical_hash(joined)}
            scenario_diagnostics.append(diagnostic)
            for agent in agents:
                a, b = active["summary"][agent.name], control["summary"][agent.name]
                scenario_rows.append({"scenario_id": scenario["scenario_id"], "stock_code": code, "role": agent.role,
                                      "text_wealth_multiple": a["final_wealth"] / agent.initial_cash,
                                      "no_text_wealth_multiple": b["final_wealth"] / agent.initial_cash,
                                      "wealth_difference_multiple": (a["final_wealth"] - b["final_wealth"]) / agent.initial_cash,
                                      "text_max_drawdown": a["max_drawdown"], "no_text_max_drawdown": b["max_drawdown"],
                                      "text_trades": a["trades"], "no_text_trades": b["trades"],
                                      "text_summary": a, "no_text_summary": b})
        for agent in agents:
            values = [row["wealth_difference_multiple"] for row in scenario_rows if row["role"] == agent.role]
            grouped.append({**scenario, "role": agent.role, "stocks": len(values),
                            "mean_difference_multiple": statistics.mean(values),
                            "minimum": min(values), "maximum": max(values),
                            "positive": sum(v > 0 for v in values), "negative": sum(v < 0 for v in values),
                            "unchanged": sum(v == 0 for v in values),
                            "changed_signal_days": sum(row["changed_signal_days"] for row in scenario_diagnostics),
                            "text_effect_days": sum(row["text_effect_days"] for row in scenario_diagnostics)})
        comparisons.extend(scenario_rows)
        diagnostics.extend(scenario_diagnostics)
    if (any(file_sha256(Path(name)) != digest for name, digest in inputs.items())
            or codes != {name: file_sha256(path) for name, path in CODE_PATHS.items()}):
        raise RuntimeError("memory sensitivity source or code changed during calculation")
    summary = {"pipeline_version": VERSION, "run_id": cfg["run_id"], "replay_id": replay_id,
               "market_dataset_id": market["market_dataset_id"], "stocks": len(base["stock_codes"]),
               "sessions_per_stock": sorted({len(steps) for steps in steps_by_code.values()}),
               "scenario_count": len(cfg["scenarios"]), "comparison_rows": len(comparisons),
               "blocked_execution_days_per_scenario": baseline_summary["blocked_execution_days"],
               "baseline_trajectory_parity": True, "no_text_trajectory_invariant": True,
               "scenarios": cfg["scenarios"], "grouped": grouped,
               "information_rule": "China-time end-of-day cutoff at t-2; delay in natural days, memory age in open sessions",
               "interpretation": "Fixed exploratory sensitivity grid; no best setting selected or behavior calibration claimed"}
    result = {**summary, "review_basis": gate["review_basis"], "comparisons": comparisons,
              "signal_diagnostics": diagnostics}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        for name, payload in (("semantic_memory_results.json", result), ("semantic_memory_summary.json", summary)):
            (staging / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        lines = ["# 文本记忆与公开延迟敏感性", "",
                 f"固定 {summary['stocks']} 家公司，{summary['scenario_count']} 个情景，{summary['comparison_rows']} 组公司与角色对照。",
                 "累计零延迟场景逐日决策和账本复现原回放。所有情景的无文本逐日决策和账本完全相同；停牌日零成交、零手续费。",
                 "延迟按自然日；记忆年龄按交易日历计算，延迟可见日之后的首个交易日记为年龄 0。截止日为中国时间 t-2 日末。",
                 "滚动 N 日保留年龄 0 到 N-1 的记录，并对保留记录取均值。指数 H 日半衰期使用 2^(-年龄/H)，除以已可见记录数，单条事件在 H 个交易日后幅度减半。",
                 "有效空记录沿用基线分母口径。延迟只移动文本的可见日；行情、成本、交易约束和 Agent 参数均固定。",
                 "", "| 记忆 | 天数 / 半衰期 | 延迟自然日 | 角色 | 公司数 | 平均末值差/初值 | 正 / 负 / 零 | 信号改变公司日 |",
                 "|---|---:|---:|---|---:|---:|---|---:|"]
        for row in grouped:
            lines.append(f"| {row['memory_mode']} | {row['memory_sessions'] or '长期'} | {row['lag_days']} | {row['role']} | "
                         f"{row['stocks']} | {row['mean_difference_multiple']:+.6%} | "
                         f"{row['positive']} / {row['negative']} / {row['unchanged']} | {row['changed_signal_days']} |")
        lines += ["", "报告全部固定情景及全体公司，不按财富结果挑选最佳记忆参数。单一 AI 参考、话题分层样本和公开时间代理限制仍保留。",
                  "原始基线日账本已存档；本结果保存逐公司和角色的财富、交易与回撤摘要、逐公司信号暴露和重放哈希，可按固定输入重新生成日路径。",
                  "结果只是规则对记忆与延迟假设的敏感性，不是投资者记忆估计、真实价格形成或监管预测验证。", ""]
        (staging / "semantic_memory_report.md").write_text("\n".join(lines), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "replay_id": replay_id, **identity,
                    "artifacts": {name: {"sha256": file_sha256(staging / name)} for name in sorted(ARTIFACTS)}}
        (staging / "semantic_memory_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return summary


def load_memory_summary(directory: Path) -> dict:
    manifest = json.loads((directory / "semantic_memory_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("pipeline_version") != VERSION or manifest.get("code_sha256") != {
            name: file_sha256(path) for name, path in CODE_PATHS.items()}:
        raise ValueError("memory sensitivity code differs from the recorded run")
    if set(manifest.get("artifacts", {})) != ARTIFACTS:
        raise ValueError("memory sensitivity artifact set is invalid")
    for name, info in manifest["artifacts"].items():
        if file_sha256(directory / name) != info["sha256"]:
            raise ValueError("memory sensitivity artifact changed")
    if not manifest.get("inputs") or any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items()):
        raise ValueError("memory sensitivity source changed")
    identity = {key: manifest[key] for key in ("inputs", "code_sha256")}
    summary = json.loads((directory / "semantic_memory_summary.json").read_text(encoding="utf-8"))
    if (manifest["replay_id"] != canonical_hash(identity) or summary.get("replay_id") != manifest["replay_id"]
            or summary.get("pipeline_version") != VERSION):
        raise ValueError("memory sensitivity summary identity is invalid")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = run_sensitivity(args.config, args.output_dir)
    print(json.dumps({key: summary[key] for key in ("stocks", "scenario_count", "comparison_rows",
                       "baseline_trajectory_parity", "no_text_trajectory_invariant")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
