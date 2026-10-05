"""Reconstruct quantity-flow summaries from archived orders using only stdlib."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_quantity_controls_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_quantity_flow_2019_v1.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def classify(decision: dict) -> str:
    if decision["risk_liquidation"]:
        return "risk_liquidation"
    if "confirmation_or_rebalance_wait" in decision["reasons"]:
        return "confirmation_or_rebalance_wait"
    return "ordinary_allocation"


def reconstruct_flow(call: dict, specs: dict) -> tuple[dict, dict, Counter, Counter]:
    """Sum actual submitted, accepted and filled quantities, never target weights."""
    total = Counter()
    fields = {stage + "_net": 0 for stage in ("requested", "accepted", "filled")}
    by_kind = {kind: Counter(fields) for kind in ("strategy", "background")}
    by_role = {}
    owner_net, submitted = Counter(), Counter()
    for order in call["orders"]:
        side = order["side"]
        if side not in {"buy", "sell"}:
            raise ValueError("quantity-flow order has an unknown side")
        quantities = [order[key] for key in ("quantity", "accepted_quantity", "filled_quantity")]
        if (any(type(value) is not int for value in quantities)
                or not 0 <= quantities[2] <= quantities[1] <= quantities[0]):
            raise ValueError("quantity-flow order quantities do not balance")
        spec = specs[order["owner"]]
        sign = 1 if side == "buy" else -1
        for stage, value in zip(("requested", "accepted", "filled"), quantities):
            total[stage + "_" + side] += value
            by_kind[spec["kind"]][stage + "_net"] += sign * value
            if spec["kind"] == "strategy":
                role = spec["parameters"]["role"]
                by_role.setdefault(role, Counter(fields))[stage + "_net"] += sign * value
        owner_net[order["owner"]] += sign * quantities[2]
        submitted[order["owner"]] += 1
    if (total["filled_buy"] != total["filled_sell"]
            or total["filled_buy"] != call["matched_volume"]
            or by_kind["strategy"]["filled_net"] + by_kind["background"]["filled_net"] != 0
            or any(value > 1 for value in submitted.values())):
        raise ValueError("quantity-flow trade conservation or owner coverage differs")
    return dict(total), {"kind": by_kind, "role": by_role}, owner_net, submitted


def compute() -> dict:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    bindings = {str(SOURCE / "manifest.json"): digest(SOURCE / "manifest.json")}
    for field in ("inputs", "code_sha256"):
        bindings.update(manifest[field])
    for name, info in manifest["artifacts"].items():
        path = SOURCE / name
        if path.parent != SOURCE:
            raise ValueError("quantity-flow artifact escaped its source directory")
        bindings[str(path)] = info["sha256"]
    if any(digest(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("quantity-flow input, source code or archive hash differs")
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    variants = {item["name"]: item for item in result["variants"]}
    paths = {(item["variant"], item["basket_index"]): item for item in result["path_summaries"]}
    rows = {name: {(row["stock_code"], row["trade_date"]): row for row in item["daily_asset_rows"]}
            for name, item in variants.items()}
    if len(variants) != 4 or len(paths) != 164 or any(len(panel) != 5289 for panel in rows.values()):
        raise ValueError("quantity-flow frozen panel coverage differs")
    branches = {name: Counter() for name in variants}
    branch_orders = {name: Counter() for name in variants}
    inventory = {name: Counter() for name in variants}
    groups = {name: {sign: {"totals": Counter(), "returns": []} for sign in ("positive", "negative", "zero")}
              for name in variants}
    initial_background, final_accounts, seen_days, seen_assets = {}, {}, set(), set()
    path_sessions = Counter()
    ledger_records = order_records = 0
    with gzip.open(SOURCE / "ledger.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            day = json.loads(line)
            name, basket, date = day["variant"], day["basket_index"], day["trade_date"]
            path_key = (name, basket)
            day_key = (*path_key, date)
            if day_key in seen_days or path_key not in paths:
                raise ValueError("quantity-flow ledger path omitted or duplicated")
            if day["portfolio_auction"]["session"] != path_sessions[path_key]:
                raise ValueError("quantity-flow path sessions are not in chronological order")
            path_sessions[path_key] += 1
            seen_days.add(day_key)
            specs = paths[path_key]["participant_specs"]
            for owner, decision in day["decisions"].items():
                branches[name][specs[owner]["parameters"]["role"] + ":" + classify(decision)] += 1
            for stock, call in day["portfolio_auction"]["asset_calls"].items():
                asset_key = (name, stock, date)
                if asset_key in seen_assets:
                    raise ValueError("quantity-flow company-day duplicated")
                seen_assets.add(asset_key)
                row = rows[name][stock, date]
                totals, flow, owner_net, _ = reconstruct_flow(call, specs)
                for field in ("requested_buy", "requested_sell", "accepted_buy", "accepted_sell", "filled_buy", "filled_sell"):
                    if row[field] != totals.get(field, 0):
                        raise ValueError("quantity-flow published total differs from archived orders")
                if any(row[field] != call[field] for field in ("matched_volume", "price_before_minor", "price_after_minor")):
                    raise ValueError("quantity-flow published price or volume differs from archived call")
                for kind in ("strategy", "background"):
                    for stage in ("requested", "accepted", "filled"):
                        field = kind + ("_net" if stage == "accepted" else "_" + stage + "_net")
                        if row[field] != flow["kind"][kind][stage + "_net"]:
                            raise ValueError("quantity-flow participant net differs from archived orders")
                reported_roles = {role: dict(values) for role, values in flow["role"].items() if any(values.values())}
                if row["strategy_role_flow"] != reported_roles:
                    raise ValueError("quantity-flow published role net differs from archived orders")
                demands = day["background_demands"][stock]
                if day["portfolio_auction"]["session"] == 0:
                    initial_background[name, basket, stock] = sum(value["current_shares"] for value in demands.values())
                actual_inventory = {"current_shares": sum(value["current_shares"] for value in demands.values()),
                                    "target_shares": sum(value["target_shares"] for value in demands.values()),
                                    "reference_initial_shares": initial_background[name, basket, stock],
                                    "requested_buy": sum(value["requested_quantity"] for value in demands.values() if value["side"] == "buy"),
                                    "requested_sell": sum(value["requested_quantity"] for value in demands.values() if value["side"] == "sell")}
                if actual_inventory != row["background_inventory_diagnostic"]:
                    raise ValueError("quantity-flow inventory summary differs from archived demands")
                inventory[name].update(actual_inventory)
                inventory[name]["asset_days"] += 1
                for owner, demand in demands.items():
                    after = day["portfolio_auction"]["accounts"][owner]["shares"][stock]
                    if after != demand["current_shares"] + owner_net[owner]:
                        raise ValueError("quantity-flow background holdings disagree with actual filled orders")
                message = result["issuer_design"]["by_stock"][stock][day["portfolio_auction"]["session"]]
                if message["trade_date"] != date or message["valuation_shift_bps"] != row["hypothetical_issuer_valuation_bps"]:
                    raise ValueError("quantity-flow company message changed")
                sign = "positive" if message["valuation_shift_bps"] > 0 else "negative" if message["valuation_shift_bps"] < 0 else "zero"
                group = groups[name][sign]
                group["returns"].append(Fraction(call["price_after_minor"] - call["price_before_minor"], call["price_before_minor"]))
                group["totals"].update(totals)
                for kind, values in flow["kind"].items():
                    group["totals"].update({kind + ":" + key: value for key, value in values.items()})
                for order in call["orders"]:
                    owner = order["owner"]
                    spec = specs[owner]
                    if spec["kind"] == "strategy":
                        label = spec["parameters"]["role"] + ":" + classify(day["decisions"][owner]) + ":" + order["side"]
                        for stage, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                            branch_orders[name][label + ":" + stage] += order[field]
                    receipt = day["issuer_information_receipts"][owner][stock]
                    status = "received" if receipt["received"] else "unreceived"
                    group["totals"][spec["kind"] + ":" + order["side"] + ":" + status + ":filled"] += order["filled_quantity"]
                    order_records += 1
            final_accounts[path_key] = day["portfolio_auction"]["accounts"]
            ledger_records += 1
    if (len(seen_days) != 7052 or len(seen_assets) != 21156 or len(initial_background) != 492
            or any(value != 43 for value in path_sessions.values())):
        raise ValueError("quantity-flow archived day or initial-stock coverage differs")
    output_variants = []
    for name, published in variants.items():
        if dict(branches[name]) != published["strategy_decision_branches"] or dict(branch_orders[name]) != published["strategy_branch_order_quantities"]:
            raise ValueError("quantity-flow decision branch or branch order tally differs")
        initial = sum(value for key, value in initial_background.items() if key[0] == name)
        final = 0
        for key, accounts in final_accounts.items():
            if key[0] != name:
                continue
            for owner, account in accounts.items():
                if account["shares"] != paths[key]["summary"]["accounts"][owner]["shares"]:
                    raise ValueError("quantity-flow final owner shares differ from published path")
                if paths[key]["participant_specs"][owner]["kind"] == "background":
                    final += sum(account["shares"].values())
        signed = {}
        all_returns = []
        for sign, group in groups[name].items():
            returns = group["returns"]
            size = len(returns)
            all_returns.extend(returns)
            signed[sign] = {"company_days": size, "mean_return_bps": float(sum(returns) * 10000 / size) if size else None,
                            "positive_return_fraction": sum(value > 0 for value in returns) / size if size else None,
                            "negative_return_fraction": sum(value < 0 for value in returns) / size if size else None,
                            "zero_return_fraction": sum(value == 0 for value in returns) / size if size else None,
                            "order_quantities": dict(group["totals"])}
        net_background = sum(group["totals"]["background:filled_net"] for group in groups[name].values())
        if initial != published["initial_background_shares"] or final != published["final_background_shares"] or final - initial != net_background:
            raise ValueError("quantity-flow background net stock accumulation differs")
        mean_bps = float(sum(all_returns) * 10000 / len(all_returns))
        if not math.isclose(mean_bps / 10000, published["comparison"]["synthetic"]["mean"], rel_tol=0, abs_tol=1e-12):
            raise ValueError("quantity-flow rational return mean differs")
        filled_sells = sum(value for key, value in branch_orders[name].items() if key.endswith(":sell:filled"))
        risk_sells = sum(value for key, value in branch_orders[name].items() if key.endswith(":risk_liquidation:sell:filled"))
        output_variants.append({"name": name, "mean_return_bps": mean_bps, "by_message_sign": signed,
                                "strategy_branches": dict(branches[name]), "strategy_branch_order_quantities": dict(branch_orders[name]),
                                "risk_liquidation_share_of_strategy_filled_sells": risk_sells / filled_sells if filled_sells else None,
                                "background_daily_mean": {key: value / inventory[name]["asset_days"] for key, value in inventory[name].items() if key != "asset_days"},
                                "initial_background_shares": initial, "final_background_shares": final, "background_filled_net": net_background})
    return {"pipeline_version": "pre-wuhan-quantity-flow-stdlib-ledger-audit-v1", "portfolio_records": ledger_records,
            "company_days": len(seen_assets), "submitted_orders_checked": order_records, "variants": output_variants,
            "interpretation": "Independent reconstruction of archived quantities, branches, holdings and signed returns. Full-path outcomes differ in intervened inventories and strategy waits. Message signs are synthetic and consumed development data are not blind evidence; conditional sign averages are descriptive, not policy effects.",
            "inputs": dict(sorted(bindings.items())), "code_sha256": digest(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("quantity-flow output must be directly inside research_outputs")
    result = compute()
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if output.read_bytes() != payload:
            raise ValueError("quantity-flow archive differs byte-for-byte")
    else:
        with output.open("xb") as handle:
            handle.write(payload)
    print(json.dumps({"portfolio_records": result["portfolio_records"], "company_days": result["company_days"],
                      "submitted_orders_checked": result["submitted_orders_checked"], "variants": [
                          {"name": row["name"], "mean_return_bps": row["mean_return_bps"],
                           "positive_message_return_bps": row["by_message_sign"]["positive"]["mean_return_bps"],
                           "negative_message_return_bps": row["by_message_sign"]["negative"]["mean_return_bps"],
                           "risk_liquidation_share_of_strategy_filled_sells": row["risk_liquidation_share_of_strategy_filled_sells"]}
                          for row in result["variants"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
