"""Test finite background inventory demand with matched idle-resource controls."""

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

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .feedback_auction import simulate_feedback
from .feedback_experiment import CODE_PATHS as FEEDBACK_CODE, verified_inputs
from .participant_market import simulate_market, validate_background
from .semantic_memory_sensitivity import canonical_hash

VERSION = "finite-background-sensitivity-v1"
CODE_PATHS = {**FEEDBACK_CODE, **{f"simulation/{name}": Path(__file__).with_name(name)
                              for name in ("participant_market.py", "background_experiment.py", "audit_feedback.py")}}
ARTIFACTS = {"background_results.json", "background_summary.json", "background_report.md", "background_ledger.jsonl.gz"}


def funding_signature(case):
    return tuple(case[field] for field in ("participants", "initial_cash", "initial_shares"))


def experiment_inputs(path: Path):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(cfg, dict) or set(cfg) != {"run_id", "feedback_config", "core_scenario_id", "background_cases"}
            or any(not isinstance(cfg[key], str) or not cfg[key].strip() for key in ("run_id", "feedback_config", "core_scenario_id"))):
        raise ValueError("background experiment requires all explicit fields")
    parent_path = (path.parent / cfg["feedback_config"]).resolve()
    parent, auction, base, market, gate, inputs, joined = verified_inputs(parent_path)
    core = next((s for s in parent["scenarios"] if s["scenario_id"] == cfg["core_scenario_id"]), None)
    if core is None or core["feedback_mode"] != "endogenous":
        raise ValueError("background core must name a declared endogenous feedback scenario")
    cases = cfg["background_cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("background cases must be explicit")
    for case in cases:
        validate_background(case, auction["venue"]["lot_size"])
    if len({c["case_id"] for c in cases}) != len(cases) or cases[0]["mode"] != "none" or sum(c["mode"] == "none" for c in cases) != 1:
        raise ValueError("background cases require unique identities and a first no-background control")
    idle = {funding_signature(c): c["case_id"] for c in cases if c["mode"] == "idle"}
    if len(idle) != sum(c["mode"] == "idle" for c in cases) or any(funding_signature(c) not in idle for c in cases if c["mode"] == "active"):
        raise ValueError("each active resource budget requires exactly one matched idle control")
    inputs[str(path)] = file_sha256(path)
    return cfg, parent, auction, base, market, gate, inputs, joined, core


def strategy_signature(day: dict, names: set[str]):
    orders = [{k: v for k, v in item.items() if k != "sequence"} for item in day["auction"]["orders"] if item["owner"] in names]
    return {"decisions": day["decisions"], "quotes": day["quotes"], "feedback": day["feedback"],
            "orders": sorted(orders, key=lambda item: item["owner"]), "trades": day["auction"]["trades"],
            "price": day["auction"]["price_after_minor"], "fee_pool": day["auction"]["fee_pool_minor"],
            "accounts": {name: day["auction"]["accounts"][name] for name in sorted(names)}}


def run_experiment(config_path: Path, output_dir: Path, progress=None):
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    cfg, parent, auction, base, market, gate, inputs, joined, core = experiment_inputs(config_path)
    if any(output_dir == Path(p) or output_dir in Path(p).parents for p in inputs):
        raise ValueError("background output must be separate from inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty background output directory")
    code = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
    identity = {"inputs": inputs, "code_sha256": code, "runtime": {"python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION}}
    experiment_id = canonical_hash(identity)
    agents = [AgentParameters(**row) for row in base["agents"]]
    paths, paired, participant_specs, ledger_rows, baseline_paths, idle_paths = [], [], {}, 0, 0, 0
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        with (staging / "background_ledger.jsonl.gz").open("wb") as binary, gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as ledger:
            for stock_index, (stock, steps) in enumerate(joined.items(), 1):
                baselines = {}
                for case in cfg["background_cases"]:
                    for response in parent["quote_response_bps"]:
                        results = []
                        for enabled in (False, True):
                            venue = {**auction["venue"], "quote_response_bps": response}
                            result = simulate_market(steps, agents, core, venue, parent["feedback_parameters"], case, stock, enabled)
                            strategy_names = {name for name, spec in result["participant_specs"].items() if spec["kind"] == "strategy"}
                            if case["mode"] == "none":
                                old = simulate_feedback(steps, agents, core, venue, parent["feedback_parameters"], enabled)
                                if (any(any(a[key] != b[key] for key in ("decisions", "quotes", "observations", "auction", "feedback"))
                                        for a, b in zip(result["trace"], old["trace"], strict=True))
                                        or any(any(result["summary"]["account_summary"][name][field] != value for field, value in saved.items())
                                               for name, saved in old["summary"]["agent_summary"].items())):
                                    raise AssertionError("no-background path did not reproduce the previous own-price market")
                                baseline_paths += 1
                                baselines[response, enabled] = result
                            elif case["mode"] == "idle":
                                if any(strategy_signature(a, strategy_names) != strategy_signature(b, strategy_names)
                                       for a, b in zip(result["trace"], baselines[response, enabled]["trace"], strict=True)):
                                    raise AssertionError("idle background resources changed strategy trading")
                                idle_paths += 1
                            participant_specs[case["case_id"]] = result["participant_specs"]
                            for day in result["trace"]:
                                record = {"stock_code": stock, "case_id": case["case_id"], "quote_response_bps": response, "use_text": enabled, **day}
                                ledger.write((json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode())
                                ledger_rows += 1
                            paths.append({"stock_code": stock, "case_id": case["case_id"], "quote_response_bps": response, **result["summary"]})
                            results.append(result)
                        no_text, text = results
                        if not any(step["text_signal"] or step["text_uncertainty"] for step in steps):
                            if any(a["decisions"] != b["decisions"] or a["auction"] != b["auction"] or a["background_demands"] != b["background_demands"]
                                   for a, b in zip(text["trace"], no_text["trace"], strict=True)):
                                raise AssertionError("empty text altered background or strategy trading")
                        paired.append({"stock_code": stock, "case_id": case["case_id"], "quote_response_bps": response,
                                       "price_difference_multiple": (text["summary"]["final_price_minor"] - no_text["summary"]["final_price_minor"]) / auction["venue"]["price_start_minor"],
                                       "role_wealth_difference": {role: text["summary"]["role_wealth_multiple"][role] - no_text["summary"]["role_wealth_multiple"][role]
                                                                 for role in text["summary"]["role_wealth_multiple"]}})
                if progress is not None:
                    progress(stock_index, len(joined))
        grouped = []
        for case in cfg["background_cases"]:
            for response in parent["quote_response_bps"]:
                pairs = [p for p in paired if p["case_id"] == case["case_id"] and p["quote_response_bps"] == response]
                selected = [p for p in paths if p["case_id"] == case["case_id"] and p["quote_response_bps"] == response]
                group = {"case_id": case["case_id"], "quote_response_bps": response, "stocks": len(pairs),
                         "mean_price_difference_multiple": statistics.mean(p["price_difference_multiple"] for p in pairs),
                         "changed_stock_prices": sum(p["price_difference_multiple"] != 0 for p in pairs),
                         "minimum_price_difference_multiple": min(p["price_difference_multiple"] for p in pairs),
                         "maximum_price_difference_multiple": max(p["price_difference_multiple"] for p in pairs),
                         "role_wealth_difference": {role: statistics.mean(p["role_wealth_difference"][role] for p in pairs) for role in ("aggressive", "conservative", "institutional")}}
                for enabled, prefix in ((True, "text"), (False, "no_text")):
                    subset = [p for p in selected if p["use_text"] == enabled]
                    group.update({f"{prefix}_matched_volume": sum(p["matched_volume"] for p in subset),
                                  f"{prefix}_trading_companies": sum(p["matched_volume"] > 0 for p in subset),
                                  f"{prefix}_trade_sessions": sum(p["trade_sessions"] for p in subset),
                                  f"{prefix}_mean_final_price_multiple": statistics.mean(p["final_price_minor"] / auction["venue"]["price_start_minor"] for p in subset),
                                  f"{prefix}_volume_by_counterparty": {kind: sum(p["volume_by_counterparty"][kind] for p in subset)
                                                                       for kind in ("strategy_strategy", "strategy_background", "background_background")}})
                    for kind in ("strategy", "background"):
                        totals = {field: sum(p[f"{kind}_orders"][field] for p in subset)
                                  for field in ("requested", "accepted", "filled", "cash_clipped_orders", "inventory_clipped_orders")}
                        totals["accepted_fill_fraction"] = totals["filled"] / totals["accepted"] if totals["accepted"] else 0.0
                        group[f"{prefix}_{kind}_orders"] = totals
                grouped.append(group)
        if any(file_sha256(Path(p)) != digest for p, digest in inputs.items()) or code != {name: file_sha256(path) for name, path in CODE_PATHS.items()}:
            raise RuntimeError("background code or source changed during execution")
        summary = {"pipeline_version": VERSION, "run_id": cfg["run_id"], "experiment_id": experiment_id, "stocks": len(joined),
                   "paths": len(paths), "ledger_rows": ledger_rows, "sessions_per_stock": sorted({p["sessions"] for p in paths}),
                   "background_cases": cfg["background_cases"], "core_scenario": core, "quote_response_bps": parent["quote_response_bps"],
                   "venue": auction["venue"], "feedback_parameters": parent["feedback_parameters"], "market_dataset_id": market["market_dataset_id"],
                   "no_background_parity_paths": baseline_paths, "idle_resources_parity_paths": idle_paths, "grouped": grouped,
                   "interpretation": "Finite hypothetical inventory demand, not calibrated liquidity or investor behavior"}
        result = {**summary, "review_basis": gate["review_basis"], "participant_specs": participant_specs, "path_summaries": paths, "paired_stock_effects": paired}
        for name, value in (("background_summary.json", summary), ("background_results.json", result)):
            (staging / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        lines = ["# 有限背景交易需求", "", f"{len(joined)} 家公司，{len(cfg['background_cases'])} 个固定情景；{len(paths)} 条路径、{ledger_rows:,} 条完整日账本。",
                 f"零背景 {baseline_paths} 条路径逐日复现上一版；闲置背景 {idle_paths} 条路径保持策略决策、成交与账户不变。",
                 "活跃背景的库存目标来自固定种子、股票、模型日和账户身份，文本开关不改变抽样；没有外部现金补充或库存重置。",
                 "每个活跃资金配置都设有相同资金与库存的闲置对照。报价主动程度、库存目标范围、日订单上限和两种子均为模型假设。",
                 "| 背景 | 响应 | 平均文本末价差 / 初价 | 无文本成交公司 | 无文本策略成交率 | 无文本策略—背景成交单位 | 无文本背景间成交单位 |",
                 "|---|---:|---:|---:|---:|---:|---:|"]
        for row in grouped:
            lines.append(f"| {row['case_id']} | {row['quote_response_bps']} | {row['mean_price_difference_multiple']:+.6%} | {row['no_text_trading_companies']} | "
                         f"{row['no_text_strategy_orders']['accepted_fill_fraction']:.4%} | {row['no_text_volume_by_counterparty']['strategy_background']} | {row['no_text_volume_by_counterparty']['background_background']} |")
        lines += ["", "策略成交率使用策略自身已接受单边订单量，背景自相交易另列，不把总成交量当作策略可执行性。",
                  "价格只由有限双边成交形成，反馈只读取自身已生成且截至 t-2 的价格。真实日期、停牌代理和问答仍为条件。",
                  "背景目标并未由真实用户流、盘口或持仓估计；改善模型成交不等于验证真实市场流动性、高保真历史拟合或预测。", ""]
        (staging / "background_report.md").write_text("\n".join(lines), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "experiment_id": experiment_id, "generated_at": datetime.now(timezone.utc).isoformat(), **identity,
                    "artifacts": {name: {"sha256": file_sha256(staging / name)} for name in sorted(ARTIFACTS)}}
        (staging / "background_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for p in staging.iterdir():
            p.replace(output_dir / p.name)
    return summary


def load_summary(directory):
    manifest = json.loads((directory / "background_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION or manifest.get("code_sha256") != {name: file_sha256(p) for name, p in CODE_PATHS.items()}
            or canonical_hash({key: manifest[key] for key in ("inputs", "code_sha256", "runtime")}) != manifest.get("experiment_id")):
        raise ValueError("background code or identity differs")
    if set(manifest.get("artifacts", {})) != ARTIFACTS or any(file_sha256(directory / name) != info["sha256"] for name, info in manifest["artifacts"].items()):
        raise ValueError("background artifact changed")
    if not manifest.get("inputs") or any(file_sha256(Path(p)) != digest for p, digest in manifest["inputs"].items()):
        raise ValueError("background source changed")
    summary = json.loads((directory / "background_summary.json").read_text(encoding="utf-8"))
    if summary.get("pipeline_version") != VERSION or summary.get("experiment_id") != manifest["experiment_id"]:
        raise ValueError("background summary belongs to another run")
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
