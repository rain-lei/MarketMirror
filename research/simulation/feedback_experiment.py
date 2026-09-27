"""Run all-company own-price feedback and cohort/order/inventory sensitivity."""

from __future__ import annotations

import argparse
import gzip
import json
import platform
import statistics
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .feedback_auction import simulate_feedback, validate_feedback, validate_scenario
from .historical_replay import prepare_steps
from .semantic_auction_experiment import CODE_PATHS as AUCTION_CODE, _config as auction_config, simulate_agents
from .semantic_historical_replay import _read_config, _verified_inputs
from .semantic_memory_sensitivity import canonical_hash, memory_join

VERSION = "feedback-cohort-sensitivity-v1"
CODE_PATHS = {**AUCTION_CODE, **{f"simulation/{name}": Path(__file__).with_name(name)
                              for name in ("feedback_auction.py", "feedback_experiment.py", "audit_auction.py")}}
ARTIFACTS = {"feedback_results.json", "feedback_summary.json", "feedback_report.md", "feedback_ledger.jsonl.gz"}


def load_config(path: Path) -> tuple[dict, dict, Path]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "auction_config", "feedback_parameters", "quote_response_bps", "scenarios"}
    if (not isinstance(cfg, dict) or set(cfg) != required or any(not isinstance(cfg[key], str) or not cfg[key].strip()
            for key in ("run_id", "auction_config"))):
        raise ValueError("feedback experiment requires all explicit fields")
    parent_path = (path.parent / cfg["auction_config"]).resolve()
    parent = auction_config(parent_path)
    validate_feedback(cfg["feedback_parameters"])
    responses = cfg["quote_response_bps"]
    if (not isinstance(responses, list) or not responses or any(type(v) is not int for v in responses)
            or responses != sorted(set(responses)) or not set(responses) <= set(parent["quote_response_bps"])):
        raise ValueError("response grid must be an increasing subset of the declared auction grid")
    scenarios = cfg["scenarios"]
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("feedback scenarios must be explicit")
    for scenario in scenarios:
        validate_scenario(scenario, parent["venue"]["lot_size"])
    if len({s["scenario_id"] for s in scenarios}) != len(scenarios):
        raise ValueError("feedback scenario identifiers must be unique")
    baseline = scenarios[0]
    if (baseline["feedback_mode"], baseline["profile_mode"], baseline["order_mode"], baseline["seed"], baseline["inventory"]) != (
            "conditioned", "homogeneous", "forward", 0, parent["initial_inventory_per_role"]):
        raise ValueError("first scenario must exactly preserve the previous conditioned cohort")
    if any(sum(s["inventory"]) != sum(baseline["inventory"]) for s in scenarios):
        raise ValueError("inventory sensitivity must preserve aggregate share supply per role")
    return cfg, parent, parent_path


def verified_inputs(config_path: Path):
    cfg, parent, parent_path = load_config(config_path)
    base_path = (parent_path.parent / parent["base_config"]).resolve()
    base = _read_config(base_path)
    if base["selection_rule"] != "all_signal_stocks":
        raise ValueError("feedback experiment requires all signal companies")
    market, rows, gate, inputs = _verified_inputs(base_path, base)
    inputs.update({str(path): file_sha256(path) for path in (config_path, parent_path)})
    groups = _load_market((base_path.parent / base["market_manifest"]).resolve().parent / "market_daily.csv", market)
    download = json.loads((base_path.parent / base["download_manifest"]).read_text(encoding="utf-8"))
    suspended = {r["symbol"].split(".")[1]: set(r["suspended_sessions"])
                 for r in download["quote_checks"] if r["symbol"] != download["benchmark_symbol"]}
    joined = {}
    for stock in base["stock_codes"]:
        steps = prepare_steps(groups[stock], base["start_date"], base["end_date"], base["momentum_sessions"], base["volatility_sessions"])
        joined[stock] = memory_join(steps, stock, rows, market["session_dates"], parent["memory_scenario"])
        for step in joined[stock]:
            step["execution_available"] = step["execution_reference_date"] not in suspended[stock]
    return cfg, parent, base, market, gate, inputs, joined


