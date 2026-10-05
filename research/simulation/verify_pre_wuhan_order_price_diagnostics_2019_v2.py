"""Independent v2 replay with a frozen, non-executed renderer environment exception."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs
from .portfolio_audit import audit_portfolio_day
from .run_pre_wuhan_order_price_diagnostics_replay_v2 import (CONFIG, OUTPUT as SOURCE, ROOT,
    REPLAY_CONFIG, RENDER_PATH, HISTORICAL_RENDER_SHA256, OBSERVED_RENDER_SHA256,
    load_source, source_books, replay_scope)

OUTPUT = ROOT / "research_outputs/pre_wuhan_order_price_statistics_2019_v2.json"


def independent_book(call, tick, specs, decisions, receipts, stock):
    """Directly enumerate eligible orders at all book breakpoints; no event sweep."""
    prior, price = call["price_before_minor"], call["price_after_minor"]
    low, high = call["price_bounds_minor"]
    orders = call["orders"]
    accepted = [o for o in orders if o["accepted_quantity"]]
    points = sorted({low, high + tick} | {o["limit_price_minor"] + (tick if o["side"] == "buy" else 0)
                                        for o in accepted if low <= o["limit_price_minor"] + (tick if o["side"] == "buy" else 0) <= high})
    def eligible(p):
        buy = sum(o["accepted_quantity"] for o in accepted if o["side"] == "buy" and o["limit_price_minor"] >= p)
        sell = sum(o["accepted_quantity"] for o in accepted if o["side"] == "sell" and o["limit_price_minor"] <= p)
        return buy, sell
    curve = []
    for i in range(len(points) - 1):
        b, s = eligible(points[i])
        curve.append({"lower_minor": points[i], "upper_minor": points[i + 1] - tick, "demand": b, "supply": s})
    best = min((-min(r["demand"], r["supply"]), abs(r["demand"] - r["supply"])) for r in curve)
    optimal = [r for r in curve if (-min(r["demand"], r["supply"]), abs(r["demand"] - r["supply"])) == best]
    nearest = [prior if r["lower_minor"] <= prior <= r["upper_minor"] else
               r["upper_minor"] if r["upper_minor"] < prior else r["lower_minor"] for r in optimal]
    selected = sorted(nearest, key=lambda p: (abs(p - prior), p))[0] if best[0] else prior
    pb, ps = eligible(prior)
    cb, cs = eligible(price)
    if selected != price or -best[0] != call["matched_volume"] or cb - cs != call["clearing_imbalance"]:
        raise ValueError("independent book objectives differ from archived clearing")
    movement = "up" if price > prior else "down" if price < prior else "flat"
    if not call["execution_available"]:
        why = "halted"
    elif best[0] == 0:
        why = "no_match"
    elif price == prior:
        why = "prior_optimal"
    elif -best[0] > min(pb, ps):
        why = "volume_requires_move"
    elif best[1] < abs(pb - ps):
        why = "imbalance_requires_move"
    else:
        raise ValueError("independent moved book has no objective improvement")
    sides = {"below" if r["upper_minor"] < prior else "above" if r["lower_minor"] > prior else "prior" for r in optimal}
    geo = ("not_applicable_no_match" if not -best[0] else "prior_in_optimal_set" if "prior" in sides else
           "both_sides" if sides == {"above", "below"} else "only_above" if "above" in sides else "only_below")
    decoded = []
    for o in orders:
        name, s = o["owner"], o["side"]
        spec = specs[name]
        if spec["kind"] == "background":
            role, profile, branch = "background", None, "inventory_target"
            if spec["asset"] != stock:
                raise ValueError("independent background asset differs")
        else:
            role, profile = spec["parameters"]["role"], int(name.split("_")[-1])
            d = decisions[name]
            branch = "ordinary"
            if "confirmation_or_rebalance_wait" in d["reasons"]:
                branch = "wait"
            if d["risk_liquidation"]:
                branch = "risk_liquidation"
        decoded.append({"owner": name, "role": role, "profile": profile, "branch": branch,
            "received": receipts[name][stock]["received"], "side": s, "requested": o["quantity"],
            "accepted": o["accepted_quantity"], "filled": o["filled_quantity"],
            "quote_minus_prior_minor": o["limit_price_minor"] - prior,
            "eligible_at_prior": (o["limit_price_minor"] - prior) * (1 if s == "buy" else -1) >= 0,
            "eligible_at_clear": (o["limit_price_minor"] - price) * (1 if s == "buy" else -1) >= 0,
            "cash_clipped": "cash_and_fee_reservation" in o["reasons"],
            "inventory_clipped": "sellable_inventory" in o["reasons"]})
    raw_digest = hashlib.sha256(json.dumps(call, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    return {"source_call_sha256": raw_digest, "prior_minor": prior, "clearing_minor": price,
            "direction": movement, "criterion": why, "optimal_geometry": geo,
            "lower_price_equidistant_tie": bool(-best[0] and price != prior and any(
                p != price and abs(p - prior) == abs(price - prior) for p in nearest)),
            "maximum_volume": -best[0], "prior_volume": min(pb, ps), "prior_demand": pb, "prior_supply": ps,
            "clearing_demand": cb, "clearing_supply": cs, "minimum_abs_imbalance": best[1],
            "optimal_intervals": optimal, "execution_available": call["execution_available"], "orders": decoded}


def add_quantities(target, o):
    s, a, displacement = o["side"], o["accepted"], o["quote_minus_prior_minor"]
    fields = {"orders_" + s: 1, "requested_" + s: o["requested"],
              "accepted_" + s: a, "filled_" + s: o["filled"]}
    place = "equal" if displacement == 0 else "above" if displacement > 0 else "below"
    fields["accepted_" + s + "_quote_" + place] = a
    fields["requested_" + s + "_quote_" + place] = o["requested"]
    fields["eligible_at_prior_" + s] = a * o["eligible_at_prior"]
    fields["eligible_at_clear_" + s] = a * o["eligible_at_clear"]
    fields["entering_" + s] = a * (o["eligible_at_clear"] and not o["eligible_at_prior"])
    fields["exiting_" + s] = a * (o["eligible_at_prior"] and not o["eligible_at_clear"])
    fields["cash_clipped_" + s] = (o["requested"] - a) * o["cash_clipped"]
    fields["inventory_clipped_" + s] = (o["requested"] - a) * o["inventory_clipped"]
    for k, v in fields.items():
        if v:
            target[k] = target.get(k, 0) + v


class IndependentTotals:
    def __init__(self):
        self.groups = {}
        self.values, self.categories, self.quotes = defaultdict(list), defaultdict(lambda: defaultdict(list)), defaultdict(lambda: defaultdict(list))
        self.phases = defaultdict(lambda: defaultdict(list))

    def add(self, row):
        key = row["seed_id"] + "|" + row["variant"]
        if key not in self.groups:
            self.groups[key] = {"books": 0, "directions": {}, "criteria": {}, "geometry": {},
                "equidistant_lower_ties": 0, "matched_volume": 0, "role_orders": {}, "role_direction_orders": {},
                "order_contexts": {}, "net_requested_by_direction": {}, "net_accepted_by_direction": {}, "session_phases": {}}
        g, d = self.groups[key], row["diagnostic"]
        direction = d["direction"]
        value = (d["clearing_minor"] - d["prior_minor"]) / d["prior_minor"]
        category = d["criterion"] + "|" + direction
        g["books"] += 1
        g["matched_volume"] += d["maximum_volume"]
        g["equidistant_lower_ties"] += int(d["lower_price_equidistant_tie"])
        for field, label in (("directions", direction), ("criteria", category), ("geometry", d["optimal_geometry"] + "|" + direction)):
            g[field][label] = g[field].get(label, 0) + 1
        self.values[key].append(value)
        self.categories[key][category].append(value)
        phase = "initial" if row["session"] == 0 else "warmup_1_9" if row["session"] <= 9 else "later_10_42"
        p = g["session_phases"].setdefault(phase, {"books": 0, "criteria": {}, "orders": {}})
        p["books"] += 1
        p["criteria"][category] = p["criteria"].get(category, 0) + 1
        self.phases[key][phase].append(value)
        for o in d["orders"]:
            role = o["role"]
            context = f"{direction}|{role}|{o['profile']}|{o['branch']}|{o['received']}"
            for field, label in (("role_orders", role), ("role_direction_orders", direction + "|" + role), ("order_contexts", context)):
                add_quantities(g[field].setdefault(label, {}), o)
            add_quantities(p["orders"], o)
            if o["accepted"] > 0:
                self.quotes[key][role + "|" + o["side"]].append(o["accepted"] * o["quote_minus_prior_minor"] / d["prior_minor"])
        for stage in ("requested", "accepted"):
            delta = sum((1 if o["side"] == "buy" else -1) * o[stage] for o in d["orders"])
            sign = "balanced" if delta == 0 else "sell_excess" if delta < 0 else "buy_excess"
            bucket = g["net_" + stage + "_by_direction"]
            label = direction + "|" + sign
            bucket[label] = bucket.get(label, 0) + 1

    def finish(self):
        for key, g in self.groups.items():
            n = g["books"]
            g["mean_return"] = math.fsum(self.values[key]) / n
            g["return_categories"] = {k: {"books": len(v), "conditional_mean": math.fsum(v) / len(v),
                "contribution_to_all_book_mean": math.fsum(v) / n} for k, v in self.categories[key].items()}
            g["accepted_quantity_weighted_quote_return"] = {k: math.fsum(v) / g["role_orders"][k.split("|")[0]]["accepted_" + k.split("|")[1]]
                                                           for k, v in self.quotes[key].items()}
            for phase, p in g["session_phases"].items():
                p["mean_return"] = math.fsum(self.phases[key][phase]) / p["books"]
        return self.groups


def independent_measures(g):
    n = g["books"]
    down = g["directions"].get("down", 0)
    req_buy = sum(v.get("requested_buy", 0) for v in g["role_orders"].values())
    req_sell = sum(v.get("requested_sell", 0) for v in g["role_orders"].values())
    accepted_buy = sum(v.get("accepted_buy", 0) for v in g["role_orders"].values())
    accepted_sell = sum(v.get("accepted_sell", 0) for v in g["role_orders"].values())
    return {"mean_return": g["mean_return"], "down_fraction": down / n,
        "up_fraction": g["directions"].get("up", 0) / n, "flat_fraction": g["directions"].get("flat", 0) / n,
        "volume_down_fraction": g["criteria"].get("volume_requires_move|down", 0) / n,
        "imbalance_down_fraction": g["criteria"].get("imbalance_requires_move|down", 0) / n,
        "no_match_fraction": g["criteria"].get("no_match|flat", 0) / n,
        "halted_fraction": g["criteria"].get("halted|flat", 0) / n,
        "trading_flat_fraction": g["criteria"].get("prior_optimal|flat", 0) / n,
        "down_with_requested_buy_excess_fraction": g["net_requested_by_direction"].get("down|buy_excess", 0) / down if down else None,
        "down_with_accepted_buy_excess_fraction": g["net_accepted_by_direction"].get("down|buy_excess", 0) / down if down else None,
        "buy_acceptance_fraction": accepted_buy / req_buy if req_buy else None,
        "sell_acceptance_fraction": accepted_sell / req_sell if req_sell else None}


def compare(a, b, path="root"):
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            raise ValueError("independent summary keys differ: " + path)
        for k in a:
            compare(a[k], b[k], path + "." + k)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            raise ValueError("independent list length differs: " + path)
        for i, (x, y) in enumerate(zip(a, b)):
            compare(x, y, path + "." + str(i))
    elif type(a) is float and type(b) is float:
        if not math.isfinite(a) or not math.isfinite(b) or not math.isclose(a, b, abs_tol=1e-12, rel_tol=1e-12):
            raise ValueError("independent floating statistic differs: " + path)
    elif type(a) is not type(b) or a != b:
        raise ValueError("independent exact value differs: " + path)


def run(audit_existing=False):
    if OUTPUT.exists() and not audit_existing:
        raise ValueError("independent diagnostics require a fresh output")
    cfg, bindings, result, base = load_source()
    for name in ("books.jsonl.gz", "summary.json", "manifest.json"):
        bindings[str(SOURCE / name)] = file_sha256(SOURCE / name)
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    compare(manifest["inputs"], {k: v for k, v in bindings.items() if k not in {str(SOURCE / n) for n in ("books.jsonl.gz", "summary.json", "manifest.json")}})
    for name, info in manifest["artifacts"].items():
        if info["sha256"] != file_sha256(SOURCE / name) or info["bytes"] != (SOURCE / name).stat().st_size:
            raise ValueError("diagnostic artifact binding differs")
    totals, states, count, orders, settlements, dense = IndependentTotals(), {}, 0, 0, 0, 0
    case = next(c for c in base["cases"] if c["case_id"] == "separated_institution35")
    with gzip.open(SOURCE / "books.jsonl.gz", "rt", encoding="utf-8") as stream:
        for day, stock, call, specs in source_books(result):
            key = day["seed_id"], day["variant"], day["basket_index"]
            session = day["portfolio_auction"]["session"]
            assets = sorted(day["portfolio_auction"]["asset_calls"])
            if stock == assets[0]:
                if session == 0:
                    states[key] = initial_from_specs(specs, assets, base, case)
                states[key] = audit_portfolio_day(day, states[key], base["venue"], session,
                                                  dense=session in cfg["dense_check_sessions"])
                settlements += 1
                dense += len(assets) if session in cfg["dense_check_sessions"] else 0
            line = next(stream, None)
            if line is None:
                raise ValueError("diagnostic ledger ended before the source")
            row = json.loads(line)
            expected = {k: day[k] for k in ("seed_id", "variant", "basket_index", "trade_date")}
            expected.update(stock_code=stock, session=session, diagnostic=independent_book(call,
                base["venue"]["tick_minor"], specs, day["decisions"], day["issuer_information_receipts"], stock))
            compare(row, expected, "book." + str(count))
            totals.add(expected)
            count += 1
            orders += len(call["orders"])
            if session == 42 and stock == assets[-1]:
                states.pop(key)
            if count % 15000 == 0:
                print(f"independently audited {count} accepted books and {settlements} real settlements", flush=True)
        if next(stream, None) is not None or states:
            raise ValueError("diagnostic ledger has extra rows or unfinished states")
    groups = totals.finish()
    summary = json.loads((SOURCE / "summary.json").read_text(encoding="utf-8"))
    compare(groups, summary["conditions"], "conditions")
    measures = {key: independent_measures(g) for key, g in groups.items()}
    compare(measures, summary["condition_measures"], "condition_measures")
    seeds = [s["seed_id"] for s in result["seeds"]]
    medians = {}
    for variant in {v["name"] for v in result["variants"]}:
        medians[variant] = {}
        for field in next(iter(measures.values())):
            values = sorted(measures[seed + "|" + variant][field] for seed in seeds if measures[seed + "|" + variant][field] is not None)
            mid = len(values) // 2
            medians[variant][field] = (values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2) if values else None
    compare(medians, summary["seed_column_medians"], "seed_medians")
    coverage = {"conditions": len(groups), "paths": settlements // 43, "basket_days": settlements, "asset_books": count}
    compare(coverage, cfg["expected_coverage"], "coverage")
    compare(coverage, summary["coverage"], "summary.coverage")
    if orders != summary["orders"] or dense != len(result["path_summaries"]) * len(cfg["dense_check_sessions"]) * 3:
        raise ValueError("independent order/full-tick coverage differs")
    source_difference = 0.0
    for v in result["variants"]:
        g = groups[v["seed_id"] + "|" + v["name"]]
        delta = abs(g["mean_return"] - v["comparison"]["synthetic"]["mean"])
        source_difference = max(source_difference, delta)
        if delta > 1e-12 or g["matched_volume"] != v["total_matched_volume"]:
            raise ValueError("independent means/volumes differ from the original factorial")
    # All v1 numeric/statistical fields must remain identical; no weaker objectives.
    original = json.loads((ROOT / "research_outputs/pre_wuhan_order_price_statistics_2019_v1.json").read_text(encoding="utf-8"))
    numeric = {"coverage": coverage, "orders": orders, "real_wallet_settlements_reaudited": settlements,
               "full_tick_asset_calls": dense, "source_mean_max_absolute_difference": source_difference,
               "condition_measures": measures, "seed_column_medians": medians,
               "independent_summary_groups_matched": len(groups), "interpretation": cfg["interpretation"]}
    for field, value in numeric.items():
        compare(value, original[field], "v1_bridge." + field)
    scope = replay_scope()
    bindings[str(RENDER_PATH)] = OBSERVED_RENDER_SHA256
    bindings[str(REPLAY_CONFIG)] = file_sha256(REPLAY_CONFIG)
    for name, expected in scope["inputs"].items():
        name = str((ROOT / name).resolve()) if not Path(name).is_absolute() else name
        if name in bindings and bindings[name] != expected:
            raise ValueError("v2 replay scope shadows a numerical binding")
        bindings[name] = expected
    receipt = {"version": "independent-accepted-book-order-price-audit-v2", "status": "PASS",
        "inputs": bindings, "coverage": coverage, "orders": orders,
        "real_wallet_settlements_reaudited": settlements, "full_tick_asset_calls": dense,
        "source_mean_max_absolute_difference": source_difference, "condition_measures": measures,
        "seed_column_medians": medians, "independent_summary_groups_matched": len(groups),
        "interpretation": cfg["interpretation"],
        "v1_numeric_fields_matched": sorted(numeric),
        "historical_renderer_binding_exception": {"path": str(RENDER_PATH),
            "historical_sha256": HISTORICAL_RENDER_SHA256, "observed_sha256": OBSERVED_RENDER_SHA256,
            "executed_in_this_replay": False, "cause_verified": False,
            "scope": "JSON/gzip accepted-book analysis only; no source PDF parsing or rendering."}}
    data = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if audit_existing:
        if OUTPUT.read_bytes() != data:
            raise ValueError("independent accepted-book statistics did not rebuild byte-identically")
        print("Independent accepted-book statistics rebuilt byte-identically.", flush=True)
    else:
        OUTPUT.write_bytes(data)
        print("Independent accepted-book objectives, order groups and real wallets audited.", flush=True)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    run(parser.parse_args().audit_existing)
