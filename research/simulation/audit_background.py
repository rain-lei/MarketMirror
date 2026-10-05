"""Rebuild finite-participant ledgers and independently verify inventory demand."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .call_auction import PRICE_TIE_BREAKS
from .audit_auction import audit_day as dense_audit_day
from .audit_feedback import check_feedback
from .background_experiment import experiment_inputs, load_summary
from .participant_market import initial_accounts

VERSION = "finite-background-trade-and-demand-audit-v1"


def rounded_fee(notional, bps):
    whole, remainder = divmod(notional * bps, 10000)
    return whole + bool(remainder)


def interval_price(auction: dict, settings: dict, price_tie_break: str = "nearest_prior"):
    """Sweep demand/supply intervals, not the engine's candidate price list.

    Demand drops immediately above a buy limit; supply rises at a sell limit.
    On each constant-volume interval, the final tick follows the selected
    tie-break rule. The default chooses the nearest prior tick; the SSE
    midpoint option is an exchange-rule sensitivity, not a full venue model.
    """
    lower, upper = auction["price_bounds_minor"]
    tick, prior = settings["tick_minor"], auction["price_before_minor"]
    events, demand, supply = {}, 0, 0
    for order in auction["orders"]:
        quantity, limit = order["accepted_quantity"], order["limit_price_minor"]
        if not quantity:
            continue
        if order["side"] == "buy":
            demand += quantity
            events.setdefault(limit + tick, [0, 0])[0] -= quantity
        else:
            events.setdefault(limit, [0, 0])[1] += quantity
    boundaries = sorted({lower, upper + tick} | {p for p in events if lower <= p <= upper})
    if price_tie_break not in PRICE_TIE_BREAKS:
        raise ValueError("unsupported independent auction tie-break rule")
    pressure = sum(order["accepted_quantity"] * (1 if order["side"] == "buy" else -1)
                   for order in auction["orders"])
    best, imbalance, scored_intervals = None, None, []
    for left, next_left in zip(boundaries, boundaries[1:]):
        change = events.get(left, (0, 0))
        demand, supply = demand + change[0], supply + change[1]
        right = next_left - tick
        scored_intervals.append((left, right, demand, supply))
        candidate = (next_left - tick if pressure > 0 else left) if (
            price_tie_break == "accepted_order_pressure" and pressure) else max(left, min(prior, next_left - tick))
        key = ((-min(demand, supply), abs(demand - supply), -candidate if pressure > 0 else candidate,
                candidate) if price_tie_break == "accepted_order_pressure" and pressure else
               (-min(demand, supply), abs(demand - supply), abs(candidate - prior), candidate))
        if price_tie_break == "sse_midpoint":
            key = (-min(demand, supply), abs(demand - supply))
        if best is None or key < best:
            best, imbalance = key, demand - supply
    if price_tie_break == "sse_midpoint" and -best[0] > 0:
        optimal = [row for row in scored_intervals if (-min(row[2], row[3]), abs(row[2] - row[3])) == best]
        first, last = min(row[0] for row in optimal), max(row[1] for row in optimal)
        price = ((first // tick + last // tick + 1) // 2) * tick
        interval = next(row for row in optimal if row[0] <= price <= row[1])
        return price, -best[0], interval[2] - interval[3]
    if best[0] == 0:
        imbalance = sum(o["accepted_quantity"] * (1 if o["side"] == "buy" else -1) for o in auction["orders"]
                        if (o["side"] == "buy" and o["limit_price_minor"] >= prior) or (o["side"] == "sell" and o["limit_price_minor"] <= prior))
        return prior, 0, imbalance
    return best[3], -best[0], imbalance


def reconstruct_day(record, previous, settings, session, price_tie_break="nearest_prior"):
    auction = record["auction"]
    before = {name: dict(a) for name, a in previous["accounts"].items()}
    if session:
        for a in before.values():
            a["sellable_shares"] = a["shares"]
    balances = {name: dict(a) for name, a in before.items()}
    price, prior = auction["price_after_minor"], previous["price_minor"]
    tick, lot, bps = settings["tick_minor"], settings["lot_size"], settings["fee_bps"]
    lower = max(tick, -(-(prior * (10000 - settings["price_band_bps"])) // (10000 * tick)) * tick)
    upper = prior * (10000 + settings["price_band_bps"]) // (10000 * tick) * tick
    if (auction["session"] != session or auction["price_before_minor"] != prior or type(price) is not int
            or price % tick or not lower <= price <= upper or auction["price_bounds_minor"] != [lower, upper]
            or not record["signal_cutoff_date"] < record["execution_reference_date"] < record["trade_date"]):
        raise ValueError("market sequence, information dates or price bounds are invalid")
    orders = {o["order_id"]: o for o in auction["orders"]}
    if (len(orders) != len(auction["orders"]) or len({o["owner"] for o in orders.values()}) != len(orders)
            or len({o["sequence"] for o in orders.values()}) != len(orders)):
        raise ValueError("market has duplicate order, owner or sequence")
    fills, volume = Counter(), 0
    for trade in auction["trades"]:
        buy, sell = orders[trade["buy_order_id"]], orders[trade["sell_order_id"]]
        quantity = trade["quantity"]
        if (type(quantity) is not int or quantity <= 0 or quantity % lot or buy["side"] != "buy" or sell["side"] != "sell"
                or trade["buyer"] != buy["owner"] or trade["seller"] != sell["owner"] or trade["buyer"] == trade["seller"]
                or trade["price_minor"] != price or not sell["limit_price_minor"] <= price <= buy["limit_price_minor"]):
            raise ValueError("invalid bilateral trade")
        fills[buy["order_id"]] += quantity
        fills[sell["order_id"]] += quantity
        balances[buy["owner"]]["cash_minor"] -= quantity * price
        balances[sell["owner"]]["cash_minor"] += quantity * price
        balances[buy["owner"]]["shares"] += quantity
        balances[sell["owner"]]["shares"] -= quantity
        balances[sell["owner"]]["sellable_shares"] -= quantity
        volume += quantity
    fees = 0
    for identity, order in orders.items():
        quantity, limit = order["quantity"], order["limit_price_minor"]
        if (order["owner"] not in before or order["side"] not in {"buy", "sell"} or type(quantity) is not int or quantity <= 0 or quantity % lot
                or type(limit) is not int or limit <= 0 or limit % tick):
            raise ValueError("invalid participant order")
        account = before[order["owner"]]
        cash_clipped, inventory_clipped = False, False
        if not auction["execution_available"] or not lower <= limit <= upper:
            expected = 0
        elif order["side"] == "sell":
            expected = min(quantity, account["sellable_shares"] // lot * lot)
            inventory_clipped = expected < quantity
        else:
            # Independent binary search of affordability, including per-order fee rounding.
            low, high = 0, min(quantity // lot, account["cash_minor"] // (limit * lot))
            while low < high:
                middle = (low + high + 1) // 2
                notional = middle * lot * limit
                if notional + rounded_fee(notional, bps) <= account["cash_minor"]:
                    low = middle
                else:
                    high = middle - 1
            expected = low * lot
            cash_clipped = expected < quantity
        accepted, filled = order["accepted_quantity"], fills[identity]
        if type(accepted) is not int or accepted != expected or not 0 <= filled <= accepted or order["filled_quantity"] != filled or order["unfilled_quantity"] != accepted - filled:
            raise ValueError("order acceptance or fill differs from independent resource bounds")
        if (("cash_and_fee_reservation" in order["reasons"]) != cash_clipped
                or ("sellable_inventory" in order["reasons"]) != inventory_clipped):
            raise ValueError("resource clipping reasons differ from reconstructed bounds")
        fee = rounded_fee(filled * price, bps)
        if order["fee_minor"] != fee:
            raise ValueError("order fee differs from reconstructed trades")
        balances[order["owner"]]["cash_minor"] -= fee
        fees += fee
    calculated_price, calculated_volume, imbalance = interval_price(auction, settings, price_tie_break)
    if (price != calculated_price or volume != calculated_volume or auction["matched_volume"] != volume
            or auction["clearing_imbalance"] != imbalance):
        raise ValueError("clearing differs from independent interval sweep")
    if (auction["accounts"] != balances or auction["fee_pool_minor"] != previous["fee_pool_minor"] + fees
            or any(type(a[f]) is not int or a[f] < 0 for a in balances.values() for f in ("cash_minor", "shares", "sellable_shares"))
            or any(a["sellable_shares"] > a["shares"] for a in balances.values())):
        raise ValueError("saved participant balances differ from bilateral trade reconstruction")
    cash, shares = sum(a["cash_minor"] for a in balances.values()), sum(a["shares"] for a in balances.values())
    if (cash != auction["cash_total_minor"] or shares != auction["shares_total"] or shares != previous["initial_shares"]
            or cash + auction["fee_pool_minor"] != previous["initial_cash_minor"]):
        raise ValueError("finite market resources do not conserve")
    return {"accounts": balances, "price_minor": price, "fee_pool_minor": auction["fee_pool_minor"],
            "initial_cash_minor": previous["initial_cash_minor"], "initial_shares": previous["initial_shares"], "trade_date": record["trade_date"]}


def verify_background_demands(record, previous, case, stock, session, settings, specs):
    names = {name for name, spec in specs.items() if spec["kind"] == "background"}
    if set(record["background_demands"]) != names:
        raise ValueError("background demand coverage differs")
    orders = {o["owner"]: o for o in record["auction"]["orders"]}
    prior, tick, lot = previous["price_minor"], settings["tick_minor"], settings["lot_size"]
    bounds = record["auction"]["price_bounds_minor"]
    for name in names:
        shares = previous["accounts"][name]["shares"]
        if case["mode"] == "idle":
            expected = {"mode": "idle", "current_shares": shares, "target_shares": shares,
                        "requested_quantity": 0, "side": "hold", "limit_price_minor": None, "shock_sha256": None}
        elif case["mode"] == "stochastic_arrival":
            digest = hashlib.sha256(f"{case['seed']}:{stock}:{session}:{name}".encode()).hexdigest()
            arrived = int(digest[:16], 16) * 10000 // 2**64 < case["arrival_rate_bps"]
            side = ("buy" if int(digest[16:32], 16) < 2**63 else "sell") if arrived else "hold"
            lots = 1 + int(digest[32:48], 16) * case["max_order_lots"] // 2**64
            quantity = lots * lot if arrived else 0
            quote = None
            if quantity:
                numerator = prior * (10000 + (case["urgency_bps"] if side == "buy" else -case["urgency_bps"]))
                denominator = 10000 * tick
                units = numerator // denominator if side == "buy" else -(-numerator // denominator)
                quote = max(bounds[0], min(bounds[1], units * tick))
            expected = {"mode": "stochastic_arrival", "current_shares": shares,
                        "arrival_rate_bps": case["arrival_rate_bps"], "arrived": arrived,
                        "requested_quantity": quantity, "side": side,
                        "limit_price_minor": quote, "shock_sha256": digest}
        else:
            digest = hashlib.sha256(f"{case['seed']}:{stock}:{session}:{name}".encode()).hexdigest()
            offset = int(digest[:16], 16) * (2 * case["target_range_lots"] + 1) // 2**64 - case["target_range_lots"]
            target = max(0, case["initial_shares"] + offset * lot)
            delta = target - shares
            quantity = min(abs(delta), case["max_order_lots"] * lot)
            side = "buy" if delta > 0 else "sell" if delta < 0 else "hold"
            quote = None
            if quantity:
                numerator = prior * (10000 + (case["urgency_bps"] if side == "buy" else -case["urgency_bps"]))
                denominator = 10000 * tick
                units = numerator // denominator if side == "buy" else -(-numerator // denominator)
                quote = max(bounds[0], min(bounds[1], units * tick))
            expected = {"mode": "inventory_target", "current_shares": shares, "target_shares": target, "target_offset_lots": offset,
                        "requested_quantity": quantity, "side": side, "limit_price_minor": quote, "shock_sha256": digest}
        if record["background_demands"][name] != expected:
            raise ValueError("background demand differs from the fixed causal inventory target")
        if expected["requested_quantity"]:
            order = orders.get(name)
            if order is None or any(order[field] != expected[key] for field, key in (("side", "side"), ("quantity", "requested_quantity"), ("limit_price_minor", "limit_price_minor"))):
                raise ValueError("background order differs from the declared demand")
        elif name in orders:
            raise ValueError("an idle or satisfied background participant submitted an order")


def ledger_records(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def audit_directory(directory, config_path, output_dir, progress=None):
    directory, config_path, output_dir = [p.resolve() for p in (directory, config_path, output_dir)]
    summary = load_summary(directory)
    manifest_path = directory / "background_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bindings = {p: file_sha256(p) for p in (manifest_path, config_path, Path(__file__))}
    cfg, parent, auction, base, _, _, inputs, joined, core = experiment_inputs(config_path)
    if manifest["inputs"] != inputs or summary["run_id"] != cfg["run_id"]:
        raise ValueError("background audit inputs belong to another run")
    if (output_dir == directory or output_dir in directory.parents or directory in output_dir.parents
            or any(output_dir == Path(p) or output_dir in Path(p).parents for p in inputs)):
        raise ValueError("background audit output must be separate from inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty background audit directory")
    result = json.loads((directory / "background_results.json").read_text(encoding="utf-8"))
    cases = {c["case_id"]: c for c in cfg["background_cases"]}
    expected_keys = {(stock, cid, response, enabled) for stock in joined for cid in cases for response in parent["quote_response_bps"] for enabled in (False, True)}
    paths = {(p["stock_code"], p["case_id"], p["quote_response_bps"], p["use_text"]): p for p in result["path_summaries"]}
    if len(paths) != len(result["path_summaries"]) or set(paths) != expected_keys or summary["paths"] != len(paths):
        raise ValueError("background path coverage differs")
    agents = [AgentParameters(**a) for a in base["agents"]]
    account_sets, specs, wealth = {}, {}, {}
    for cid, case in cases.items():
        _, accounts, specs[cid], wealth[cid] = initial_accounts(agents, core, case, auction["venue"]["price_start_minor"])
        account_sets[cid] = {name: a.__dict__ for name, a in accounts.items()}
    if result["participant_specs"] != specs:
        raise ValueError("background participant specification differs")
    states, histories, hashers, counts, totals, aggregates = {}, {}, {}, Counter(), {}, {}
    rows, traded, halted, dense_checks, dense_traded = 0, 0, 0, 0, 0
    for record in ledger_records(directory / "background_ledger.jsonl.gz"):
        key = tuple(record.pop(field) for field in ("stock_code", "case_id", "quote_response_bps", "use_text"))
        if key not in paths:
            raise ValueError("background ledger contains an undeclared path")
        stock, cid, response, enabled = key
        if key not in states:
            accounts = account_sets[cid]
            states[key] = {"accounts": accounts, "price_minor": auction["venue"]["price_start_minor"], "fee_pool_minor": 0,
                           "initial_cash_minor": sum(a["cash_minor"] for a in accounts.values()), "initial_shares": sum(a["shares"] for a in accounts.values())}
            histories[key], hashers[key], aggregates[key] = [], hashlib.sha256(b"["), Counter()
            totals[key] = {name: dict(peak=w, max_drawdown=0.0, trades=0, fees_minor=0, risk_breach_sessions=0) for name, w in wealth[cid].items()}
        session = counts[key]
        if session >= len(joined[stock]):
            raise ValueError("background ledger has too many sessions")
        source = joined[stock][session]
        if (any(record[f] != source[f] for f in ("trade_date", "signal_cutoff_date", "execution_reference_date"))
                or record["auction"]["execution_available"] != source["execution_available"]):
            raise ValueError("background calendar or suspension differs from its source")
        text, uncertainty = (source["text_signal"], source["text_uncertainty"]) if enabled else (0.0, 0.0)
        evidence = source["text_evidence"] if enabled else "text:disabled"
        if record["text_signal_used"] != text or record["text_uncertainty_used"] != uncertainty or record["text_evidence"] != evidence:
            raise ValueError("background text ablation or visibility differs")
        check_feedback(record, histories[key], parent["feedback_parameters"])
        if any(record[f] != record["feedback"][f] for f in ("market_signal", "estimated_volatility")):
            raise ValueError("background feedback and observations differ")
        names = list(specs[cid])
        order = (names if core["order_mode"] == "forward" else list(reversed(names)) if core["order_mode"] == "reverse" else
                 sorted(names, key=lambda n: (hashlib.sha256(f"{core['seed']}:{session}:{n}".encode()).hexdigest(), n)))
        if record["arrival_order"] != order or any(o["sequence"] != order.index(o["owner"]) for o in record["auction"]["orders"]):
            raise ValueError("background order priority differs")
        strategy_names = {name for name, spec in specs[cid].items() if spec["kind"] == "strategy"}
        if any(set(record[f]) != strategy_names for f in ("observations", "decisions")):
            raise ValueError("strategy observations or decisions are incomplete")
        for name in strategy_names:
            observation, spec = record["observations"][name], specs[cid][name]
            signal = max(-1.0, min(1.0, record["market_signal"] * spec["profile"]["momentum_loading"] + spec["profile"]["market_bias"]))
            if (observation["step"] != session or observation["price"] != states[key]["price_minor"] / 100
                    or observation["market_signal"] != signal or observation["estimated_volatility"] != record["estimated_volatility"]
                    or observation["text_signal"] != text or observation["uncertainty"] != uncertainty or observation["text_evidence"] != evidence):
                raise ValueError("strategy observation differs from its causal profile")
        verify_background_demands(record, states[key], cases[cid], stock, session, auction["venue"], specs[cid])
        prior = states[key]
        states[key] = reconstruct_day(record, prior, auction["venue"], session)
        # Outcome-independent sampling: sessions 0,31,62,93 on every 124-day path.
        if session % 31 == 0:
            dense = dense_audit_day(record, prior, auction["venue"], session)
            if dense != states[key]:
                raise ValueError("interval audit differs from independent full-tick audit")
            dense_checks += 1
            dense_traded += int(record["auction"]["matched_volume"] > 0)
        for trade in record["auction"]["trades"]:
            kinds = {specs[cid][trade["buyer"]]["kind"], specs[cid][trade["seller"]]["kind"]}
            category = "strategy_background" if len(kinds) == 2 else "strategy_strategy" if kinds == {"strategy"} else "background_background"
            aggregates[key][category] += trade["quantity"]
        for item in record["auction"]["orders"]:
            kind = specs[cid][item["owner"]]["kind"]
            totals[key][item["owner"]]["trades"] += int(item["filled_quantity"] > 0)
            totals[key][item["owner"]]["fees_minor"] += item["fee_minor"]
            for field, target in (("quantity", "requested"), ("accepted_quantity", "accepted"), ("filled_quantity", "filled")):
                aggregates[key][f"{kind}_{target}"] += item[field]
            aggregates[key][f"{kind}_cash_clipped_orders"] += int("cash_and_fee_reservation" in item["reasons"])
            aggregates[key][f"{kind}_inventory_clipped_orders"] += int("sellable_inventory" in item["reasons"])
        for name, account in states[key]["accounts"].items():
            total = totals[key][name]
            value = account["cash_minor"] + account["shares"] * states[key]["price_minor"]
            total["peak"] = max(total["peak"], value)
            total["max_drawdown"] = max(total["max_drawdown"], 1 - value / total["peak"])
            if name in strategy_names:
                parameters = specs[cid][name]["parameters"]
                cap = min(parameters["max_weight"], parameters["risk_budget"] / record["estimated_volatility"])
                if record["decisions"][name]["risk_weight_cap"] != cap:
                    raise ValueError("strategy risk cap differs")
                total["risk_breach_sessions"] += int(account["shares"] * states[key]["price_minor"] / value > cap + 1e-9)
        histories[key].append({"trade_date": record["trade_date"], "price_before_minor": prior["price_minor"], "price_after_minor": states[key]["price_minor"]})
        if counts[key]:
            hashers[key].update(b", ")
        hashers[key].update(json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
        counts[key] += 1
        rows += 1
        traded += int(record["auction"]["matched_volume"] > 0)
        halted += int(not record["auction"]["execution_available"])
        aggregates[key]["trade_sessions"] += int(record["auction"]["matched_volume"] > 0)
        aggregates[key]["price_change_sessions"] += int(prior["price_minor"] != states[key]["price_minor"])
        aggregates[key]["blocked_sessions"] += int(not record["auction"]["execution_available"])
        if progress is not None and rows % 20000 == 0:
            progress(rows, summary["ledger_rows"])
    if set(states) != expected_keys or rows != summary["ledger_rows"]:
        raise ValueError("background ledger coverage is incomplete")
    for key, state in states.items():
        path, cid, aggregate = paths[key], key[1], aggregates[key]
        hashers[key].update(b"]")
        if (counts[key] != len(joined[key[0]]) or path["sessions"] != counts[key] or path["trace_sha256"] != hashers[key].hexdigest()
                or path["final_price_minor"] != state["price_minor"] or path["fee_pool_minor"] != state["fee_pool_minor"]
                or any(path[f] != aggregate[f] for f in ("trade_sessions", "price_change_sessions", "blocked_sessions"))
                or any(path["volume_by_counterparty"][f] != aggregate[f] for f in ("strategy_strategy", "strategy_background", "background_background"))
                or path["matched_volume"] != sum(path["volume_by_counterparty"].values())):
            raise ValueError("background trace or volume summary differs")
        for kind in ("strategy", "background"):
            for field in ("requested", "accepted", "filled", "cash_clipped_orders", "inventory_clipped_orders"):
                if path[f"{kind}_orders"][field] != aggregate[f"{kind}_{field}"]:
                    raise ValueError("participant fill or resource clipping summary differs")
            accepted = aggregate[f"{kind}_accepted"]
            if path[f"{kind}_orders"]["accepted_fill_fraction"] != (aggregate[f"{kind}_filled"] / accepted if accepted else 0.0):
                raise ValueError("participant fill fraction differs")
        if path["initial_cash_minor"] != state["initial_cash_minor"] or path["initial_shares"] != state["initial_shares"]:
            raise ValueError("saved initial resources differ from the declared budget")
        for name, account in state["accounts"].items():
            saved, total = path["account_summary"][name], totals[key][name]
            value = account["cash_minor"] + account["shares"] * state["price_minor"]
            if (any(saved[f] != v for f, v in account.items()) or saved["initial_wealth_minor"] != wealth[cid][name]
                    or saved["final_wealth_minor"] != value or saved["wealth_multiple"] != value / wealth[cid][name]
                    or saved["kind"] != specs[cid][name]["kind"] or saved["role"] != specs[cid][name].get("parameters", {}).get("role")
                    or any(saved[f] != total[f] for f in ("trades", "fees_minor", "max_drawdown", "risk_breach_sessions"))):
                raise ValueError("background final account differs from reconstructed trades")
        for role in ("aggressive", "conservative", "institutional"):
            names = [name for name, spec in specs[cid].items() if spec.get("parameters", {}).get("role") == role]
            final = sum(state["accounts"][name]["cash_minor"] + state["accounts"][name]["shares"] * state["price_minor"] for name in names)
            if path["role_wealth_multiple"][role] != final / sum(wealth[cid][name] for name in names):
                raise ValueError("role aggregate wealth differs from reconstructed accounts")
    if (any(file_sha256(directory / name) != info["sha256"] for name, info in manifest["artifacts"].items())
            or any(file_sha256(p) != digest for p, digest in bindings.items())
            or any(file_sha256(Path(p)) != digest for p, digest in inputs.items())):
        raise ValueError("background source, artifact or auditor changed during audit")
    audited = {"pipeline_version": VERSION, "status": "passed", "experiment_id": summary["experiment_id"], "paths": len(paths), "ledger_rows": rows,
               "traded_sessions": traded, "halted_sessions": halted, "interval_price_checks": rows,
               "sampled_full_tick_sessions": dense_checks, "sampled_traded_tick_sweeps": dense_traded,
               "checks": {"trade_reconstruction": True, "exact_resource_acceptance": True, "interval_clearing": True,
                          "sampled_dense_clearing": True, "causal_own_price": True, "background_target_and_orders": True,
                          "text_and_halt_sources": True, "counterparty_categories": True, "full_trace_and_accounts": True}}
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "background_audit.json"
    target.write_text(json.dumps(audited, ensure_ascii=False, indent=2), encoding="utf-8")
    binding = {"pipeline_version": VERSION, "run_manifest": str(manifest_path), "run_manifest_sha256": file_sha256(manifest_path),
               "config_path": str(config_path), "config_sha256": file_sha256(config_path), "code_sha256": file_sha256(Path(__file__)), "result_sha256": file_sha256(target)}
    (output_dir / "background_audit_manifest.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding="utf-8")
    return audited


def load_audit(directory, audit_dir):
    manifest = json.loads((audit_dir / "background_audit_manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("pipeline_version") != VERSION or manifest["code_sha256"] != file_sha256(Path(__file__))
            or Path(manifest["run_manifest"]).resolve() != (directory / "background_manifest.json").resolve()
            or manifest["run_manifest_sha256"] != file_sha256(directory / "background_manifest.json")
            or manifest["config_sha256"] != file_sha256(Path(manifest["config_path"]))
            or manifest["result_sha256"] != file_sha256(audit_dir / "background_audit.json")):
        raise ValueError("background audit binding changed")
    result = json.loads((audit_dir / "background_audit.json").read_text(encoding="utf-8"))
    if result.get("status") != "passed" or not result.get("checks") or any(v is not True for v in result["checks"].values()):
        raise ValueError("background audit did not pass every check")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit_directory(args.directory, args.config, args.output_dir,
          lambda done, total: print(f"Audited {done}/{total} rows", flush=True)), ensure_ascii=False))


if __name__ == "__main__":
    main()