def run_experiment(config_path: Path, output_dir: Path, progress=None) -> dict:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    cfg, parent, base, market, gate, inputs, joined = verified_inputs(config_path)
    if any(output_dir == Path(name) or output_dir in Path(name).parents for name in inputs):
        raise ValueError("feedback output must be separate from input files")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty feedback output directory")
    code = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
    runtime = {"python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION}
    identity = {"inputs": inputs, "code_sha256": code, "runtime": runtime}
    experiment_id = canonical_hash(identity)
    agents = [AgentParameters(**row) for row in base["agents"]]
    paths, paired, records, participant_specs, parity_paths = [], [], 0, {}, 0
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        with (staging / "feedback_ledger.jsonl.gz").open("wb") as binary, gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as ledger:
            for stock_index, (stock, steps) in enumerate(joined.items(), start=1):
                for scenario_index, scenario in enumerate(cfg["scenarios"]):
                    for response in cfg["quote_response_bps"]:
                        results = []
                        for enabled in (False, True):
                            result = simulate_feedback(steps, agents, scenario, {**parent["venue"], "quote_response_bps": response},
                                                       cfg["feedback_parameters"], enabled)
                            if scenario_index == 0:
                                old = simulate_agents(steps, agents, scenario["inventory"], {**parent["venue"], "quote_response_bps": response}, enabled)
                                if (any(result["summary"][key] != value for key, value in old["summary"].items() if key != "trace_sha256")
                                        or any(any(a[key] != b[key] for key in ("decisions", "quotes", "auction"))
                                               for a, b in zip(result["trace"], old["trace"], strict=True))):
                                    raise AssertionError("conditioned baseline did not reproduce the archived auction engine")
                                parity_paths += 1
                            participant_specs[scenario["scenario_id"]] = result["participants"]
                            for day in result["trace"]:
                                record = {"stock_code": stock, "scenario_id": scenario["scenario_id"],
                                          "quote_response_bps": response, "use_text": enabled, **day}
                                ledger.write((json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                                records += 1
                            paths.append({"stock_code": stock, "scenario_id": scenario["scenario_id"], **result["summary"]})
                            results.append(result)
                        control, active = results
                        if not any(step["text_signal"] or step["text_uncertainty"] for step in steps):
                            if any(a["auction"] != b["auction"] or a["decisions"] != b["decisions"] for a, b in zip(active["trace"], control["trace"], strict=True)):
                                raise AssertionError("empty text changed an auction path")
                        if response == 0 and any(r["summary"]["price_change_sessions"] for r in results):
                            raise AssertionError("zero quote response changed prices")
                        paired.append({"stock_code": stock, "scenario_id": scenario["scenario_id"], "quote_response_bps": response,
                                       "price_difference_multiple": (active["summary"]["final_price_minor"] - control["summary"]["final_price_minor"]) / parent["venue"]["price_start_minor"],
                                       "text_final_price_minor": active["summary"]["final_price_minor"], "no_text_final_price_minor": control["summary"]["final_price_minor"],
                                       "text_matched_volume": active["summary"]["matched_volume"], "no_text_matched_volume": control["summary"]["matched_volume"],
                                       "role_wealth_difference": {role: active["summary"]["role_wealth_multiple"][role] - control["summary"]["role_wealth_multiple"][role]
                                                                 for role in active["summary"]["role_wealth_multiple"]}})
                if progress is not None:
                    progress(stock_index, len(joined))
        grouped = []
        for scenario in cfg["scenarios"]:
            for response in cfg["quote_response_bps"]:
                pairs = [r for r in paired if r["scenario_id"] == scenario["scenario_id"] and r["quote_response_bps"] == response]
                selected = [r for r in paths if r["scenario_id"] == scenario["scenario_id"] and r["quote_response_bps"] == response]
                group = {"scenario_id": scenario["scenario_id"], "quote_response_bps": response, "stocks": len(pairs),
                         "mean_price_difference_multiple": statistics.mean(r["price_difference_multiple"] for r in pairs),
                         "changed_stock_prices": sum(r["price_difference_multiple"] != 0 for r in pairs),
                         "minimum_price_difference_multiple": min(r["price_difference_multiple"] for r in pairs),
                         "maximum_price_difference_multiple": max(r["price_difference_multiple"] for r in pairs),
                         "role_wealth_difference": {role: statistics.mean(r["role_wealth_difference"][role] for r in pairs) for role in ("aggressive", "conservative", "institutional")}}
                for enabled, prefix in ((True, "text"), (False, "no_text")):
                    subset = [r for r in selected if r["use_text"] == enabled]
                    volume, accepted = sum(r["matched_volume"] for r in subset), sum(r["accepted_quantity"] for r in subset)
                    group.update({f"{prefix}_matched_volume": volume, f"{prefix}_accepted_fill_fraction": 2 * volume / accepted if accepted else 0.0,
                                  f"{prefix}_trade_sessions": sum(r["trade_sessions"] for r in subset),
                                  f"{prefix}_mean_final_price_multiple": statistics.mean(r["final_price_minor"] / parent["venue"]["price_start_minor"] for r in subset)})
                grouped.append(group)
        if (any(file_sha256(Path(name)) != digest for name, digest in inputs.items())
                or code != {name: file_sha256(path) for name, path in CODE_PATHS.items()}):
            raise RuntimeError("feedback inputs or code changed during execution")
        summary = {"pipeline_version": VERSION, "run_id": cfg["run_id"], "experiment_id": experiment_id,
                   "stocks": len(joined), "scenarios": cfg["scenarios"], "quote_response_bps": cfg["quote_response_bps"],
                   "paths": len(paths), "ledger_rows": records, "agents_per_market": 12,
                   "sessions_per_stock": sorted({r["sessions"] for r in paths}), "market_dataset_id": market["market_dataset_id"],
                   "venue": parent["venue"], "memory_scenario": parent["memory_scenario"], "feedback_parameters": cfg["feedback_parameters"],
                   "conditioned_baseline_parity_paths": parity_paths, "conditioned_baseline_trajectory_parity": True,
                   "grouped": grouped, "interpretation": "Own-price feedback and hypothetical cohort sensitivity; not calibrated market prediction"}
        result = {**summary, "review_basis": gate["review_basis"], "participant_specs": participant_specs,
                  "path_summaries": paths, "paired_stock_effects": paired}
        for name, value in (("feedback_summary.json", summary), ("feedback_results.json", result)):
            (staging / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        lines = ["# 模拟价格反馈、主体差异与订单顺序敏感性", "",
                 f"{len(joined)} 家公司；{len(cfg['scenarios'])} 个预先固定情景；{len(paths)} 条路径、{records:,} 条完整日账本。",
                 f"原外部条件基线的 {parity_paths} 条逐日决策、报价、成交与账户完全复现。",
                 "内生情景只从各自已生成且截至 t-2 的价格计算信号，历史实际收益、动量和波动率不进入该通道。",
                 "冷启动缺失收益按零补齐固定窗口，波动率采用预声明下限。历史日期、停牌代理和真实问答仍为条件输入。",
                 "| 情景 | 报价响应 | 平均文本末价差 / 初价 | 改变公司 | 有文本成交率 | 无文本成交率 |", "|---|---:|---:|---:|---:|---:|"]
        for group in grouped:
            lines.append(f"| {group['scenario_id']} | {group['quote_response_bps']} | {group['mean_price_difference_multiple']:+.6%} | "
                         f"{group['changed_stock_prices']} | {group['text_accepted_fill_fraction']:.4%} | {group['no_text_accepted_fill_fraction']:.4%} |")
        lines += ["", "四个主体画像是预设动量载荷、持续信念偏差、文本敏感度、基础仓位和风险预算差异；不代表观测到的真实投资者。",
                  "到达顺序只影响同价配给；正序、反序及两个固定哈希种子全部报告。均衡库存保持每类总股票供给和初始总财富不变。",
                  "角色财富差按该类合计末财富 / 合计初财富计算，避免不同初始持仓的等权倍数混淆。",
                  "所有观测、决策、限价、成交、账户、顺序和反馈证据保存在无损压缩账本；参数尚未校准，高保真历史拟合与预警未完成。", ""]
        (staging / "feedback_report.md").write_text("\n".join(lines), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "experiment_id": experiment_id, "generated_at": datetime.now(timezone.utc).isoformat(),
                    **identity, "artifacts": {name: {"sha256": file_sha256(staging / name)} for name in sorted(ARTIFACTS)}}
        (staging / "feedback_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return summary


def load_summary(directory: Path) -> dict:
    manifest = json.loads((directory / "feedback_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION or manifest.get("code_sha256") != {name: file_sha256(path) for name, path in CODE_PATHS.items()}
            or canonical_hash({key: manifest[key] for key in ("inputs", "code_sha256", "runtime")}) != manifest.get("experiment_id")):
        raise ValueError("feedback code or run identity differs")
    if set(manifest.get("artifacts", {})) != ARTIFACTS or any(file_sha256(directory / name) != value["sha256"] for name, value in manifest["artifacts"].items()):
        raise ValueError("feedback artifact changed")
    if not manifest.get("inputs") or any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items()):
        raise ValueError("feedback source changed")
    summary = json.loads((directory / "feedback_summary.json").read_text(encoding="utf-8"))
    if summary.get("pipeline_version") != VERSION or summary.get("experiment_id") != manifest["experiment_id"]:
        raise ValueError("feedback summary belongs to another run")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    def progress(done, total):
        if done % 10 == 0 or done == total:
            print(f"Processed {done}/{total} companies", flush=True)
    result = run_experiment(args.config, args.output_dir, progress)
    print(json.dumps({key: result[key] for key in ("stocks", "paths", "ledger_rows", "grouped")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
