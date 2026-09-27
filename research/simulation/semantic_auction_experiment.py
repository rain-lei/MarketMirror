"""Condition finite call-auction markets on verified, lagged real text signals.

Realized stock returns are never applied to the simulated price. Historical
benchmark momentum and stock volatility are lagged conditioning inputs only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import platform
import statistics
import tempfile
import zlib
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters, AgentState, Observation, decide
from .call_auction import AuctionAccount, CallAuction, LimitOrder
from .historical_replay import prepare_steps
from .semantic_historical_replay import CODE_PATHS as BASE_CODE_PATHS, _read_config, _verified_inputs
from .semantic_memory_sensitivity import canonical_hash, memory_join, validate_scenario
from .semantic_replay import _check_steps

VERSION = "semantic-finite-auction-v1"
CODE_PATHS = {**BASE_CODE_PATHS, **{f"simulation/{name}": Path(__file__).with_name(name) for name in (
    "call_auction.py", "semantic_auction_experiment.py", "semantic_memory_sensitivity.py")}}
ARTIFACTS = {"auction_results.json", "auction_summary.json", "auction_report.md", "auction_ledger.jsonl.gz"}
VENUE_FIELDS = {"price_start_minor", "lot_size", "tick_minor", "fee_bps", "price_band_bps",
                "quote_spread_bps", "quote_response_bps"}


def _config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    fields = {"run_id", "base_config", "memory_scenario", "initial_inventory_per_role", "venue", "quote_response_bps"}
    if not isinstance(cfg, dict) or set(cfg) != fields or any(
            not isinstance(cfg[key], str) or not cfg[key].strip() for key in ("run_id", "base_config")):
        raise ValueError("auction experiment requires all explicit fields")
    validate_scenario(cfg["memory_scenario"])
    venue = cfg["venue"]
    if not isinstance(venue, dict) or set(venue) != VENUE_FIELDS - {"quote_response_bps"}:
        raise ValueError("venue settings must be explicit")
    _validate_venue({**venue, "quote_response_bps": 0})
    inventory = cfg["initial_inventory_per_role"]
    if (not isinstance(inventory, list) or len(inventory) < 2 or len(set(inventory)) != len(inventory)
            or any(type(q) is not int or q < 0 or q % venue["lot_size"] for q in inventory)
            or not any(inventory)):
        raise ValueError("each role requires the same explicit cohort of distinct whole-lot inventories")
    coefficients = cfg["quote_response_bps"]
    if (not isinstance(coefficients, list) or not coefficients or coefficients != sorted(set(coefficients))
            or coefficients[0] != 0 or any(type(v) is not int or not 0 <= v <= 5000 for v in coefficients)):
        raise ValueError("quote response grid must be unique, increasing, bounded and include zero")
    return cfg


def _validate_venue(venue: dict) -> None:
    if not isinstance(venue, dict) or set(venue) != VENUE_FIELDS or any(type(v) is not int or v < 0 for v in venue.values()):
        raise ValueError("all venue parameters must be nonnegative integers")
    if venue["quote_spread_bps"] > 2000 or venue["quote_response_bps"] > 5000:
        raise ValueError("venue spread or quote response is outside the model bounds")
    CallAuction({"validation": AuctionAccount(1, 0, 0)}, venue["price_start_minor"], venue["lot_size"],
                venue["tick_minor"], venue["fee_bps"], venue["price_band_bps"])


def simulate_agents(steps: list[dict], role_agents: list[AgentParameters], inventory: list[int],
                    venue_settings: dict, use_text: bool) -> dict:
    """Replay a closed modeled asset market; no observed_return enters price or quotes."""
    _check_steps(steps)
    _validate_venue(venue_settings)
    if type(use_text) is not bool or {agent.role for agent in role_agents} != {
            "aggressive", "conservative", "institutional"} or len(role_agents) != 3:
        raise ValueError("auction requires one parameter set per role and an explicit text ablation")
    if (not isinstance(inventory, list) or not inventory or any(type(q) is not int or q < 0
            or q % venue_settings["lot_size"] for q in inventory)):
        raise ValueError("initial inventory must consist of nonnegative whole lots")
    agents = [replace(agent, name=f"{agent.role}_{index:02d}") for agent in role_agents for index in range(len(inventory))]
    accounts, initial_wealth, states = {}, {}, {}
    for agent in agents:
        cash = Decimal(str(agent.initial_cash)) * 100
        if cash != cash.to_integral_value():
            raise ValueError("initial cash must have at most two decimal places")
        shares = inventory[int(agent.name.rsplit("_", 1)[1])]
        accounts[agent.name] = AuctionAccount(int(cash), shares, shares)
        initial_wealth[agent.name] = int(cash) + shares * venue_settings["price_start_minor"]
        states[agent.name] = AgentState(agent.initial_cash, float(shares))
    venue = CallAuction(accounts, venue_settings["price_start_minor"], venue_settings["lot_size"],
                        venue_settings["tick_minor"], venue_settings["fee_bps"], venue_settings["price_band_bps"])
    peaks = dict(initial_wealth)
    drawdowns = {agent.name: 0.0 for agent in agents}
    risk_breaches = {agent.name: 0 for agent in agents}
    trades = {agent.name: 0 for agent in agents}
    fees = {agent.name: 0 for agent in agents}
    trace = []
    for index, step in enumerate(steps):
        if "execution_reference_date" in step and not step["signal_cutoff_date"] < step["execution_reference_date"] < step["trade_date"]:
            raise ValueError("auction conditioning and execution reference dates are out of order")
        decisions, orders, quotes = {}, [], {}
        lower, upper = venue.price_bounds()
        for sequence, agent in enumerate(agents):
            account = venue.accounts[agent.name]
            state = states[agent.name]
            state.cash, state.shares = account.cash_minor / 100, float(account.shares)
            text, uncertainty = (step["text_signal"], step["text_uncertainty"]) if use_text else (0.0, 0.0)
            observation = Observation(index, venue.price_minor / 100, step["estimated_volatility"],
                                      step["market_signal"], text, uncertainty,
                                      f"benchmark-through:{step['signal_cutoff_date']}", step["text_evidence"])
            decision = decide(agent, state, observation, use_text=use_text)
            decisions[agent.name] = asdict(decision)
            # Same role belief as the allocation rule; quote sensitivity is an explicit assumption.
            belief = agent.market_sensitivity * observation.market_signal + agent.text_sensitivity * text - agent.uncertainty_aversion * uncertainty
            quantity = math.floor(abs(decision.requested_shares) / venue.lot_size) * venue.lot_size
            if not quantity:
                continue
            side = "buy" if decision.requested_shares > 0 else "sell"
            spread_sign = -1 if side == "buy" else 1
            reservation = Decimal(venue.price_minor) * (1 + Decimal(str(belief))
                          * venue_settings["quote_response_bps"] / 10000
                          + Decimal(spread_sign * venue_settings["quote_spread_bps"]) / 20000)
            rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
            quote = int((reservation / venue.tick_minor).to_integral_value(rounding=rounding)) * venue.tick_minor
            quote = max(lower, min(upper, quote))
            quotes[agent.name] = {"belief": belief, "limit_price_minor": quote}
            orders.append(LimitOrder(f"session-{index}:{agent.name}", agent.name, side, quantity, quote, sequence))
        cleared = venue.clear(index, orders, step.get("execution_available", True))
        for order in cleared["orders"]:
            trades[order["owner"]] += int(order["filled_quantity"] > 0)
            fees[order["owner"]] += order["fee_minor"]
        for agent in agents:
            account = venue.accounts[agent.name]
            wealth = account.cash_minor + account.shares * venue.price_minor
            peaks[agent.name] = max(peaks[agent.name], wealth)
            drawdowns[agent.name] = max(drawdowns[agent.name], 1 - wealth / peaks[agent.name])
            closing_weight = account.shares * venue.price_minor / wealth
            risk_breaches[agent.name] += int(closing_weight > decisions[agent.name]["risk_weight_cap"] + 1e-9)
        trace.append({"trade_date": step["trade_date"], "signal_cutoff_date": step["signal_cutoff_date"],
                      "execution_reference_date": step.get("execution_reference_date"),
                      "market_signal": step["market_signal"], "estimated_volatility": step["estimated_volatility"],
                      "text_signal_used": step["text_signal"] if use_text else 0.0,
                      "text_uncertainty_used": step["text_uncertainty"] if use_text else 0.0,
                      "text_evidence": step["text_evidence"] if use_text else "text:disabled",
                      "decisions": decisions, "quotes": quotes, "auction": cleared})
    accepted_quantity = sum(row["accepted_quantity"] for day in trace for row in day["auction"]["orders"])
    requested_quantity = sum(row["quantity"] for day in trace for row in day["auction"]["orders"])
    matched = sum(day["auction"]["matched_volume"] for day in trace)
    summary = {"sessions": len(steps), "agents": len(agents), "use_text": use_text,
               "quote_response_bps": venue_settings["quote_response_bps"], "final_price_minor": venue.price_minor,
               "initial_cash_minor": venue.initial_cash_minor, "initial_shares": venue.initial_shares,
               "final_cash_minor": sum(account.cash_minor for account in venue.accounts.values()),
               "fee_pool_minor": venue.fee_pool_minor, "matched_volume": matched,
               "trade_sessions": sum(day["auction"]["matched_volume"] > 0 for day in trace),
               "price_change_sessions": sum(day["auction"]["price_before_minor"] != day["auction"]["price_after_minor"] for day in trace),
               "blocked_sessions": sum(not day["auction"]["execution_available"] for day in trace),
               "accepted_quantity": accepted_quantity, "requested_quantity": requested_quantity,
               "accepted_fill_fraction": 2 * matched / accepted_quantity if accepted_quantity else 0.0,
               "request_fill_fraction": 2 * matched / requested_quantity if requested_quantity else 0.0,
               "unfilled_quantity": accepted_quantity - 2 * matched,
               "trace_sha256": canonical_hash(trace),
               "agent_summary": {agent.name: {"role": agent.role,
                   "initial_wealth_minor": initial_wealth[agent.name], **asdict(venue.accounts[agent.name]),
                   "final_wealth_minor": venue.accounts[agent.name].cash_minor + venue.accounts[agent.name].shares * venue.price_minor,
                   "wealth_multiple": (venue.accounts[agent.name].cash_minor + venue.accounts[agent.name].shares * venue.price_minor) / initial_wealth[agent.name],
                   "trades": trades[agent.name], "fees_minor": fees[agent.name],
                   "max_drawdown": drawdowns[agent.name], "risk_breach_sessions": risk_breaches[agent.name]} for agent in agents}}
    return {"summary": summary, "trace": trace}


def run_experiment(config_path: Path, output_dir: Path, progress=None) -> dict:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    cfg = _config(config_path)
    base_path = (config_path.parent / cfg["base_config"]).resolve()
    base = _read_config(base_path)
    if base["selection_rule"] != "all_signal_stocks":
        raise ValueError("auction experiment requires all signal companies")
    market, rows, gate, inputs = _verified_inputs(base_path, base)
    inputs[str(config_path)] = file_sha256(config_path)
    if any(output_dir == Path(name) or output_dir in Path(name).parents for name in inputs):
        raise ValueError("auction output must be separate from all input files")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty auction output directory")
    code = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
    runtime = {"python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION}
    identity = {"inputs": inputs, "code_sha256": code, "runtime": runtime}
    experiment_id = canonical_hash(identity)
    market_path = (base_path.parent / base["market_manifest"]).resolve()
    groups = _load_market(market_path.parent / "market_daily.csv", market)
    download = json.loads((base_path.parent / base["download_manifest"]).read_text(encoding="utf-8"))
    suspended = {row["symbol"].split(".")[1]: set(row["suspended_sessions"])
                 for row in download["quote_checks"] if row["symbol"] != download["benchmark_symbol"]}
    agents = [AgentParameters(**row) for row in base["agents"]]
    paths, paired, roles, daily_rows = [], [], [], 0
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        ledger_path = staging / "auction_ledger.jsonl.gz"
        with ledger_path.open("wb") as binary, gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as ledger:
            for stock_index, stock in enumerate(base["stock_codes"], start=1):
                steps = prepare_steps(groups[stock], base["start_date"], base["end_date"],
                                      base["momentum_sessions"], base["volatility_sessions"])
                joined = memory_join(steps, stock, rows, market["session_dates"], cfg["memory_scenario"])
                for step in joined:
                    step["execution_available"] = step["execution_reference_date"] not in suspended[stock]
                for response in cfg["quote_response_bps"]:
                    results = []
                    for enabled in (False, True):
                        result = simulate_agents(joined, agents, cfg["initial_inventory_per_role"],
                                                 {**cfg["venue"], "quote_response_bps": response}, enabled)
                        for day in result["trace"]:
                            row = {"stock_code": stock, "quote_response_bps": response, "use_text": enabled, **day}
                            ledger.write((json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                            daily_rows += 1
                        paths.append({"stock_code": stock, **result["summary"]})
                        results.append(result)
                    control, active = results
                    has_text = any(step["text_signal"] != 0 or step["text_uncertainty"] != 0 for step in joined)
                    if not has_text and active["summary"]["trace_sha256"] != control["summary"]["trace_sha256"]:
                        # Evidence metadata differs across enabled states; decisions and exchange state must not.
                        if any(a["decisions"] != b["decisions"] or a["auction"] != b["auction"]
                               for a, b in zip(active["trace"], control["trace"], strict=True)):
                            raise AssertionError("an empty text channel changed the closed market")
                    if response == 0 and any(path["summary"]["price_change_sessions"] for path in results):
                        raise AssertionError("zero quote response changed transaction prices")
                    paired.append({"stock_code": stock, "quote_response_bps": response,
                                   "text_price_multiple": active["summary"]["final_price_minor"] / cfg["venue"]["price_start_minor"],
                                   "no_text_price_multiple": control["summary"]["final_price_minor"] / cfg["venue"]["price_start_minor"],
                                   "price_difference_multiple": (active["summary"]["final_price_minor"] - control["summary"]["final_price_minor"]) / cfg["venue"]["price_start_minor"],
                                   "text_matched_volume": active["summary"]["matched_volume"],
                                   "no_text_matched_volume": control["summary"]["matched_volume"]})
                    for role in ("aggressive", "conservative", "institutional"):
                        members = [name for name, row in active["summary"]["agent_summary"].items() if row["role"] == role]
                        roles.append({"stock_code": stock, "quote_response_bps": response, "role": role,
                                      "mean_wealth_difference_multiple": statistics.mean(
                                          active["summary"]["agent_summary"][name]["wealth_multiple"]
                                          - control["summary"]["agent_summary"][name]["wealth_multiple"] for name in members)})
                if progress is not None:
                    progress(stock_index, len(base["stock_codes"]))
        grouped = []
        for response in cfg["quote_response_bps"]:
            selected = [row for row in paired if row["quote_response_bps"] == response]
            selected_paths = [row for row in paths if row["quote_response_bps"] == response]
            grouped.append({"quote_response_bps": response, "stocks": len(selected),
                            "mean_price_difference_multiple": statistics.mean(row["price_difference_multiple"] for row in selected),
                            "minimum_price_difference_multiple": min(row["price_difference_multiple"] for row in selected),
                            "maximum_price_difference_multiple": max(row["price_difference_multiple"] for row in selected),
                            "changed_stock_prices": sum(row["price_difference_multiple"] != 0 for row in selected),
                            "text_matched_volume": sum(row["text_matched_volume"] for row in selected),
                            "no_text_matched_volume": sum(row["no_text_matched_volume"] for row in selected),
                            "text_trade_sessions": sum(row["trade_sessions"] for row in selected_paths if row["use_text"]),
                            "no_text_trade_sessions": sum(row["trade_sessions"] for row in selected_paths if not row["use_text"]),
                            "text_unfilled_quantity": sum(row["unfilled_quantity"] for row in selected_paths if row["use_text"]),
                            "no_text_unfilled_quantity": sum(row["unfilled_quantity"] for row in selected_paths if not row["use_text"]),
                            "roles": [{"role": role, "mean_wealth_difference_multiple": statistics.mean(
                                row["mean_wealth_difference_multiple"] for row in roles if row["role"] == role and row["quote_response_bps"] == response)}
                                      for role in ("aggressive", "conservative", "institutional")]})
        if (any(file_sha256(Path(name)) != digest for name, digest in inputs.items())
                or code != {name: file_sha256(path) for name, path in CODE_PATHS.items()}):
            raise RuntimeError("auction input or code changed during calculation")
        summary = {"pipeline_version": VERSION, "run_id": cfg["run_id"], "experiment_id": experiment_id,
                   "data_kind": "observed_signal_conditioned_model_market", "stocks": len(base["stock_codes"]),
                   "paths": len(paths), "agents_per_market": len(agents) * len(cfg["initial_inventory_per_role"]),
                   "sessions_per_stock": sorted({row["sessions"] for row in paths}), "ledger_rows": daily_rows,
                   "market_dataset_id": market["market_dataset_id"], "venue": cfg["venue"],
                   "initial_inventory_per_role": cfg["initial_inventory_per_role"], "memory_scenario": cfg["memory_scenario"],
                   "zero_quote_response_price_invariant": True, "grouped": grouped,
                   "information_rule": "t-2 observed signals condition quotes; realized stock returns do not set modeled prices",
                   "interpretation": "Finite hypothetical auction mechanism; not calibrated investor behavior, historical price reconstruction or prediction"}
        result = {**summary, "review_basis": gate["review_basis"], "path_summaries": paths,
                  "paired_stock_effects": paired, "paired_role_effects": roles}
        for name, payload in (("auction_summary.json", summary), ("auction_results.json", result)):
            (staging / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        lines = ["# 有限资金与持仓的语义集合竞价实验", "",
                 f"{summary['stocks']} 家公司，各市场 {summary['agents_per_market']} 个主体；{summary['paths']} 条市场路径、{daily_rows:,} 条完整日账本。",
                 "三类参数各复制为相同初始持仓梯度的主体。资金、股票供给有限；没有外部账户吸收订单或无限提供库存。",
                 "模型价格由双方限价订单的实际成交决定，零成交保持原价；原始股票实际收益未直接推进模拟价格。",
                 "按最大成交量、最小绝对不平衡、最接近前价和较低报价档位依次选价；分配按更优限价及固定提交顺序。",
                 "买方按限价预留资金和费用；卖方受可卖库存限制，新购库存下个模型交易日才可卖。未成交日订单到期，不自动被外部账户承接。",
                 "每笔结算使用整数最小货币单位；每条已成交订单按总成交额收一次向上取整的费用；全市场现金加费用和股票总量逐日严格守恒。",
                 "", "| 报价响应（基点） | 公司数 | 文本－无文本平均末价/初价 | 末价改变公司 | 有文本成交单位 | 无文本成交单位 |",
                 "|---:|---:|---:|---:|---:|---:|"]
        for row in grouped:
            lines.append(f"| {row['quote_response_bps']} | {row['stocks']} | {row['mean_price_difference_multiple']:+.4%} | "
                         f"{row['changed_stock_prices']} | {row['text_matched_volume']} | {row['no_text_matched_volume']} |")
        lines += ["", "全部固定报价响应假设均报告。零报价响应价格不变，用作价格形成通道对照。",
                  "真实问答及截至 t-2 的市场动量/个股波动率只作为条件输入。开盘归一化价格、初始持仓、报价响应、费用、批量、报价档位和价格带均为模型假设，不能称为真实交易所规则或实证估计。",
                  "所有逐日信号、决策、报价、成交双方、未成交量及账户结算保存在无损压缩的 `auction_ledger.jsonl.gz`；摘要绑定原文来源、模型评分、信号和当前执行代码。",
                  "研究目标还包括行为校准、多资产机构策略及历史/预测验证；当前只是新增真正的有限双边撮合机制。", ""]
        (staging / "auction_report.md").write_text("\n".join(lines), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "experiment_id": experiment_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(), **identity,
                    "artifacts": {name: {"sha256": file_sha256(staging / name)} for name in sorted(ARTIFACTS)}}
        (staging / "auction_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return summary


def load_auction_summary(directory: Path) -> dict:
    manifest = json.loads((directory / "auction_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("pipeline_version") != VERSION or manifest.get("code_sha256") != {
            name: file_sha256(path) for name, path in CODE_PATHS.items()}:
        raise ValueError("auction code differs from the recorded run")
    if set(manifest.get("artifacts", {})) != ARTIFACTS:
        raise ValueError("auction artifact set is invalid")
    if any(file_sha256(directory / name) != info["sha256"] for name, info in manifest["artifacts"].items()):
        raise ValueError("auction artifact changed")
    if not manifest.get("inputs") or any(file_sha256(Path(name)) != digest for name, digest in manifest["inputs"].items()):
        raise ValueError("auction source changed")
    summary = json.loads((directory / "auction_summary.json").read_text(encoding="utf-8"))
    if (summary.get("pipeline_version") != VERSION or summary.get("experiment_id") != manifest["experiment_id"]
            or canonical_hash({key: manifest[key] for key in ("inputs", "code_sha256", "runtime")}) != manifest["experiment_id"]):
        raise ValueError("auction summary identity is invalid")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    def progress(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            print(f"Processed {done}/{total} companies", flush=True)
    result = run_experiment(args.config, args.output_dir, progress=progress)
    print(json.dumps({key: result[key] for key in ("stocks", "paths", "agents_per_market", "ledger_rows", "grouped")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
