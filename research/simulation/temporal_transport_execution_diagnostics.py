"""Read-only order capacity and unchanged-price diagnosis for all 2022 cells."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "research/configs/temporal_transport_execution_diagnostics_2022_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_transport_execution_diagnostics_2022_v1"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def diagnose_call(call):
    """Separate finite accepted quantity from executable limit prices."""
    amounts = Counter()
    reasons = Counter()
    for order in call["orders"]:
        side = order["side"]
        if side not in {"buy", "sell"}:
            raise ValueError("Unknown order side")
        request, accepted, filled = (order[k] for k in ["quantity", "accepted_quantity", "filled_quantity"])
        if (any(type(value) is not int for value in [request, accepted, filled])
                or not 0 <= filled <= accepted <= request):
            raise ValueError("Invalid order quantities")
        for key, value in [("requested", request), ("accepted", accepted), ("filled", filled)]:
            amounts[side + "_" + key] += value
        if "cash_and_fee_reservation" in order["reasons"]:
            if side != "buy":
                raise ValueError("Cash clipping was attributed to a sell")
            amounts["buy_cash_and_fee_clipped"] += request - accepted
        if "sellable_inventory" in order["reasons"]:
            if side != "sell":
                raise ValueError("Inventory clipping was attributed to a buy")
            amounts["sell_inventory_clipped"] += request - accepted
        for reason in order["reasons"]:
            reasons[reason] += 1
    volume = call["matched_volume"]
    upper = min(amounts["buy_accepted"], amounts["sell_accepted"])
    if (type(volume) is not int or volume < 0 or volume > upper
            or amounts["buy_filled"] != volume or amounts["sell_filled"] != volume):
        raise ValueError("Matched volume and double-sided fills disagree")
    previous, after = call["price_before_minor"], call["price_after_minor"]
    if any(type(x) is not int or x <= 0 for x in [previous, after]):
        raise ValueError("Invalid auction prices")
    if type(call["execution_available"]) is not bool:
        raise ValueError("Unknown execution state")
    if not call["execution_available"]:
        if volume or previous != after:
            raise ValueError("A halted call executed or changed price")
        category = "halted_unchanged"
    elif volume > 0:
        category = "matched_unchanged" if previous == after else "matched_price_changed"
    else:
        if previous != after:
            raise ValueError("A zero-volume call changed price")
        category = "no_accepted_buy" if amounts["buy_accepted"] == 0 else "no_accepted_sell" if amounts["sell_accepted"] == 0 else "accepted_limits_do_not_cross"
        if category == "accepted_limits_do_not_cross":
            best_buy = max(x["limit_price_minor"] for x in call["orders"] if x["side"] == "buy" and x["accepted_quantity"])
            best_sell = min(x["limit_price_minor"] for x in call["orders"] if x["side"] == "sell" and x["accepted_quantity"])
            if best_buy >= best_sell:
                raise ValueError("Crossing accepted prices were reported as zero-volume")
    amounts["mechanical_accepted_quantity_upper_bound"] = upper
    amounts["matched_volume"] = volume
    return {"quantities": dict(amounts), "order_reasons": dict(reasons),
            "call_category": category, "price_unchanged": previous == after}


def seed(si):
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    for relative, expected in protocol["bindings"].items():
        if sha(ROOT / relative) != expected:
            raise ValueError("Frozen diagnostic input changed: " + relative)
    cfg = json.loads((ROOT / protocol["execution_config_path"]).read_text(encoding="utf-8"))
    if type(si) is not int or si not in range(5):
        raise ValueError("Unexpected seed index")
    destination = OUTPUT / f"seed_{si}.json"
    if destination.exists():
        raise RuntimeError("Preserve prior completed order diagnostics")
    conditions = []
    bindings = {}
    ledger_rows = asset_calls = 0
    base = ROOT / "research_outputs/temporal_transport_execution_2022_v1" / f"seed_{si}"
    for ci, cell in enumerate(cfg["variants"]):
        folder = base / f"cell_{ci}"
        checkpoint_path = folder / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint["protocol_sha256"] != protocol["execution_config_sha256"]:
            raise ValueError("Mixed numerical protocols")
        for name, digest in checkpoint["artifacts"].items():
            if sha(folder / name) != digest:
                raise ValueError("Bound raw condition artifact changed")
        bindings[checkpoint_path.relative_to(ROOT).as_posix()] = sha(checkpoint_path)
        saved = json.loads((folder / "condition.json").read_text(encoding="utf-8"))
        flows = Counter()
        quantities = Counter()
        categories = Counter()
        order_reasons = Counter()
        role_cash_clipped = Counter()
        call_count = 0
        actual_stock_dates = set()
        with gzip.open(folder / "ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
            for bi, basket in enumerate(cfg["baskets"]):
                specs = saved["paths"][bi]["participant_specs"]
                for session, day in enumerate(cfg["dates"]):
                    line = next(stream, None)
                    if line is None:
                        raise ValueError("Truncated diagnostic ledger")
                    row = json.loads(line)
                    if (row["seed_id"], row["variant"], row["basket_index"], row["trade_date"], row["portfolio_auction"]["session"]) != (cfg["seeds"][si]["seed_id"], cell["name"], bi, day, session):
                        raise ValueError("Raw ledger identity differs")
                    calls = row["portfolio_auction"]["asset_calls"]
                    if set(calls) != set(basket):
                        raise ValueError("Missing asset call")
                    for stock, call in calls.items():
                        if (stock, day) in actual_stock_dates:
                            raise ValueError("Duplicate company date")
                        actual_stock_dates.add((stock, day))
                        diagnostic = diagnose_call(call)
                        quantities.update(diagnostic["quantities"])
                        categories[diagnostic["call_category"]] += 1
                        order_reasons.update(diagnostic["order_reasons"])
                        call_count += 1
                        for order in call["orders"]:
                            spec = specs[order["owner"]]
                            role = "background" if spec["kind"] == "background" else spec["parameters"]["role"]
                            for key, field in [("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")]:
                                flows[f"{role}|{order['side']}|{key}"] += order[field]
                            if "cash_and_fee_reservation" in order["reasons"]:
                                amount = order["quantity"] - order["accepted_quantity"]
                                flows[f"{role}|{order['side']}|cash_clipped"] += amount
                                role_cash_clipped[role] += amount
                    ledger_rows += 1
            if next(stream, None) is not None:
                raise ValueError("Extra raw ledger rows")
        if (call_count != 7134 or len(actual_stock_dates) != 7134
                or dict(flows) != saved["variant"]["role_order_quantities"]
                or quantities["matched_volume"] != saved["variant"]["total_matched_volume"]):
            raise ValueError("Full condition raw flow scope or original totals differ")
        flat_count = sum(count for category, count in categories.items() if category != "matched_price_changed")
        expected_flat = saved["variant"]["comparison"]["synthetic"]["zero_return_fraction"]
        if not math.isclose(flat_count / call_count, expected_flat, rel_tol=0, abs_tol=1e-15):
            raise ValueError("Raw unchanged calls do not match original return statistics")
        conditions.append({"seed_id": cfg["seeds"][si]["seed_id"], "name": cell["name"],
                           "residual_dependence": cell["residual_dependence"], "delivery": cell["delivery"],
                           "risk_mode": cell["risk_mode"], "background_anchor": cell["background_anchor"],
                           "feedback_scale": cell["feedback_scale"], "asset_calls": call_count,
                           "quantities": dict(quantities), "categories": dict(categories),
                           "order_reasons": dict(order_reasons), "role_cash_clipped": dict(role_cash_clipped)})
        asset_calls += call_count
        print(f"Read-only raw order diagnosis seed {si + 1}/5 condition {ci + 1}/48 complete.", flush=True)
    if ledger_rows != 114144 or asset_calls != 342432 or len(conditions) != 48:
        raise ValueError("Incomplete per-seed diagnostic scope")
    record = {"status": "COMPLETE_RAW_ORDER_CAPACITY_AND_UNCHANGED_PRICE_DIAGNOSIS_SEED",
              "completed_at_utc": datetime.now(timezone.utc).isoformat(), "protocol_sha256": sha(PROTOCOL),
              "seed_index": si, "conditions": conditions, "ledger_rows": ledger_rows,
              "asset_calls": asset_calls, "source_checkpoint_bindings": bindings,
              "model_parameters_changed": False, "independent_new_market_validation": False}
    OUTPUT.mkdir(exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    seed(int(sys.argv[1]))
