"""All-company fixed baskets: shared cash, concentration and text controls."""
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
from .background_experiment import CODE_PATHS as BACKGROUND_CODE, experiment_inputs as background_inputs
from .portfolio_market import simulate_portfolio, validate_case
from .semantic_memory_sensitivity import canonical_hash

VERSION = "shared-portfolio-experiment-v2"
CODE_PATHS = {**BACKGROUND_CODE, **{f"simulation/{n}": Path(__file__).with_name(n)
              for n in ("portfolio_auction.py", "portfolio_market.py", "portfolio_experiment.py")}}
ARTIFACTS = {"portfolio_summary.json", "portfolio_results.json", "portfolio_ledger.jsonl.gz", "portfolio_report.md"}


def experiment_inputs(path):
    path = path.resolve()
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if (set(cfg) != {"run_id", "background_config", "background_case_id", "basket_size", "quote_response_bps", "cases"}
            or any(not isinstance(cfg[k], str) or not cfg[k] for k in ("run_id", "background_config", "background_case_id"))
            or type(cfg["basket_size"]) is not int or not 2 <= cfg["basket_size"] <= 8):
        raise ValueError("portfolio config requires explicit source, basket size and cases")
    bgcfg, parent, auction, base, market, gate, inputs, joined, core = background_inputs(path.parent / cfg["background_config"])
    background = next((c for c in bgcfg["background_cases"] if c["case_id"] == cfg["background_case_id"]), None)
    if background is None or background["mode"] != "active":
        raise ValueError("portfolio requires a declared active finite background")
    responses = cfg["quote_response_bps"]
    if (not isinstance(responses, list) or not responses or responses != sorted(set(responses))
            or any(type(v) is not int or v not in parent["quote_response_bps"] for v in responses)):
        raise ValueError("portfolio quote grid must be a declared increasing subset")
    if not isinstance(cfg["cases"], list) or not cfg["cases"]:
        raise ValueError("portfolio cases must be explicit")
    for case in cfg["cases"]:
        validate_case(case)
    if len({c["case_id"] for c in cfg["cases"]}) != len(cfg["cases"]):
        raise ValueError("portfolio case identities must be unique")
    caps = {c["institutional_asset_cap"] for c in cfg["cases"] if c["cash_mode"] == "shared"}
    if not any(c["cash_mode"] == "separated" and c["institutional_asset_cap"] in caps for c in cfg["cases"]):
        raise ValueError("portfolio requires a same-concentration separated cash control")
    stocks = sorted(joined)
    if len(stocks) % cfg["basket_size"]:
        raise ValueError("fixed baskets must cover every company with no dropped remainder")
    if any("initial_cash_weights" in case and len(case["initial_cash_weights"]) != cfg["basket_size"]
           for case in cfg["cases"]):
        raise ValueError("initial cash weights must match the configured basket size")
    baskets = [stocks[i:i+cfg["basket_size"]] for i in range(0, len(stocks), cfg["basket_size"])]
    inputs[str(path)] = file_sha256(path)
    return cfg, parent, auction, base, market, gate, inputs, joined, core, background, baskets


