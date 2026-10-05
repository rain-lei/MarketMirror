"""Run a source-bound no-text portfolio baseline for the pre-event Wuhan cohort."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import statistics
import tempfile
import zlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.market_data import VERSION as MARKET_VERSION
from ..data_pipeline.provenance import file_sha256
from ..semantic.annotation_pack import canonical_hash as canonical_cohort_hash
from .agents import AgentParameters
from .feedback_auction import validate_feedback, validate_scenario
from .historical_replay import load_config as load_replay_config, prepare_steps
from .participant_market import validate_background
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import initial_portfolio, simulate_portfolio, validate_case
from .semantic_auction_experiment import _validate_venue
from .semantic_memory_sensitivity import canonical_hash
from .semantic_signal_join import join_steps


VERSION = "wuhan-pre-event-no-text-portfolio-v1"
ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = {name: ROOT / name for name in (
    "simulation/wuhan_portfolio_baseline.py", "simulation/portfolio_market.py",
    "simulation/portfolio_auction.py", "simulation/portfolio_audit.py",
    "simulation/call_auction.py", "simulation/audit_auction.py",
    "simulation/feedback_auction.py", "simulation/participant_market.py",
    "simulation/agents.py", "simulation/historical_replay.py",
    "simulation/semantic_signal_join.py", "simulation/semantic_auction_experiment.py",
    "baselines/run_experiments.py", "data_pipeline/market_data.py")}
ARTIFACTS = {"portfolio_no_text_summary.json", "portfolio_no_text_paths.json",
             "portfolio_no_text_ledger.jsonl.gz", "portfolio_no_text_report.md"}
REQUIRED = {"run_id", "market_replay_config", "download_manifest", "universe_config",
            "basket_size", "venue", "feedback_parameters", "core", "background", "cases"}


def _validate_frozen_cohort(universe: dict, replay_codes: list[str]) -> list[str]:
    codes = universe.get("stock_codes")
    selection = universe.get("selection")
    if selection is None:
        selection = universe.get("selection_audit")
    if (not isinstance(codes, list) or not codes or len(set(codes)) != len(codes)
            or codes != replay_codes or not isinstance(selection, dict)):
        raise ValueError("portfolio cohort differs from the frozen pre-event question universe")
    rule = selection.get("selection_rule")
    if rule == "sha256_rank_of_pre_event_question_active_issuers":
        valid = selection.get("sample_size") == len(codes)
    elif rule == "sha256_rank_after_excluding_prior_development_and_v3_validation_cohorts":
        valid = (selection.get("sample_size") == len(codes)
                 and selection.get("selected_stock_codes_sha256") == canonical_cohort_hash(codes)
                 and selection.get("status") == "membership_frozen_text_unreviewed_model_unrun")
    else:
        valid = False
    if not valid:
        raise ValueError("portfolio cohort selection metadata or membership hash is invalid")
    return codes


def _load_inputs(config_path: Path):
    config_path = config_path.resolve()
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if (not isinstance(cfg, dict) or set(cfg) != REQUIRED
            or any(not isinstance(cfg[name], str) or not cfg[name].strip()
                   for name in ("run_id", "market_replay_config", "download_manifest", "universe_config"))
            or type(cfg["basket_size"]) is not int or not 2 <= cfg["basket_size"] <= 8):
        raise ValueError("portfolio baseline requires a complete fixed configuration")
    replay_path = (config_path.parent / cfg["market_replay_config"]).resolve()
    replay = load_replay_config(replay_path)
    universe_path = (config_path.parent / cfg["universe_config"]).resolve()
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    codes = _validate_frozen_cohort(universe, replay["stock_codes"])
    market_path = (replay_path.parent / replay["market_manifest"]).resolve()
    market_csv = market_path.parent / "market_daily.csv"
    market = json.loads(market_path.read_text(encoding="utf-8"))
    if (market.get("pipeline_version") != MARKET_VERSION or market.get("data_kind") != "observed"
            or set(market.get("series", {})) != set(codes)
            or market.get("artifacts", {}).get("market_daily.csv", {}).get("sha256") != file_sha256(market_csv)):
        raise ValueError("prepared observed market differs from the pre-event cohort")
    download_path = (config_path.parent / cfg["download_manifest"]).resolve()
    download = json.loads(download_path.read_text(encoding="utf-8"))
    symbols = {f"{'sh' if code[0] == '6' else 'sz'}.{code}" for code in codes}
    if (download.get("provider") != "BaoStock" or set(download.get("source_symbols", [])) != symbols
            or download.get("benchmark_symbol") != market["settings"]["benchmark_id"]
            or download.get("end_date") < replay["end_date"]):
        raise ValueError("download identity or range differs from the frozen market panel")
    supplemental = download.get("supplemental_sources", [])
    if not isinstance(supplemental, list):
        raise ValueError("supplemental market sources must be a list")
    supplemental_codes = set()
    for source in supplemental:
        if (not isinstance(source, dict) or source.get("provider") != "Sina"
                or source.get("stock_code") != "200468" or source.get("symbol") != "sz.200468"
                or source.get("adjustment_basis") != "unadjusted_close_to_close_price_return"
                or source.get("response_file") not in download.get("artifacts", {})
                or source.get("capture_manifest_file") not in download.get("artifacts", {})):
            raise ValueError("supplemental source identity or declared return basis is invalid")
        if source["stock_code"] in supplemental_codes or source["stock_code"] not in codes:
            raise ValueError("supplemental source repeats or escapes the frozen cohort")
        supplemental_codes.add(source["stock_code"])
        if (download["artifacts"][source["response_file"]].get("sha256") != source.get("response_sha256")
                or download["artifacts"][source["capture_manifest_file"]].get("sha256")
                != source.get("capture_manifest_sha256")):
            raise ValueError("supplemental source hashes differ from the composite download manifest")
    mixed_basis = market["settings"].get("stock_return_basis") == "mixed_adjusted_and_unadjusted_price_return"
    if mixed_basis != bool(supplemental_codes):
        raise ValueError("declared mixed stock-return basis differs from supplemental sources")
    declared_config = market.get("config_path")
    if not isinstance(declared_config, str) or not Path(declared_config).is_absolute():
        raise ValueError("prepared market has no absolute import config path")
    market_config = Path(declared_config).resolve()
    if (market.get("config_sha256") != file_sha256(market_config)
            or market.get("settings") != json.loads(market_config.read_text(encoding="utf-8"))):
        raise ValueError("prepared market import settings changed")
    source_paths = {config_path, replay_path, universe_path, market_path, market_csv,
                    download_path, market_config}
    for name, info in download["artifacts"].items():
        path = download_path.parent / name
        if file_sha256(path) != info["sha256"]:
            raise ValueError(f"download artifact changed: {name}")
        source_paths.add(path)
    for info in market["inputs"].values():
        path = Path(info["path"]).resolve()
        if path.parent != download_path.parent or file_sha256(path) != info["sha256"]:
            raise ValueError("prepared market source changed")
        source_paths.add(path)
    quote_checks = {row["symbol"].split(".")[1]: row for row in download["quote_checks"]
                    if row["symbol"] != download["benchmark_symbol"]}
    if set(quote_checks) != set(codes):
        raise ValueError("quote checks do not cover the fixed stock universe")
    _validate_venue(cfg["venue"])
    validate_feedback(cfg["feedback_parameters"])
    validate_scenario(cfg["core"], cfg["venue"]["lot_size"])
    validate_background(cfg["background"], cfg["venue"]["lot_size"])
    if cfg["core"]["feedback_mode"] != "endogenous" or cfg["background"]["mode"] != "active":
        raise ValueError("portfolio baseline needs endogenous feedback and finite active background")
    cases = cfg["cases"]
    if (not isinstance(cases, list) or len(cases) != 2
            or {case.get("cash_mode") for case in cases} != {"shared", "separated"}
            or len({case.get("case_id") for case in cases}) != 2):
        raise ValueError("portfolio baseline requires paired shared and separated cash controls")
    for case in cases:
        validate_case(case)
    if cases[0]["institutional_asset_cap"] != cases[1]["institutional_asset_cap"]:
        raise ValueError("paired cash controls must have the same concentration limit")
    if len(codes) % cfg["basket_size"]:
        raise ValueError("fixed baskets would omit part of the pre-event cohort")
    agents = [AgentParameters(**row) for row in replay["agents"]]
    groups = _load_market(market_csv, market)
    joined = {}
    for code in codes:
        steps = prepare_steps(groups[code], replay["start_date"], replay["end_date"],
                              replay["momentum_sessions"], replay["volatility_sessions"])
        joined[code] = join_steps(steps, code, [])
        suspended = set(quote_checks[code]["suspended_sessions"])
        for step in joined[code]:
            step["execution_available"] = step["execution_reference_date"] not in suspended
    sorted_codes = sorted(codes)
    baskets = [sorted_codes[index:index + cfg["basket_size"]]
               for index in range(0, len(sorted_codes), cfg["basket_size"])]
    inputs = {str(path): file_sha256(path) for path in sorted(source_paths)}
    return cfg, replay, market, agents, joined, baskets, inputs


def _initial_audit_state(agents, cfg, assets, case):
    _, accounts, _, _ = initial_portfolio(agents, cfg["core"], cfg["background"], assets,
                                           case, cfg["venue"])
    return {"accounts": {name: asdict(account) for name, account in accounts.items()},
            "prices": {asset: cfg["venue"]["price_start_minor"] for asset in assets},
            "fee_pool_minor": 0,
            "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
            "initial_shares": {asset: sum(account.shares[asset] for account in accounts.values())
                               for asset in assets}}


def run_baseline(config_path: Path, output_dir: Path, progress=None) -> dict:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    cfg, replay, market, agents, joined, baskets, inputs = _load_inputs(config_path)
    if (any(output_dir == Path(path) or output_dir in Path(path).parents for path in inputs)
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("portfolio output must be a new directory separate from inputs")
    code = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
    runtime = {"python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION}
    identity = {"inputs": inputs, "code_sha256": code, "runtime": runtime}
    experiment_id = canonical_hash(identity)
    paths, ledger_rows = [], 0
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        ledger_path = staging / "portfolio_no_text_ledger.jsonl.gz"
        with ledger_path.open("wb") as binary, gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as ledger:
            for basket_index, assets in enumerate(baskets):
                for case in cfg["cases"]:
                    result = simulate_portfolio({asset: joined[asset] for asset in assets}, agents,
                                                cfg["core"], cfg["background"], cfg["venue"],
                                                cfg["feedback_parameters"], case, False)
                    prior = _initial_audit_state(agents, cfg, assets, case)
                    path_id = f"{basket_index}:{case['case_id']}"
                    for session, day in enumerate(result["trace"]):
                        if any(observation["text_signal"] != 0 or observation["text_uncertainty"] != 0
                               or observation["text_evidence"] != "text:disabled"
                               for observation in day["observations"].values()):
                            raise AssertionError("no-text portfolio baseline contains a text signal")
                        prior = audit_portfolio_day(day, prior, cfg["venue"], session)
                        ledger.write((json.dumps({"path_id": path_id, **day}, ensure_ascii=False,
                                                 separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                        ledger_rows += 1
                    saved = result["summary"]
                    if (saved["final_prices_minor"] != prior["prices"]
                            or saved["fee_pool_minor"] != prior["fee_pool_minor"]
                            or any(any(saved["accounts"][name][field] != account[field]
                                       for field in ("wallets", "shares", "sellable"))
                                   for name, account in prior["accounts"].items())):
                        raise AssertionError("portfolio summary differs from independently reconstructed wallets")
                    paths.append({"path_id": path_id, "basket_index": basket_index,
                                  "case_id": case["case_id"], **saved})
                if progress:
                    progress(basket_index + 1, len(baskets))
        grouped = []
        for case in cfg["cases"]:
            selected = [row for row in paths if row["case_id"] == case["case_id"]]
            requested = sum(row["strategy_requested"] for row in selected)
            accepted = sum(row["strategy_accepted"] for row in selected)
            filled = sum(row["strategy_filled"] for row in selected)
            grouped.append({"case_id": case["case_id"], "cash_mode": case["cash_mode"],
                            "baskets": len(selected), "strategy_requested": requested,
                            "strategy_accepted": accepted, "strategy_filled": filled,
                            "strategy_fill_fraction": filled / accepted if accepted else 0.0,
                            "cash_clipped_orders": sum(row["cash_clipped_orders"] for row in selected),
                            "mean_final_price_multiple": statistics.mean(
                                price / cfg["venue"]["price_start_minor"] for row in selected
                                for price in row["final_prices_minor"].values()),
                            "role_wealth_multiple": {role: statistics.mean(
                                row["role_wealth_multiple"][role] for row in selected)
                                for role in ("aggressive", "conservative", "institutional")}})
        if (any(file_sha256(Path(path)) != digest for path, digest in inputs.items())
                or code != {name: file_sha256(path) for name, path in CODE_PATHS.items()}):
            raise RuntimeError("portfolio code or source changed during calculation")
        summary = {"pipeline_version": VERSION, "experiment_id": experiment_id,
                   "run_id": cfg["run_id"], "market_dataset_id": market["market_dataset_id"],
                   "stocks": len(joined), "baskets": baskets, "cases": cfg["cases"],
                   "paths": len(paths), "ledger_rows": ledger_rows,
                   "asset_call_rows": ledger_rows * cfg["basket_size"],
                   "audited_portfolio_days": ledger_rows,
                   "dates": {"start": replay["start_date"], "end": replay["end_date"]},
                   "grouped": grouped,
                   "interpretation": "Endogenous hypothetical portfolio baseline conditioned only on historical calendar and suspension proxies; not a fit to observed prices."}
        (staging / "portfolio_no_text_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (staging / "portfolio_no_text_paths.json").write_text(
            json.dumps(paths, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        report = ["# 武汉事前公司样本：无文本多资产机制基线", "",
                  f"固定 {len(joined)} 家公司、{len(baskets)} 个代码排序三股篮子，"
                  f"{len(paths)} 条路径、{ledger_rows:,} 个组合日。",
                  "历史行情只确定交易日、t-2 信息截止日、停牌代理和样本；"
                  "模拟价格来自有限双边撮合后的自身反馈，不拟合历史价格。", "",
                  "| 资金条件 | 策略成交/接受 | 现金裁剪订单 | 平均终价/初价 |",
                  "|---|---:|---:|---:|"]
        for group in grouped:
            report.append(f"| {group['case_id']} | {group['strategy_fill_fraction']:.2%} | "
                          f"{group['cash_clipped_orders']} | {group['mean_final_price_multiple']:.4f} |")
        report += ["", "每个组合日均经独立钱包、股份及双边成交重建复核。"
                   "现金、库存、报价、背景需求和机构上限沿用先前示意机制参数，未经真实订单或持仓校准。",
                   "代码排序篮子不代表行业或真实机构投资组合；本实验不包含文本，"
                   "不能据此估计文本增益、政策因果效应或监管预警能力。", ""]
        (staging / "portfolio_no_text_report.md").write_text("\n".join(report), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "experiment_id": experiment_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(), **identity,
                    "artifacts": {name: {"sha256": file_sha256(staging / name)} for name in sorted(ARTIFACTS)}}
        (staging / "portfolio_no_text_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return summary


def load_summary(directory: Path) -> dict:
    manifest = json.loads((directory / "portfolio_no_text_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION
            or manifest.get("code_sha256") != {name: file_sha256(path) for name, path in CODE_PATHS.items()}
            or canonical_hash({name: manifest[name] for name in ("inputs", "code_sha256", "runtime")})
            != manifest.get("experiment_id")
            or set(manifest.get("artifacts", {})) != ARTIFACTS
            or any(file_sha256(directory / name) != entry["sha256"]
                   for name, entry in manifest["artifacts"].items())
            or any(file_sha256(Path(path)) != digest for path, digest in manifest["inputs"].items())):
        raise ValueError("portfolio baseline source, code or artifact changed")
    summary = json.loads((directory / "portfolio_no_text_summary.json").read_text(encoding="utf-8"))
    if summary.get("experiment_id") != manifest["experiment_id"] or summary.get("pipeline_version") != VERSION:
        raise ValueError("portfolio baseline summary identity differs")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = run_baseline(args.config, args.output_dir,
                           lambda done, total: print(f"Processed {done}/{total} baskets", flush=True)
                           if done % 10 == 0 or done == total else None)
    print(json.dumps({name: summary[name] for name in
                      ("stocks", "paths", "ledger_rows", "asset_call_rows", "grouped")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
