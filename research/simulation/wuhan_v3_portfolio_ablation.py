"""Pair gated Wuhan v3 Agent signals with the frozen no-text portfolio paths."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from ..semantic.wuhan_protocol_v3 import verify_pack
from ..semantic.wuhan_v3_signal_adapter import load_verified_signal_stream
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import simulate_portfolio
from .semantic_memory_sensitivity import canonical_hash
from .semantic_signal_join import join_steps
from .wuhan_portfolio_baseline import _initial_audit_state, _load_inputs, load_summary as load_baseline_summary


VERSION = "wuhan-v3-independent-portfolio-ablation-v1"
ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = {name: ROOT / name for name in (
    "simulation/wuhan_v3_portfolio_ablation.py",
    "simulation/wuhan_portfolio_baseline.py",
    "simulation/portfolio_market.py",
    "simulation/portfolio_auction.py",
    "simulation/portfolio_audit.py",
    "simulation/semantic_signal_join.py",
    "semantic/wuhan_v3_signal_adapter.py",
    "semantic/wuhan_protocol_v3.py",
    "semantic/score_wuhan_v3_validation.py",
)}
ARTIFACTS = {"v3_portfolio_summary.json", "v3_portfolio_paths.json", "v3_portfolio_report.md"}


def _check_cohort(pack_dir: Path, config_path: Path, joined: dict, rows: list[dict]) -> None:
    items, pack = verify_pack(pack_dir)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    universe_path = (config_path.parent / config["universe_config"]).resolve()
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    codes = set(universe["stock_codes"])
    if (pack.get("universe_size") != len(codes)
            or pack.get("universe_selection") != universe["selection"]
            or set(joined) != codes or len(items) != len(rows)
            or len({item["stock_code"] for item in items.values()}) != len(items)
            or {item["stock_code"] for item in items.values()} != {row["stock_code"] for row in rows}
            or not {row["stock_code"] for row in rows} <= codes
            or len(codes) != 126 or len(rows) != 102):
        raise ValueError("v3 replies do not match the frozen 126-company portfolio cohort")


def _audit_path(result: dict, agents: list, cfg: dict, assets: list[str], case: dict) -> int:
    prior = _initial_audit_state(agents, cfg, assets, case)
    for session, day in enumerate(result["trace"]):
        prior = audit_portfolio_day(day, prior, cfg["venue"], session)
    saved = result["summary"]
    if (saved["final_prices_minor"] != prior["prices"]
            or saved["fee_pool_minor"] != prior["fee_pool_minor"]
            or any(any(saved["accounts"][name][field] != account[field]
                       for field in ("wallets", "shares", "sellable"))
                   for name, account in prior["accounts"].items())):
        raise AssertionError("portfolio path differs from reconstructed wallets")
    return len(result["trace"])


def run_ablation(config_path: Path, baseline_dir: Path, pack_dir: Path, raw_dir: Path,
                 normal_dir: Path, score_dir: Path, signal_dir: Path, output_dir: Path) -> dict:
    paths = [path.resolve() for path in (config_path, baseline_dir, pack_dir, raw_dir,
                                        normal_dir, score_dir, signal_dir, output_dir)]
    (config_path, baseline_dir, pack_dir, raw_dir, normal_dir, score_dir,
     signal_dir, output_dir) = paths
    # Verify the signal and archived control before creating a result directory.
    rows, signal_result = load_verified_signal_stream(pack_dir, raw_dir, normal_dir,
                                                      score_dir, signal_dir)
    baseline = load_baseline_summary(baseline_dir)
    cfg, replay, market, agents, no_text_steps, baskets, market_inputs = _load_inputs(config_path)
    _check_cohort(pack_dir, config_path, no_text_steps, rows)
    if (baseline["run_id"] != cfg["run_id"]
            or baseline["market_dataset_id"] != market["market_dataset_id"]
            or baseline["baskets"] != baskets or baseline["cases"] != cfg["cases"]
            or baseline["stocks"] != len(no_text_steps)
            or baseline["paths"] != len(baskets) * len(cfg["cases"])):
        raise ValueError("archived no-text control differs from the frozen portfolio design")
    control_paths = json.loads((baseline_dir / "portfolio_no_text_paths.json").read_text(encoding="utf-8"))
    controls = {row["path_id"]: row for row in control_paths}
    if len(controls) != baseline["paths"] or len(control_paths) != len(controls):
        raise ValueError("archived no-text paths have missing or duplicate identities")
    source_paths = {Path(name) for name in market_inputs}
    source_paths.update((baseline_dir / name for name in (
        "portfolio_no_text_manifest.json", "portfolio_no_text_summary.json",
        "portfolio_no_text_paths.json", "portfolio_no_text_ledger.jsonl.gz")))
    source_paths.update((directory / name for directory, names in (
        (pack_dir, ("annotation_manifest.json", "annotation_items.jsonl")),
        (raw_dir, ("model_run_manifest.json", "model_raw_outputs.jsonl")),
        (normal_dir, ("normalization_manifest.json", "model_predictions.jsonl")),
        (score_dir, ("independent_comparison_manifest.json", "independent_comparison.json")),
        (signal_dir, ("agent_signal_manifest.json", "agent_signal_rows.jsonl",
                      "agent_signal_result.json", "agent_signal_report.md"))) for name in names))
    inputs = {str(path): file_sha256(path) for path in sorted(source_paths)}
    code = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
    if (any(output_dir == path or output_dir in path.parents or path in output_dir.parents
            for path in paths[:-1])
            or any(output_dir == path or output_dir in path.parents for path in source_paths)
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("v3 portfolio output must be a new directory separate from sources")
    joined = {asset: join_steps(steps, asset, rows) for asset, steps in no_text_steps.items()}
    no_reply = set(joined) - {row["stock_code"] for row in rows}
    if (len(no_reply) != 24 or any(any(step["semantic_item_count"] or step["text_signal"]
                                   or step["text_uncertainty"] for step in joined[asset])
                                for asset in no_reply)):
        raise AssertionError("no-reply companies acquired a text signal")
    paired, audited_days = [], 0
    for basket_index, assets in enumerate(baskets):
        basket = {asset: joined[asset] for asset in assets}
        for case in cfg["cases"]:
            path_id = f"{basket_index}:{case['case_id']}"
            control = simulate_portfolio(basket, agents, cfg["core"], cfg["background"],
                                         cfg["venue"], cfg["feedback_parameters"], case, False)
            active = simulate_portfolio(basket, agents, cfg["core"], cfg["background"],
                                        cfg["venue"], cfg["feedback_parameters"], case, True)
            if {"path_id": path_id, "basket_index": basket_index,
                "case_id": case["case_id"], **control["summary"]} != controls.get(path_id):
                raise AssertionError(f"no-text rerun differs from archived control: {path_id}")
            audited_days += _audit_path(control, agents, cfg, assets, case)
            audited_days += _audit_path(active, agents, cfg, assets, case)
            effects = {asset: sum(step["text_signal"] != 0 or step["text_uncertainty"] != 0
                                  for step in basket[asset]) for asset in assets}
            if not any(effects.values()) and {key: value for key, value in active["summary"].items()
                                              if key not in {"use_text", "trace_sha256"}} != {
                    key: value for key, value in control["summary"].items()
                    if key not in {"use_text", "trace_sha256"}}:
                raise AssertionError("all-zero text basket changed the portfolio")
            paired.append({"path_id": path_id, "basket_index": basket_index,
                           "case_id": case["case_id"], "assets": assets,
                           "signal_days_by_asset": effects,
                           "role_wealth_difference_multiple": {
                               role: active["summary"]["role_wealth_multiple"][role]
                               - control["summary"]["role_wealth_multiple"][role]
                               for role in ("aggressive", "conservative", "institutional")},
                           "text": active["summary"], "no_text": control["summary"]})
    if (len(paired) != len(controls) or any(file_sha256(Path(name)) != digest
                                          for name, digest in inputs.items())
            or code != {name: file_sha256(path) for name, path in CODE_PATHS.items()}):
        raise RuntimeError("portfolio source, path coverage or code changed during calculation")
    identity = {"inputs": inputs, "code_sha256": code}
    experiment_id = canonical_hash(identity)
    summary = {"pipeline_version": VERSION, "experiment_id": experiment_id,
               "baseline_experiment_id": baseline["experiment_id"],
               "pack_experiment_id": signal_result["pack_experiment_id"],
               "model_id": signal_result["model_id"], "gate": signal_result["gate"],
               "market_dataset_id": market["market_dataset_id"], "stocks": len(joined),
               "reply_stocks": len(rows), "no_reply_stocks": len(no_reply),
               "baskets": len(baskets), "paired_paths": len(paired),
               "audited_portfolio_days": audited_days,
               "dates": {"start": replay["start_date"], "end": replay["end_date"]},
               "by_case": [{"case_id": case["case_id"], "paths": len(selected),
                            "text_affected_paths": sum(any(row["signal_days_by_asset"].values()) for row in selected),
                            "role_mean_wealth_difference_multiple": {role: statistics.mean(
                                row["role_wealth_difference_multiple"][role] for row in selected)
                                for role in ("aggressive", "conservative", "institutional")}}
                           for case in cfg["cases"]
                           for selected in [[row for row in paired if row["case_id"] == case["case_id"]]]],
               "interpretation": "Controlled hypothetical Agent response to independently gated text; not a market forecast, causal effect, or calibrated real-investor result."}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        (stage / "v3_portfolio_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (stage / "v3_portfolio_paths.json").write_text(
            json.dumps(paired, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        report = ["# 武汉独立公司 v3 文本/无文本组合配对", "",
                  f"固定 {len(joined)} 家公司、{len(rows)} 家可见回复、{len(no_reply)} 家无可见回复；"
                  f"{len(paired)} 对组合路径、{audited_days} 个双路径组合日经账本重建。",
                  "同一篮子、资金条件、Agent 参数、日历与停牌约束；每条无文本路径与已归档基线精确一致。",
                  "只有独立公司 AI 参考评分的所有预设门槛复核通过，文本信号才进入 Agent。", "",
                  "| 资金条件 | 配对路径 | 有文本作用路径 | 激进型平均财富差 | 保守型平均财富差 | 机构型平均财富差 |",
                  "|---|---:|---:|---:|---:|---:|"]
        for group in summary["by_case"]:
            values = group["role_mean_wealth_difference_multiple"]
            report.append(f"| {group['case_id']} | {group['paths']} | {group['text_affected_paths']} | "
                          f"{values['aggressive']:+.6f} | {values['conservative']:+.6f} | "
                          f"{values['institutional']:+.6f} |")
        report += ["", "价格由示意双边撮合内生生成，真实行情只用于样本、时点和停牌代理。",
                   "AI 单人盲复核是参考而非人工金标准；财富差不证明预测收益或监管预警能力。", ""]
        (stage / "v3_portfolio_report.md").write_text("\n".join(report), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "experiment_id": experiment_id, **identity,
                    "artifacts": {name: {"sha256": file_sha256(stage / name)} for name in sorted(ARTIFACTS)}}
        (stage / "v3_portfolio_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return summary


def load_summary(directory: Path) -> dict:
    """Verify archived source, code and result bytes before reading a paired summary."""
    directory = directory.resolve()
    manifest = json.loads((directory / "v3_portfolio_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION
            or manifest.get("code_sha256") != {name: file_sha256(path) for name, path in CODE_PATHS.items()}
            or not isinstance(manifest.get("inputs"), dict) or not manifest["inputs"]
            or any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items())
            or manifest.get("experiment_id") != canonical_hash({
                "inputs": manifest["inputs"], "code_sha256": manifest["code_sha256"]})
            or manifest.get("artifacts") != {name: {"sha256": file_sha256(directory / name)}
                                                for name in sorted(ARTIFACTS)}):
        raise ValueError("v3 portfolio source, code or artifact differs from its manifest")
    summary = json.loads((directory / "v3_portfolio_summary.json").read_text(encoding="utf-8"))
    if (summary.get("pipeline_version") != VERSION
            or summary.get("experiment_id") != manifest["experiment_id"]
            or summary.get("gate", {}).get("passed") is not True):
        raise ValueError("v3 portfolio summary identity or gate differs")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "baseline_dir", "pack_dir", "raw_dir", "normal_dir", "score_dir", "signal_dir"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_ablation(*(getattr(args, name) for name in (
        "config", "baseline_dir", "pack_dir", "raw_dir", "normal_dir", "score_dir", "signal_dir")),
                          args.output_dir)
    print(json.dumps({name: result[name] for name in ("stocks", "reply_stocks", "paired_paths",
                                                    "audited_portfolio_days", "by_case")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
