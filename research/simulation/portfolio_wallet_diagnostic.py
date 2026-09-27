"""Replay fixed portfolio order books under matched cash-allocation controls."""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import math
import platform
import statistics
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .audit_portfolio import load_audit
from .portfolio_auction import PortfolioAccount, PortfolioAuction
from .portfolio_audit import audit_portfolio_day
from .portfolio_experiment import experiment_inputs, load_summary
from .portfolio_market import initial_portfolio
from .call_auction import LimitOrder

VERSION = "portfolio-wallet-allocation-diagnostic-v6"
ARTIFACTS = {"wallet_diagnostic_results.json", "wallet_diagnostic_summary.json", "wallet_diagnostic_report.md"}


def load_config(path):
    path = path.resolve()
    cfg = json.loads(path.read_text(encoding="utf-8"))
    expected = {"run_id", "source_run", "source_audit", "source_config", "source_case_id", "sample_sessions", "cash_allocations"}
    if set(cfg) != expected or any(not isinstance(cfg[k], str) or not cfg[k]
                                   for k in ("run_id", "source_run", "source_audit", "source_config", "source_case_id")):
        raise ValueError("wallet diagnostic config fields invalid")
    if (not isinstance(cfg["sample_sessions"], list) or not cfg["sample_sessions"]
            or cfg["sample_sessions"] != sorted(set(cfg["sample_sessions"]))
            or any(type(s) is not int or s < 0 for s in cfg["sample_sessions"])):
        raise ValueError("wallet diagnostic sample sessions must be unique increasing integers")
    allocations = cfg["cash_allocations"]
    expected_allocations = {"equal", *(f"asset_{i}_concentrated_{share}" for i in range(3) for share in (80, 98))}
    if (set(allocations) != expected_allocations
            or any(not isinstance(v, list) or len(v) != 3 or any(type(x) is not int or x <= 0 for x in v)
                   for v in allocations.values())
            or allocations["equal"] != [1, 1, 1]
            or any(allocations[f"asset_{i}_concentrated_{share}"] !=
                   ([share // 10, 1, 1] if i == 0 and share == 80 else
                    [1, share // 10, 1] if i == 1 and share == 80 else
                    [1, 1, share // 10] if i == 2 and share == 80 else
                    [share, 1, 1] if i == 0 else [1, share, 1] if i == 1 else [1, 1, share])
                   for i in range(3) for share in (80, 98))):
        raise ValueError("wallet diagnostic requires equal and counterbalanced 80/10/10 and 98/1/1 controls")
    return path, cfg


def _reallocate(accounts, specs, assets, weights):
    changed = copy.deepcopy(accounts)
    for name, spec in specs.items():
        if spec["kind"] != "strategy":
            continue
        account = changed[name]
        total = sum(account.wallets.values())
        if weights is None:
            account.wallets = {"shared": total}
        else:
            denominator = sum(weights)
            cash = [total * weight // denominator for weight in weights]
            remainder = total - sum(cash)
            cash[max(range(len(weights)), key=lambda i: weights[i])] += remainder
            account.wallets = dict(zip(assets, cash, strict=True))
        account.sellable = dict(account.shares)
        account.validate(assets)
    for name, spec in specs.items():
        if spec["kind"] == "background":
            changed[name].sellable = dict(changed[name].shares)
            changed[name].validate(assets)
    return changed


def _metric(result, specs, assets, prior_prices, prior_accounts):
    strategy = {name for name, spec in specs.items() if spec["kind"] == "strategy"}
    requested = accepted = filled = clipped = 0
    requested_by_asset = {a: 0 for a in assets}
    accepted_by_asset = {a: 0 for a in assets}
    filled_by_asset = {a: 0 for a in assets}
    clipped_by_asset = {a: 0 for a in assets}
    for asset, call in result["asset_calls"].items():
        for order in call["orders"]:
            if order["owner"] not in strategy:
                continue
            requested += order["quantity"]
            accepted += order["accepted_quantity"]
            filled += order["filled_quantity"]
            was_clipped = int("cash_and_fee_reservation" in order["reasons"])
            clipped += was_clipped
            requested_by_asset[asset] += order["quantity"]
            accepted_by_asset[asset] += order["accepted_quantity"]
            filled_by_asset[asset] += order["filled_quantity"]
            clipped_by_asset[asset] += was_clipped
    prices = {a: result["asset_calls"][a]["price_after_minor"] for a in assets}
    accounts = result["accounts"]
    start_nav = sum(sum(prior_accounts[n]["wallets"].values()) +
                    sum(prior_accounts[n]["shares"][a] * prior_prices[a] for a in assets) for n in strategy)
    end_nav = sum(sum(accounts[n]["wallets"].values()) + sum(accounts[n]["shares"][a] * prices[a] for a in assets)
                   for n in strategy)
    return {"strategy_requested": requested, "strategy_accepted": accepted, "strategy_filled": filled,
            "accepted_fill_fraction": filled / accepted if accepted else 0.0,
            "requested_fill_fraction": filled / requested if requested else 0.0,
            "cash_clipped_orders": clipped, "requested_by_asset": requested_by_asset,
            "accepted_by_asset": accepted_by_asset, "filled_by_asset": filled_by_asset,
            "clipped_by_asset": clipped_by_asset, "matched_volume_by_asset": {
                a: result["asset_calls"][a]["matched_volume"] for a in assets},
            "prices_after_minor": prices, "strategy_cash_after_minor": sum(
                sum(accounts[n]["wallets"].values()) for n in strategy),
            "marked_strategy_nav_before_minor": start_nav, "marked_strategy_nav_after_minor": end_nav,
            "marked_strategy_nav_delta_minor": end_nav - start_nav,
            "fee_pool_minor": result["fee_pool_minor"]}


def _aggregate(records, variant_names):
    groups = {}
    by_group = defaultdict(list)
    for row in records:
        by_group[(row["quote_response_bps"], row["use_text"])].append(row)
    for (response, use_text), selected in sorted(by_group.items()):
        variants = {}
        for name in variant_names:
            values = [r["outcomes"][name] for r in selected]
            clips = sum(v["cash_clipped_orders"] for v in values)
            req, acc, fill = (sum(v[k] for v in values) for k in
                              ("strategy_requested", "strategy_accepted", "strategy_filled"))
            nav_diffs = [r["outcomes"][name]["marked_strategy_nav_after_minor"] -
                         r["outcomes"]["shared"]["marked_strategy_nav_after_minor"] for r in selected]
            changed_prices = sum(v["prices_after_minor"] != r["outcomes"]["shared"]["prices_after_minor"]
                                 for r, v in zip(selected, values, strict=True))
            variants[name] = {"snapshots": len(values), "strategy_requested": req, "strategy_accepted": acc,
                              "strategy_filled": fill, "accepted_fill_fraction": fill / acc if acc else 0.0,
                              "requested_fill_fraction": fill / req if req else 0.0,
                              "cash_clipped_orders": clips, "changed_price_snapshots_vs_shared": changed_prices,
                              "mean_strategy_marked_nav_delta_vs_shared_minor": statistics.mean(nav_diffs),
                              "filled_by_asset_position": [sum(v["filled_by_asset"][r["assets"][i]] for v, r in
                                                                     zip(values, selected, strict=True)) for i in range(3)]}
        groups[f"{response}:{int(use_text)}"] = {"quote_response_bps": response, "use_text": use_text,
                                                "sampled_snapshots": len(selected), "variants": variants}
    return list(groups.values())


def verify_group_summaries(records, variant_names, saved_groups):
    grouped = defaultdict(list)
    for row in records:
        grouped[(row["quote_response_bps"], row["use_text"])].append(row)
    expected_keys = {(200, False), (200, True), (500, False), (500, True)}
    if set(grouped) != expected_keys or len(saved_groups) != len(expected_keys):
        raise ValueError("wallet diagnostic group coverage differs")
    saved = {(g["quote_response_bps"], g["use_text"]): g for g in saved_groups}
    if len(saved) != len(saved_groups) or set(saved) != expected_keys:
        raise ValueError("wallet diagnostic summary group keys differ")
    for key, rows in grouped.items():
        group = saved[key]
        if group["sampled_snapshots"] != len(rows) or set(group["variants"]) != set(variant_names):
            raise ValueError("wallet diagnostic summary variant coverage differs")
        for row in rows:
            if (len(row["assets"]) != 3 or set(row["outcomes"]) != set(variant_names)
                    or set(row["cash_allocation_totals_minor"]) != set(variant_names)
                    or set(row["cash_allocation_totals_minor"].values()) != {row["source_cash_minor"]}):
                raise ValueError("wallet diagnostic variants do not preserve source resources")
        for name in variant_names:
            outcomes = [row["outcomes"][name] for row in rows]
            requested = sum(v["strategy_requested"] for v in outcomes)
            accepted = sum(v["strategy_accepted"] for v in outcomes)
            filled = sum(v["strategy_filled"] for v in outcomes)
            exact = {"snapshots": len(rows), "strategy_requested": requested,
                     "strategy_accepted": accepted, "strategy_filled": filled,
                     "cash_clipped_orders": sum(v["cash_clipped_orders"] for v in outcomes),
                     "changed_price_snapshots_vs_shared": sum(
                         v["prices_after_minor"] != row["outcomes"]["shared"]["prices_after_minor"]
                         for row, v in zip(rows, outcomes, strict=True)),
                     "filled_by_asset_position": [sum(row["outcomes"][name]["filled_by_asset"][row["assets"][i]]
                                                       for row in rows) for i in range(3)]}
            saved_variant = group["variants"][name]
            if any(saved_variant[k] != value for k, value in exact.items()):
                raise ValueError("wallet diagnostic summary counts differ from snapshot records")
            approximations = {"accepted_fill_fraction": filled / accepted if accepted else 0.0,
                              "requested_fill_fraction": filled / requested if requested else 0.0,
                              "mean_strategy_marked_nav_delta_vs_shared_minor":
                                  math.fsum(row["outcomes"][name]["marked_strategy_nav_after_minor"] -
                                            row["outcomes"]["shared"]["marked_strategy_nav_after_minor"]
                                            for row in rows) / len(rows)}
            if any(not math.isclose(saved_variant[k], value, rel_tol=1e-12, abs_tol=1e-9)
                   for k, value in approximations.items()):
                raise ValueError("wallet diagnostic summary rates or marked NAV differ from snapshot records")


def run_diagnostic(config_path, output_dir, progress=None):
    config_path, cfg = load_config(config_path)
    root = Path(__file__).resolve().parents[2]
    output_dir = output_dir.resolve()
    source = root / "research_outputs" / cfg["source_run"]
    source_audit = root / "research_outputs" / cfg["source_audit"]
    source_config = root / "research" / "configs" / cfg["source_config"]
    if (output_dir == source or output_dir in source.parents or source in output_dir.parents
            or output_dir == source_audit or output_dir in source_audit.parents or source_audit in output_dir.parents
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("wallet diagnostic output must be a new empty directory outside its sources")
    summary = load_summary(source)
    audit = load_audit(source, source_audit)
    if audit["status"] != "passed" or summary["paths"] != 672 or summary["ledger_rows"] != 83328:
        raise ValueError("source portfolio experiment and full audit must pass first")
    source_cfg, parent, auction_cfg, base, _, _, source_inputs, joined, core, background, baskets = experiment_inputs(source_config)
    target = next((c for c in source_cfg["cases"] if c["case_id"] == cfg["source_case_id"]), None)
    if target != {"case_id": cfg["source_case_id"], "cash_mode": "shared", "institutional_asset_cap": 0.35}:
        raise ValueError("diagnostic source must be the predeclared shared 35% portfolio case")
    if any(s >= len(next(iter(joined.values()))) for s in cfg["sample_sessions"]):
        raise ValueError("diagnostic sample session lies outside the source calendar")
    code_paths = {"portfolio_wallet_diagnostic.py": Path(__file__),
                  "portfolio_auction.py": Path(__file__).with_name("portfolio_auction.py"),
                  "portfolio_audit.py": Path(__file__).with_name("portfolio_audit.py")}
    code_hashes = {k: file_sha256(v) for k, v in code_paths.items()}
    bound_inputs = {"config": file_sha256(config_path), "source_config": file_sha256(source_config),
                    "source_manifest": file_sha256(source / "portfolio_manifest.json"),
                    "source_audit_manifest": file_sha256(source_audit / "portfolio_audit_manifest.json")}
    identity = {"pipeline_version": VERSION, "run_id": cfg["run_id"], "source_experiment_id": summary["experiment_id"],
                "source_audit_version": audit["pipeline_version"], "inputs": bound_inputs, "code_sha256": code_hashes,
                "runtime": {"python": platform.python_version()}}
    agents = [AgentParameters(**a) for a in base["agents"]]
    states, specs_by_path, counters = {}, {}, defaultdict(int)
    records = []
    sample_sessions = set(cfg["sample_sessions"])
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=output_dir))
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        scratch = Path(temporary)
        with gzip.open(source / "portfolio_ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                key = row["path_id"]
                path_bits = key.split(":")
                if len(path_bits) != 4 or path_bits[1] != cfg["source_case_id"]:
                    continue
                basket_index, _, response, text_flag = int(path_bits[0]), path_bits[1], int(path_bits[2]), int(path_bits[3])
                path_key = key
                if path_key not in states:
                    assets = baskets[basket_index]
                    cohort, initial, specs, _ = initial_portfolio(
                        agents, core, background, assets, target, {**auction_cfg["venue"], "quote_response_bps": response})
                    states[path_key] = {n: copy.deepcopy(a) for n, a in initial.items()}
                    specs_by_path[path_key] = specs
                session = counters[path_key]
                if row["portfolio_auction"]["session"] != session or session >= len(joined[baskets[basket_index][0]]):
                    raise ValueError("source path session order differs")
                previous_accounts = {n: copy.deepcopy(a) for n, a in states[path_key].items()}
                states[path_key] = {n: PortfolioAccount(**a) for n, a in row["portfolio_auction"]["accounts"].items()}
                if session not in sample_sessions:
                    counters[path_key] += 1
                    continue
                assets = baskets[basket_index]
                source_auction = row["portfolio_auction"]
                prices = source_auction["prices_before_minor"]
                books = {a: [LimitOrder(o["order_id"], o["owner"], o["side"], o["quantity"],
                                         o["limit_price_minor"], o["sequence"])
                             for o in source_auction["asset_calls"][a]["orders"]] for a in assets}
                available = {a: source_auction["asset_calls"][a]["execution_available"] for a in assets}
                settings = {**auction_cfg["venue"], "quote_response_bps": response}
                scenarios = {"shared": None, **cfg["cash_allocations"]}
                outcomes = {}
                for variant, weights in scenarios.items():
                    accounts = _reallocate(previous_accounts, specs_by_path[path_key], assets, weights)
                    market = PortfolioAuction(accounts, assets, settings)
                    market.prices = dict(prices)
                    market.accounts = copy.deepcopy(accounts)
                    result = market.clear(0, books, available)
                    prior = {"accounts": {n: {"wallets": dict(a.wallets), "shares": dict(a.shares), "sellable": dict(a.sellable)}
                                          for n, a in accounts.items()}, "prices": dict(prices), "fee_pool_minor": 0,
                             "initial_cash_minor": sum(sum(a.wallets.values()) for a in accounts.values()),
                             "initial_shares": {a: sum(x.shares[a] for x in accounts.values()) for a in assets}}
                    for a in accounts.values():
                        a.sellable = dict(a.shares)
                    checked = audit_portfolio_day({**{k: row[k] for k in ("trade_date", "signal_cutoff_date", "execution_reference_date")},
                                                   "portfolio_auction": result}, prior, settings, 0)
                    expected = {"accounts": result["accounts"], "prices": {a: result["asset_calls"][a]["price_after_minor"] for a in assets},
                                "fee_pool_minor": result["fee_pool_minor"], "initial_cash_minor": prior["initial_cash_minor"],
                                "initial_shares": prior["initial_shares"]}
                    if checked != expected:
                        raise ValueError("one-day wallet reallocation differs from independent settlement reconstruction")
                    if variant == "shared" and (result["asset_calls"] != source_auction["asset_calls"]
                                                  or result["accounts"] != source_auction["accounts"]
                                                  or result["escrows"] != source_auction["escrows"]
                                                  or result["fee_pool_minor"] != sum(
                                                      call["fee_pool_minor"] for call in source_auction["asset_calls"].values())):
                        raise ValueError("shared wallet replay does not reproduce the source day's orders and settlement")
                    prior_accounts = {n: {"wallets": dict(a.wallets), "shares": dict(a.shares)} for n, a in accounts.items()}
                    outcomes[variant] = _metric(result, specs_by_path[path_key], assets, prices, prior_accounts)
                records.append({"path_id": key, "basket_index": basket_index, "assets": assets,
                                "quote_response_bps": response, "use_text": bool(text_flag), "session": session,
                                "trade_date": row["trade_date"], "source_cash_minor": sum(sum(a.wallets.values()) for a in previous_accounts.values()),
                                "cash_allocation_totals_minor": {name: sum(sum(account.wallets.values()) for account in
                                    _reallocate(previous_accounts, specs_by_path[path_key], assets, weights).values())
                                    for name, weights in scenarios.items()}, "outcomes": outcomes})
                counters[path_key] += 1
                if progress and len(records) % 100 == 0:
                    progress(len(records), len(baskets) * len(source_cfg["quote_response_bps"]) * 2 * len(sample_sessions))
    expected_paths = len(baskets) * len(source_cfg["quote_response_bps"]) * 2
    if len(states) != expected_paths or len(records) != expected_paths * len(sample_sessions):
        raise ValueError("wallet diagnostic source path or sampled day coverage incomplete")
    for key, count in counters.items():
        if count != len(next(iter(joined.values()))):
            raise ValueError("wallet diagnostic source path calendar incomplete")
    variant_names = ["shared", *cfg["cash_allocations"]]
    groups = _aggregate(records, variant_names)
    for row in records:
        total = row["cash_allocation_totals_minor"]
        if len(set(total.values())) != 1:
            raise ValueError("cash-allocation variants do not preserve matched total resources")
    if (load_summary(source) != summary or load_audit(source, source_audit) != audit
            or bound_inputs != {"config": file_sha256(config_path), "source_config": file_sha256(source_config),
                                "source_manifest": file_sha256(source / "portfolio_manifest.json"),
                                "source_audit_manifest": file_sha256(source_audit / "portfolio_audit_manifest.json")}):
        raise ValueError("wallet diagnostic source bindings changed during execution")
    result = {**identity, "sample_sessions": cfg["sample_sessions"], "baskets": len(baskets),
              "sampled_snapshots": len(records), "variants": list(cfg["cash_allocations"]), "records": records}
    summary_data = {**identity, "sample_sessions": cfg["sample_sessions"], "baskets": len(baskets),
                    "sampled_snapshots": len(records), "groups": groups,
                    "interpretation": "One-day fixed-order replay; portfolio decisions and later path feedback are held fixed"}
    lines = ["# 多资产钱包分配：固定订单簿配对诊断", "",
             f"基于已通过全量审计的组合源，抽取 {len(records)} 个预设公司篮子—日期快照，覆盖 {len(baskets)} 个篮子和日期索引 {', '.join(map(str, cfg['sample_sessions']))}。",
             "每个快照冻结原策略及背景订单、价格、股票和总现金，仅变更策略现金的共享/分账户方式；各变体的起始现金和股票完全相同。共享钱包回放必须复现源当日成交；每个变体的账户结算另用独立账本重建。",
             "这是固定订单的一日机制诊断，不模拟后续决策、价格反馈或整段策略效果。80/10/10 与 98/1/1 分配按资产排序轮换集中位置，以免系统性偏向篮子首只股票。", "",
             "| 响应 | 文本 | 分配 | 样本 | 接受成交率 | 请求成交率 | 现金截断单 | 相对共享改价快照 | 平均策略盯市 NAV 差（minor） |",
             "|---:|:---:|---|---:|---:|---:|---:|---:|---:|"]
    for group in groups:
        for name, values in group["variants"].items():
            lines.append(f"| {group['quote_response_bps']} | {'有' if group['use_text'] else '无'} | {name} | {values['snapshots']} | "
                         f"{values['accepted_fill_fraction']:.4%} | {values['requested_fill_fraction']:.4%} | {values['cash_clipped_orders']} | "
                         f"{values['changed_price_snapshots_vs_shared']} | {values['mean_strategy_marked_nav_delta_vs_shared_minor']:+.2f} |")
    lines += ["", "所有指标都来自冻结原订单簿下的一日反事实；若改变了成交，后续市场价格和策略订单也会变化。本表不能替代多日资金分配实验。", ""]
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in (("wallet_diagnostic_results.json", result), ("wallet_diagnostic_summary.json", summary_data)):
        (staging / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (staging / "wallet_diagnostic_report.md").write_text("\n".join(lines), encoding="utf-8")
    if code_hashes != {k: file_sha256(v) for k, v in code_paths.items()}:
        raise ValueError("wallet diagnostic code changed during execution")
    manifest = {**identity, "generated_at": datetime.now(timezone.utc).isoformat(),
                "artifacts": {name: file_sha256(staging / name) for name in sorted(ARTIFACTS)}}
    (staging / "wallet_diagnostic_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for file in staging.iterdir():
        file.replace(output_dir / file.name)
    staging.rmdir()
    return summary_data


def load_diagnostic(directory, config_path):
    directory = Path(directory).resolve()
    config_path, cfg = load_config(config_path)
    root = Path(__file__).resolve().parents[2]
    source = root / "research_outputs" / cfg["source_run"]
    source_audit = root / "research_outputs" / cfg["source_audit"]
    manifest = json.loads((directory / "wallet_diagnostic_manifest.json").read_text(encoding="utf-8"))
    code_paths = {"portfolio_wallet_diagnostic.py": Path(__file__),
                  "portfolio_auction.py": Path(__file__).with_name("portfolio_auction.py"),
                  "portfolio_audit.py": Path(__file__).with_name("portfolio_audit.py")}
    source_summary = load_summary(source)
    source_audit_result = load_audit(source, source_audit)
    if (manifest["pipeline_version"] != VERSION or manifest["run_id"] != cfg["run_id"]
            or source_summary["experiment_id"] != manifest["source_experiment_id"]
            or source_audit_result["pipeline_version"] != manifest["source_audit_version"]
            or manifest["inputs"] != {"config": file_sha256(config_path),
                "source_config": file_sha256(root / "research" / "configs" / cfg["source_config"]),
                "source_manifest": file_sha256(source / "portfolio_manifest.json"),
                "source_audit_manifest": file_sha256(source_audit / "portfolio_audit_manifest.json")}
            or manifest["code_sha256"] != {k: file_sha256(v) for k, v in code_paths.items()}
            or manifest["artifacts"] != {name: file_sha256(directory / name) for name in sorted(ARTIFACTS)}):
        raise ValueError("wallet diagnostic source, code or artifacts changed")
    summary = json.loads((directory / "wallet_diagnostic_summary.json").read_text(encoding="utf-8"))
    results = json.loads((directory / "wallet_diagnostic_results.json").read_text(encoding="utf-8"))
    variant_names = ["shared", *cfg["cash_allocations"]]
    if (summary["source_experiment_id"] != manifest["source_experiment_id"]
            or results["source_experiment_id"] != manifest["source_experiment_id"]
            or summary["sampled_snapshots"] != len(results["records"])
            or len(results["records"]) != summary["baskets"] * len(cfg["sample_sessions"]) * 4):
        raise ValueError("wallet diagnostic result coverage differs")
    verify_group_summaries(results["records"], variant_names, summary["groups"])
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_diagnostic(args.config, args.output_dir,
                            lambda n, t: print(f"Processed {n}/{t} matched snapshots", flush=True))
    print(json.dumps({k: result[k] for k in ("sampled_snapshots", "groups")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
