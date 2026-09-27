"""Independently reconstruct saved auction ledgers from bilateral trades."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .semantic_auction_experiment import _config, _read_config, load_auction_summary

VERSION = "auction-trade-ledger-audit-v1"


def audit_day(row: dict, previous: dict, settings: dict, session: int) -> dict:
    """Trades drive balances; saved orders and accounts are claims to be checked."""
    auction = row["auction"]
    before = {name: dict(account) for name, account in previous["accounts"].items()}
    if session:
        for account in before.values():
            account["sellable_shares"] = account["shares"]
    balances = {name: dict(account) for name, account in before.items()}
    price, prior = auction["price_after_minor"], previous["price_minor"]
    tick, lot, bps = settings["tick_minor"], settings["lot_size"], settings["fee_bps"]
    if auction["session"] != session or auction["price_before_minor"] != prior:
        raise ValueError("auction sequence or prior price differs from the reconstructed path")
    if row["signal_cutoff_date"] >= row["execution_reference_date"] or row["execution_reference_date"] >= row["trade_date"]:
        raise ValueError("saved information cutoff is not before execution and trade dates")
    if previous.get("trade_date") is not None and row["trade_date"] <= previous["trade_date"]:
        raise ValueError("saved trading dates did not advance")
    lower = max(tick, int((Decimal(prior) * (10000 - settings["price_band_bps"]) / (10000 * tick))
                         .to_integral_value(rounding=ROUND_CEILING)) * tick)
    upper = int((Decimal(prior) * (10000 + settings["price_band_bps"]) / (10000 * tick))
                .to_integral_value(rounding=ROUND_FLOOR)) * tick
    if type(price) is not int or price % tick or not lower <= price <= upper or auction["price_bounds_minor"] != [lower, upper]:
        raise ValueError("saved clearing price or band is invalid")
    orders = {order["order_id"]: order for order in auction["orders"]}
    if len(orders) != len(auction["orders"]) or len({order["owner"] for order in orders.values()}) != len(orders):
        raise ValueError("duplicate order or owner in one session")
    fills = Counter()
    volume = 0
    for trade in auction["trades"]:
        buy, sell = orders[trade["buy_order_id"]], orders[trade["sell_order_id"]]
        quantity = trade["quantity"]
        if (buy["side"] != "buy" or sell["side"] != "sell" or trade["buyer"] != buy["owner"]
                or trade["seller"] != sell["owner"] or trade["buyer"] == trade["seller"]
                or type(quantity) is not int or quantity <= 0 or quantity % lot
                or trade["price_minor"] != price or not sell["limit_price_minor"] <= price <= buy["limit_price_minor"]):
            raise ValueError("invalid bilateral trade identity, quantity or limit price")
        fills[buy["order_id"]] += quantity
        fills[sell["order_id"]] += quantity
        volume += quantity
        balances[buy["owner"]]["cash_minor"] -= quantity * price
        balances[sell["owner"]]["cash_minor"] += quantity * price
        balances[buy["owner"]]["shares"] += quantity
        balances[sell["owner"]]["shares"] -= quantity
        balances[sell["owner"]]["sellable_shares"] -= quantity
    fees, buy_levels, sell_levels = 0, Counter(), Counter()
    for order_id, order in orders.items():
        account = before[order["owner"]]
        accepted, filled = order["accepted_quantity"], fills[order_id]
        if (order["side"] not in {"buy", "sell"} or type(accepted) is not int or accepted < 0
                or accepted % lot or order["quantity"] % lot or not 0 <= filled <= accepted <= order["quantity"]
                or order["filled_quantity"] != filled or order["unfilled_quantity"] != accepted - filled):
            raise ValueError("order acceptance or fill differs from bilateral trades")
        if accepted and (not auction["execution_available"] or not lower <= order["limit_price_minor"] <= upper):
            raise ValueError("a halted or out-of-band order was accepted")
        if order["side"] == "sell" and accepted > account["sellable_shares"]:
            raise ValueError("accepted sell exceeds the session's sellable inventory")
        if order["side"] == "buy":
            reservation = accepted * order["limit_price_minor"]
            quotient, remainder = divmod(reservation * bps, 10000)
            if reservation + quotient + bool(remainder) > account["cash_minor"]:
                raise ValueError("buy acceptance failed cash and rounded fee reservation")
        quotient, remainder = divmod(filled * price * bps, 10000)
        fee = quotient + bool(remainder)
        if fee != order["fee_minor"]:
            raise ValueError("fee differs from the independently recomputed order total")
        balances[order["owner"]]["cash_minor"] -= fee
        fees += fee
        if accepted:
            (buy_levels if order["side"] == "buy" else sell_levels)[order["limit_price_minor"]] += accepted
    if not volume:
        if price != prior or fees or (buy_levels and sell_levels and max(buy_levels) >= min(sell_levels)):
            raise ValueError("zero trade price, fee or executable overlap is invalid")
    else:
        # Dense tick sweep is independent of the clearing engine's sparse candidate set.
        demand, supply, best = sum(buy_levels.values()), 0, None
        for candidate in range(lower, upper + tick, tick):
            demand -= buy_levels[candidate - tick]
            supply += sell_levels[candidate]
            key = (-min(demand, supply), abs(demand - supply), abs(candidate - prior), candidate)
            if best is None or key < best:
                best = key
        if price != best[3] or volume != -best[0]:
            raise ValueError("clearing price or volume differs from independent dense tick sweep")
    if (volume != auction["matched_volume"] or auction["accounts"] != balances
            or auction["fee_pool_minor"] != previous["fee_pool_minor"] + fees):
        raise ValueError("saved account or fee ledger differs from trade reconstruction")
    if any(account["cash_minor"] < 0 or not 0 <= account["sellable_shares"] <= account["shares"] for account in balances.values()):
        raise ValueError("reconstructed account became negative or oversold")
    cash, shares = sum(a["cash_minor"] for a in balances.values()), sum(a["shares"] for a in balances.values())
    if (cash != auction["cash_total_minor"] or shares != auction["shares_total"]
            or cash + auction["fee_pool_minor"] != previous["initial_cash_minor"] or shares != previous["initial_shares"]):
        raise ValueError("reconstructed market cash or shares do not conserve")
    return {"accounts": balances, "price_minor": price, "fee_pool_minor": auction["fee_pool_minor"],
            "initial_cash_minor": previous["initial_cash_minor"], "initial_shares": shares, "trade_date": row["trade_date"]}


def audit_directory(directory: Path, config_path: Path, output_dir: Path) -> dict:
    directory, config_path, output_dir = [path.resolve() for path in (directory, config_path, output_dir)]
    summary = load_auction_summary(directory)
    manifest = json.loads((directory / "auction_manifest.json").read_text(encoding="utf-8"))
    initial_bindings = {directory / "auction_manifest.json": file_sha256(directory / "auction_manifest.json"),
                        config_path: file_sha256(config_path), Path(__file__): file_sha256(Path(__file__))}
    cfg = _config(config_path)
    if manifest["inputs"].get(str(config_path)) != file_sha256(config_path) or cfg["run_id"] != summary["run_id"]:
        raise ValueError("auction audit config does not belong to the recorded run")
    if output_dir == directory or output_dir in directory.parents or directory in output_dir.parents or output_dir == config_path:
        raise ValueError("audit output must be separate from inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty auction audit directory")
    base = _read_config((config_path.parent / cfg["base_config"]).resolve())
    initial_accounts = {f"{agent['role']}_{index:02d}": {"cash_minor": int(Decimal(str(agent["initial_cash"])) * 100),
                        "shares": shares, "sellable_shares": shares}
                        for agent in base["agents"] for index, shares in enumerate(cfg["initial_inventory_per_role"])}
    results = json.loads((directory / "auction_results.json").read_text(encoding="utf-8"))
    paths = {(row["stock_code"], row["quote_response_bps"], row["use_text"]): row for row in results["path_summaries"]}
    states, hashers, counts = {}, {}, Counter()
    count, traded, halted, matched = 0, 0, 0, 0
    with gzip.open(directory / "auction_ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            key = (record.pop("stock_code"), record.pop("quote_response_bps"), record.pop("use_text"))
            if key not in paths:
                raise ValueError("ledger contains an undeclared market path")
            if key not in states:
                states[key] = {"accounts": initial_accounts, "price_minor": cfg["venue"]["price_start_minor"],
                               "fee_pool_minor": 0, "initial_cash_minor": sum(a["cash_minor"] for a in initial_accounts.values()),
                               "initial_shares": sum(a["shares"] for a in initial_accounts.values())}
                hashers[key] = hashlib.sha256(b"[")
            states[key] = audit_day(record, states[key], cfg["venue"], counts[key])
            if counts[key]:
                hashers[key].update(b", ")
            hashers[key].update(json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
            counts[key] += 1
            count += 1
            traded += int(record["auction"]["matched_volume"] > 0)
            halted += int(not record["auction"]["execution_available"])
            matched += record["auction"]["matched_volume"]
    if set(states) != set(paths) or count != summary["ledger_rows"]:
        raise ValueError("saved ledger path coverage is incomplete")
    for key, state in states.items():
        path = paths[key]
        hashers[key].update(b"]")
        if (counts[key] != path["sessions"] or hashers[key].hexdigest() != path["trace_sha256"]
                or state["price_minor"] != path["final_price_minor"] or state["fee_pool_minor"] != path["fee_pool_minor"]):
            raise ValueError("saved full trace differs from its recorded summary")
        for name, account in state["accounts"].items():
            saved = path["agent_summary"][name]
            if any(saved[field] != account[field] for field in account) or saved["final_wealth_minor"] != account["cash_minor"] + account["shares"] * state["price_minor"]:
                raise ValueError("final participant summary differs from reconstructed accounts")
    if (any(file_sha256(directory / name) != info["sha256"] for name, info in manifest["artifacts"].items())
            or any(file_sha256(path) != digest for path, digest in initial_bindings.items())):
        raise ValueError("auction source, ledger or auditor changed during audit")
    result = {"pipeline_version": VERSION, "status": "passed", "experiment_id": summary["experiment_id"],
              "paths": len(paths), "ledger_rows": count, "traded_sessions": traded, "halted_sessions": halted,
              "matched_volume": matched, "dense_tick_sweeps": traded,
              "checks": {"trade_reconstructed_cash_and_inventory": True, "integer_conservation": True,
                         "bounded_orders_and_fees": True, "no_self_trade": True, "no_trade_price_invariant": True,
                         "dense_sweep_price_and_volume": True, "full_trace_and_final_summary": True}}
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "auction_audit.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    binding = {"pipeline_version": VERSION, "run_manifest": str(directory / "auction_manifest.json"),
               "run_manifest_sha256": file_sha256(directory / "auction_manifest.json"),
               "config_path": str(config_path), "config_sha256": file_sha256(config_path),
               "code_sha256": file_sha256(Path(__file__)), "result_sha256": file_sha256(result_path)}
    (output_dir / "auction_audit_manifest.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_audit(directory: Path, audit_dir: Path) -> dict:
    manifest = json.loads((audit_dir / "auction_audit_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION or manifest.get("code_sha256") != file_sha256(Path(__file__))
            or Path(manifest["run_manifest"]).resolve() != (directory / "auction_manifest.json").resolve()
            or manifest["run_manifest_sha256"] != file_sha256(directory / "auction_manifest.json")
            or manifest["config_sha256"] != file_sha256(Path(manifest["config_path"]))
            or manifest["result_sha256"] != file_sha256(audit_dir / "auction_audit.json")):
        raise ValueError("auction audit binding, source or artifact changed")
    result = json.loads((audit_dir / "auction_audit.json").read_text(encoding="utf-8"))
    if result.get("status") != "passed" or not result.get("checks") or any(value is not True for value in result["checks"].values()):
        raise ValueError("auction audit did not pass every check")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit_directory(args.directory, args.config, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