def run_experiment(config_path, output_dir, progress=None):
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    cfg, parent, auction, base, market, gate, inputs, joined, core, background, baskets = experiment_inputs(config_path)
    if any(output_dir == Path(p) or output_dir in Path(p).parents for p in inputs) or (output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("portfolio output must be a new empty directory separate from inputs")
    code = {n: file_sha256(p) for n, p in CODE_PATHS.items()}
    identity = {"inputs": inputs, "code_sha256": code, "runtime": {"python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION}}
    experiment_id = canonical_hash(identity)
    agents = [AgentParameters(**a) for a in base["agents"]]
    paths, ledger_rows, specs = [], 0, {}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        with (staging / "portfolio_ledger.jsonl.gz").open("wb") as binary, gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as ledger:
            for index, assets in enumerate(baskets):
                for case in cfg["cases"]:
                    for response in cfg["quote_response_bps"]:
                        for enabled in (False, True):
                            result = simulate_portfolio({a: joined[a] for a in assets}, agents, core, background,
                                  {**auction["venue"], "quote_response_bps": response}, parent["feedback_parameters"], case, enabled)
                            key = f"{index}:{case['case_id']}:{response}:{int(enabled)}"
                            specs[key] = result["participant_specs"]
                            paths.append({"path_id": key, "basket_index": index, "case_id": case["case_id"], "quote_response_bps": response, **result["summary"]})
                            for day in result["trace"]:
                                ledger.write((json.dumps({"path_id": key, **day}, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode())
                                ledger_rows += 1
                if progress:
                    progress(index + 1, len(baskets))
        groups = []
        for case in cfg["cases"]:
            for response in cfg["quote_response_bps"]:
                subset = [p for p in paths if p["case_id"] == case["case_id"] and p["quote_response_bps"] == response]
                group = {"case_id": case["case_id"], "quote_response_bps": response, "cash_mode": case["cash_mode"],
                         "institutional_asset_cap": case["institutional_asset_cap"],
                         "initial_cash_weights": case.get("initial_cash_weights")}
                for enabled, prefix in ((False, "no_text"), (True, "text")):
                    selected = [p for p in subset if p["use_text"] == enabled]
                    requested, accepted, filled = [sum(p[k] for p in selected) for k in ("strategy_requested", "strategy_accepted", "strategy_filled")]
                    group.update({prefix + "_strategy_fill_fraction": filled / accepted if accepted else 0.0,
                                  prefix + "_strategy_accepted": accepted, prefix + "_strategy_filled": filled,
                                  prefix + "_strategy_requested": requested,
                                  prefix + "_strategy_requested_fill_fraction": filled / requested if requested else 0.0,
                                  prefix + "_cash_clipped_orders": sum(p["cash_clipped_orders"] for p in selected),
                                  prefix + "_mean_final_price_multiple": statistics.mean(v / auction["venue"]["price_start_minor"] for p in selected for v in p["final_prices_minor"].values()),
                                  prefix + "_role_wealth_multiple": {r: statistics.mean(p["role_wealth_multiple"][r] for p in selected) for r in ("aggressive", "conservative", "institutional")},
                                  prefix + "_risk_breach_sessions": sum(a["risk_breach_sessions"] for p in selected for a in p["accounts"].values()),
                                  prefix + "_concentration_breach_sessions": sum(a["concentration_breach_sessions"] for p in selected for a in p["accounts"].values())})
                group["mean_text_price_difference_multiple"] = group["text_mean_final_price_multiple"] - group["no_text_mean_final_price_multiple"]
                groups.append(group)
        if code != {n: file_sha256(p) for n, p in CODE_PATHS.items()} or any(file_sha256(Path(p)) != d for p, d in inputs.items()):
            raise ValueError("portfolio code or sources changed during execution")
        summary = {"pipeline_version": VERSION, "experiment_id": experiment_id, "run_id": cfg["run_id"], "stocks": len(joined),
                   "baskets": baskets, "paths": len(paths), "ledger_rows": ledger_rows, "asset_call_rows": ledger_rows * cfg["basket_size"],
                   "cases": cfg["cases"], "quote_response_bps": cfg["quote_response_bps"], "grouped": groups,
                   "interpretation": "Fixed code-order baskets and hypothetical portfolios, not observed institutional holdings"}
        result = {**summary, "path_summaries": paths, "participant_specs": specs}
        for name, data in (("portfolio_summary.json", summary), ("portfolio_results.json", result)):
            (staging / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        lines = ["# 多资产共享现金与机构集中度", "", f"{len(joined)} 公司、{len(baskets)} 个固定篮子，{len(paths)} 条路径、{ledger_rows:,} 条组合日账本。",
                 "股票代码排序后固定分组，不代表行业或真实机构组合。全部买单先按限价和费用进行钱包比例预留，卖出收入下一轮才可用。",
                 "| 配置 | 策略报价响应 | 无文本策略成交率 | 有文本策略成交率 | 平均文本末价差 / 初价 | 无文本现金裁剪次数 |",
                 "|---|---:|---:|---:|---:|---:|"]
        for g in groups:
            lines.append(f"| {g['case_id']} | {g['quote_response_bps']} | {g['no_text_strategy_fill_fraction']:.4%} | {g['text_strategy_fill_fraction']:.4%} | {g['mean_text_price_difference_multiple']:+.6%} | {g['no_text_cash_clipped_orders']} |")
        lines += ["", "组合目标包含总仓位、风险预算和机构单资产集中度约束；实际成交可能不充分，事后超限分别计数。",
                  "协方差只取自身生成且截至 t-2 的价格收益，固定 25% 对角收缩及波动率下限。参数未经真实持仓、行业、财务或盘口校准。", ""]
        (staging / "portfolio_report.md").write_text("\n".join(lines), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "experiment_id": experiment_id, "generated_at": datetime.now(timezone.utc).isoformat(), **identity,
                    "artifacts": {n: {"sha256": file_sha256(staging / n)} for n in sorted(ARTIFACTS)}}
        (staging / "portfolio_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for p in staging.iterdir():
            p.replace(output_dir / p.name)
    return summary


def load_summary(directory):
    manifest = json.loads((directory / "portfolio_manifest.json").read_text(encoding="utf-8"))
    if (manifest["pipeline_version"] != VERSION or manifest["code_sha256"] != {n:file_sha256(p) for n,p in CODE_PATHS.items()}
            or canonical_hash({k:manifest[k] for k in ("inputs","code_sha256","runtime")}) != manifest["experiment_id"]
            or set(manifest["artifacts"]) != ARTIFACTS or any(file_sha256(directory/n) != d["sha256"] for n,d in manifest["artifacts"].items())
            or any(file_sha256(Path(p)) != d for p,d in manifest["inputs"].items())):
        raise ValueError("portfolio sources, code, identity or artifacts changed")
    summary=json.loads((directory/"portfolio_summary.json").read_text(encoding="utf-8"))
    if summary["experiment_id"] != manifest["experiment_id"] or summary["pipeline_version"] != VERSION:
        raise ValueError("portfolio summary identity differs")
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config",type=Path);parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    result=run_experiment(args.config,args.output_dir,lambda n,t:print(f"Processed {n}/{t} baskets",flush=True))
    print(json.dumps({k:result[k] for k in ("paths","ledger_rows","asset_call_rows","grouped")},ensure_ascii=False))


if __name__=="__main__":main()
