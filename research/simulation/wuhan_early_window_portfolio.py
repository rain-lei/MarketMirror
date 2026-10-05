"""Run an isolated early-window Wuhan portfolio baseline with warm-start volatility."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import platform
import statistics
import tempfile
import zlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import simulate_portfolio
from .semantic_memory_sensitivity import canonical_hash
from .semantic_signal_join import join_steps
from .wuhan_portfolio_baseline import _initial_audit_state, _load_inputs


VERSION = "wuhan-early-window-warm-start-portfolio-v2"
ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = {name: ROOT / name for name in (
    "simulation/wuhan_early_window_portfolio.py",
    "simulation/wuhan_portfolio_baseline.py",
    "simulation/portfolio_market.py",
    "simulation/portfolio_auction.py",
    "simulation/portfolio_audit.py",
    "simulation/audit_background.py",
    "simulation/audit_auction.py",
    "simulation/call_auction.py",
    "simulation/feedback_auction.py",
    "simulation/participant_market.py",
    "simulation/agents.py",
    "simulation/semantic_signal_join.py",
    "simulation/semantic_auction_experiment.py",
    "simulation/historical_replay.py",
    "baselines/run_experiments.py",
    "data_pipeline/market_data.py",
)}
LEDGER_NAME = "early_window_ledger.jsonl.gz"
ARTIFACTS = {"early_window_summary.json", "early_window_paths.json",
             "early_window_report.md", LEDGER_NAME}


def _warm_prepare_steps(rows: list[Any], start_date: str, end_date: str,
                        momentum_sessions: int, volatility_sessions: int) -> list[dict[str, Any]]:
    """Prepare causal steps while padding only unavailable pre-listing volatility history.

    A stock must still have two prior observed sessions for the t-2 information cutoff.
    Padding is limited to the volatility estimate and is recorded in each step.
    """
    ordered = sorted(rows, key=lambda row: row.trade_date)
    if not ordered or len({row.trade_date for row in ordered}) != len(ordered):
        raise ValueError("warm-start replay requires unique ordered market sessions")
    if any(not all(math.isfinite(v) and v > -1 for v in (r.stock_return, r.market_return)) for r in ordered):
        raise ValueError("warm-start replay needs finite simple returns strictly above -100 percent")
    steps = []
    for i, row in enumerate(ordered):
        day = row.trade_date.isoformat()
        if not start_date <= day <= end_date:
            continue
        if i < 2:
            raise ValueError("warm-start replay start lacks a strictly lagged information cutoff")
        stock_history = [r.stock_return for r in ordered[max(0, i - 1 - volatility_sessions):i - 1]]
        market_history = [r.market_return for r in ordered[max(0, i - 1 - volatility_sessions):i - 1]]
        if len(stock_history) < 2 or len(market_history) < 2:
            raise ValueError("warm-start replay needs at least two prior returns")
        missing = max(0, volatility_sessions - len(stock_history))
        stock_vol = max(1e-6, min(1.0, statistics.stdev(([0.0] * missing) + stock_history)))
        market_vol = max(1e-6, statistics.stdev(([0.0] * missing) + market_history))
        momentum = sum(market_history[-momentum_sessions:])
        signal = math.tanh(momentum / (market_vol * math.sqrt(momentum_sessions)))
        steps.append({"trade_date": day, "signal_cutoff_date": ordered[i - 2].trade_date.isoformat(),
                      "execution_reference_date": ordered[i - 1].trade_date.isoformat(),
                      "observed_return": row.stock_return, "market_signal": signal,
                      "estimated_volatility": stock_vol,
                      "warm_start_missing_sessions": missing})
    if not steps:
        raise ValueError("warm-start replay date range has no market sessions")
    return steps


def _load_warm_inputs(config_path: Path, start_date: str, end_date: str):
    cfg, replay, market, agents, old_joined, baskets, inputs = _load_inputs(config_path)
    market_path = (config_path.parent / replay["market_manifest"]).resolve()
    groups = _load_market(market_path.parent / "market_daily.csv", market)
    download_path = (config_path.parent / cfg["download_manifest"]).resolve()
    download = json.loads(download_path.read_text(encoding="utf-8"))
    suspended = {row["symbol"].split(".")[1]: set(row["suspended_sessions"])
                 for row in download["quote_checks"] if row["symbol"] != download["benchmark_symbol"]}
    joined = {}
    for code in replay["stock_codes"]:
        steps = _warm_prepare_steps(groups[code], start_date, end_date,
                                    replay["momentum_sessions"], replay["volatility_sessions"])
        joined[code] = join_steps(steps, code, [])
        for step in joined[code]:
            step["execution_available"] = step["execution_reference_date"] not in suspended[code]
    # The old baseline uses the strict estimator.  This comparison proves the
    # warm-start implementation becomes byte-for-byte equivalent after the
    # strict 20-session history is available.
    overlap = {}
    for code in replay["stock_codes"]:
        strict = old_joined[code]
        warm = {step["trade_date"]: step for step in joined[code]}
        overlap[code] = all(
            {key: strict_step[key] for key in strict_step if key != "warm_start_missing_sessions"}
            == {key: warm[strict_step["trade_date"]][key] for key in strict_step if key != "warm_start_missing_sessions"}
            for strict_step in strict if strict_step["trade_date"] in warm)
    if not all(overlap.values()):
        raise AssertionError("warm-start steps differ after the strict history is available")
    return cfg, replay, market, agents, joined, baskets, inputs, overlap


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
        raise AssertionError("warm-start portfolio path differs from reconstructed wallets")
    return len(result["trace"])


def run_early_window(config_path: Path, output_dir: Path,
                     start_date: str = "2020-01-23", end_date: str = "2020-03-31") -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    try:
        date.fromisoformat(start_date)
        date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError("warm-start window dates must be ISO dates") from exc
    if start_date > end_date:
        raise ValueError("warm-start window is reversed")
    cfg, replay, market, agents, joined, baskets, inputs, overlap = _load_warm_inputs(
        config_path, start_date, end_date)
    if (any(output_dir == Path(name) or output_dir in Path(name).parents
            or Path(name) in output_dir.parents for name in inputs)
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("warm-start output must be a new empty directory")
    code = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
    runtime = {"python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION}
    identity = {"inputs": inputs, "code_sha256": code, "runtime": runtime,
                "window": {"start": start_date, "end": end_date,
                           "volatility_padding": "zero_returns_only_when_fewer_than_20_pre_cutoff_sessions"}}
    experiment_id = canonical_hash(identity)
    paths, audited_days = [], 0
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        ledger_path = stage / LEDGER_NAME
        with ledger_path.open("wb") as binary, gzip.GzipFile(
                filename="", mode="wb", fileobj=binary, mtime=0) as ledger:
            for basket_index, assets in enumerate(baskets):
                for case in cfg["cases"]:
                    result = simulate_portfolio({asset: joined[asset] for asset in assets}, agents,
                                                cfg["core"], cfg["background"], cfg["venue"],
                                                cfg["feedback_parameters"], case, False)
                    audited_days += _audit_path(result, agents, cfg, assets, case)
                    path_id = f"{basket_index}:{case['case_id']}"
                    for day in result["trace"]:
                        ledger.write((json.dumps({"path_id": path_id, **day}, ensure_ascii=False,
                                                 separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                    summary = result["summary"]
                    paths.append({"path_id": path_id, "basket_index": basket_index,
                                  "case_id": case["case_id"], **summary})
        if (any(file_sha256(Path(name)) != digest for name, digest in inputs.items())
                or code != {name: file_sha256(path) for name, path in CODE_PATHS.items()}):
            raise RuntimeError("warm-start source or code changed during calculation")
        payload = {"pipeline_version": VERSION, "experiment_id": experiment_id,
                   "run_id": cfg["run_id"], "market_dataset_id": market["market_dataset_id"],
                   "stocks": len(joined), "baskets": baskets, "paths": len(paths),
                   "ledger_rows": audited_days,
                   "asset_call_rows": audited_days * cfg["basket_size"],
                   "audited_portfolio_days": audited_days,
                   "dates": {"start": start_date, "end": end_date},
                   "strict_overlap_step_parity": all(overlap.values()),
                   "strict_overlap_stocks": sum(overlap.values()),
                   "warm_start_stocks": sum(any(step["warm_start_missing_sessions"] for step in joined[code])
                                             for code in joined),
                   "interpretation": "Early-window mechanism baseline with causal warm-start volatility; no text or causal market claim."}
        (stage / "early_window_summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (stage / "early_window_paths.json").write_text(
            json.dumps(paths, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        report = ["# 武汉事前早期窗口：暖启动无文本组合基线", "",
                  f"窗口 {start_date} 至 {end_date}，固定 {len(joined)} 家公司、{len(baskets)} 个三股篮子、{len(paths)} 条路径，"
                  f"{audited_days} 个组合日完成账本重建。",
                  "2020-01-23 起允许上市较晚的股票使用截至 t-2 已有的至少两条历史收益；不足 20 条的波动率估计只补零收益。",
                  f"保存了 {audited_days * cfg['basket_size']:,} 次资产竞价的完整压缩日账本；"
                  f"严格 20 日历史区间有 {sum(overlap.values())}/{len(overlap)} 家公司的步骤逐字段一致。",
                  "该输出仍是内生撮合机制基线，不把历史价格当模拟价格，也不含文本信号。", ""]
        (stage / "early_window_report.md").write_text("\n".join(report), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "experiment_id": experiment_id, **identity,
                    "artifacts": {name: {"sha256": file_sha256(stage / name)} for name in sorted(ARTIFACTS)}}
        (stage / "early_window_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return payload


def load_summary(directory: Path) -> dict[str, Any]:
    """Load only a source- and code-verified warm-start result."""
    directory = directory.resolve()
    manifest = json.loads((directory / "early_window_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION
            or manifest.get("code_sha256") != {name: file_sha256(path) for name, path in CODE_PATHS.items()}
            or not isinstance(manifest.get("inputs"), dict)
            or any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items())
            or manifest.get("experiment_id") != canonical_hash({
                "inputs": manifest["inputs"], "code_sha256": manifest["code_sha256"],
                "runtime": manifest["runtime"], "window": manifest["window"]})
            or manifest.get("artifacts") != {name: {"sha256": file_sha256(directory / name)}
                                                for name in sorted(ARTIFACTS)}):
        raise ValueError("warm-start source, code or artifact differs from its manifest")
    summary = json.loads((directory / "early_window_summary.json").read_text(encoding="utf-8"))
    if (summary.get("pipeline_version") != VERSION
            or summary.get("experiment_id") != manifest["experiment_id"]
            or summary.get("strict_overlap_step_parity") is not True):
        raise ValueError("warm-start summary identity or overlap parity differs")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", default="2020-01-23")
    parser.add_argument("--end-date", default="2020-03-31")
    args = parser.parse_args()
    result = run_early_window(args.config, args.output_dir, args.start_date, args.end_date)
    print(json.dumps({name: result[name] for name in (
        "stocks", "paths", "audited_portfolio_days", "strict_overlap_step_parity",
        "warm_start_stocks")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
