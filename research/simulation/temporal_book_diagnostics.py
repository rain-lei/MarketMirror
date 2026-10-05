"""Full-grid archived-book diagnosis; alternatives hold the accepted book fixed."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import statistics
import tempfile

from .temporal_information_study import (
    ROOT, CONFIG as SOURCE_CONFIG, OUTPUT as SOURCE, CELLS, read, encoded,
    require, checkpoint, bindings_ok,
)
from .order_price_diagnostics import diagnose_book, order_metrics
from .verify_pre_wuhan_order_price_diagnostics_2019 import independent_book, add_quantities
from .audit_background import interval_price
from ..data_pipeline.provenance import file_sha256

CONFIG = ROOT / "research/configs/temporal_book_diagnostics_2021_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_book_diagnostics_2021_v1"
RULES = ("nearest_prior", "sse_midpoint", "accepted_order_pressure")


def alternative_prices(diagnostic, tick):
    """Select from independently enumerated intervals, retaining both objectives."""
    prior, optimal = diagnostic["prior_minor"], diagnostic["optimal_intervals"]
    nearest = diagnostic["clearing_minor"]
    if not diagnostic["maximum_volume"]:
        return dict.fromkeys(RULES, prior)
    first = min(r["lower_minor"] for r in optimal)
    last = max(r["upper_minor"] for r in optimal)
    midpoint = ((first // tick + last // tick + 1) // 2) * tick
    pressure = sum(o["accepted"] * (1 if o["side"] == "buy" else -1) for o in diagnostic["orders"])
    endpoint = last if pressure > 0 else first if pressure < 0 else nearest
    prices = dict(zip(RULES, (nearest, midpoint, endpoint)))
    require(all(any(r["lower_minor"] <= p <= r["upper_minor"] for r in optimal) for p in prices.values()),
            "same-book alternative left the first/second objective optimum")
    return prices


def scope_group():
    return {"books": 0, "counts": Counter(), "role_quantities": defaultdict(Counter),
            "branch_quantities": defaultdict(Counter), "same_book_rules": defaultdict(Counter)}


def add_group(group, d, prices, tick):
    group["books"] += 1
    c = group["counts"]
    c[d["criterion"]] += 1
    c["direction_" + d["direction"]] += 1
    c["geometry_" + d["optimal_geometry"]] += 1
    c["matched_volume"] += d["maximum_volume"]
    c["execution_available"] += int(d["execution_available"])
    ticks = sum((r["upper_minor"] - r["lower_minor"]) // tick + 1 for r in d["optimal_intervals"])
    if d["maximum_volume"]:
        c["positive_volume"] += 1
        c["unique_optimal_tick"] += int(ticks == 1)
        c["multiple_optimal_ticks"] += int(ticks > 1)
        if d["direction"] == "flat":
            c["traded_flat_unique_prior"] += int(ticks == 1)
            c["traded_flat_multiple_optima"] += int(ticks > 1)
    for rule, p in prices.items():
        r = group["same_book_rules"][rule]
        r["flat_books"] += int(p == d["prior_minor"])
        r["price_changed_from_saved"] += int(p != d["clearing_minor"])
        r["positive_volume_flat"] += int(p == d["prior_minor"] and d["maximum_volume"] > 0)
        r["traded_flat_moved"] += int(d["direction"] == "flat" and d["maximum_volume"] > 0 and p != d["prior_minor"])
        r["up_books"] += int(p > d["prior_minor"])
        r["down_books"] += int(p < d["prior_minor"])
    for o in d["orders"]:
        q = order_metrics(o)
        independently_added = {}
        add_quantities(independently_added, o)
        require(dict(q) == independently_added, "independent per-order quantity classification differs")
        group["role_quantities"][o["role"]].update(q)
        group["branch_quantities"][o["role"] + "|" + o["branch"]].update(q)


def finish_group(group):
    n, c = group["books"], group["counts"]
    flat = c["direction_flat"]
    traded_flat = c["prior_optimal"]
    return {"books": n, "counts": dict(c),
            "fractions": {"zero_return": flat / n,
                "no_trade": (c["halted"] + c["no_match"]) / n,
                "traded_flat": traded_flat / n,
                "positive_volume_among_zero": traded_flat / flat if flat else None,
                "multiple_optima_among_traded_flat": c["traded_flat_multiple_optima"] / traded_flat if traded_flat else None},
            "role_quantities": {k: dict(v) for k, v in group["role_quantities"].items()},
            "branch_quantities": {k: dict(v) for k, v in group["branch_quantities"].items()},
            "same_book_rules": {k: {**dict(v), "flat_fraction": v["flat_books"] / n,
                "traded_flat_fraction_moved": v["traded_flat_moved"] / traded_flat if traded_flat else None}
                for k, v in group["same_book_rules"].items()}}


def load():
    cfg, source = read(CONFIG), read(SOURCE_CONFIG)
    require(cfg["version"] == "temporal-full-accepted-book-diagnosis-2021-v1"
            and cfg["preserve_full_scope"] is True and cfg["is_full_path_counterfactual"] is False,
            "book diagnosis protocol differs")
    for p, h in cfg["bindings"].items():
        require(file_sha256(ROOT / p) == h, "frozen book diagnostic source changed: " + p)
    bindings_ok(source)
    return cfg, source


def seed(si):
    cfg, source = load()
    require(type(si) is int and 0 <= si < 5, "book diagnosis seed outside full grid")
    seed_id = source["seeds"][si]["seed_id"]
    target = OUTPUT / f"seed_{si}"
    require(not target.exists(), "preserve completed or incomplete book diagnosis")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"pending_seed_{si}_", dir=OUTPUT))
    counts, summaries = Counter(), []
    tick = source["model_design"]["venue"]["tick_minor"]
    with (stage / "positions.jsonl.gz").open("xb") as raw, gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as out:
        for ci, cell in enumerate(CELLS):
            folder = SOURCE / f"seed_{si}/cell_{ci}"
            checkpoint(folder, [seed_id, cell["name"]])
            saved = read(folder / "condition.json")
            panel = {(r["stock_code"], r["trade_date"]): r for r in saved["variant"]["daily_asset_rows"]}
            groups = {key: scope_group() for key in ("full", "source_known", "initial_internal_window", "later_internal_window")}
            cc = Counter()
            with gzip.open(folder / "ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
                for bi, path in enumerate(saved["paths"]):
                    require(path["basket_index"] == bi and path["seed_id"] == seed_id and path["variant"] == cell["name"],
                            "archived path identity differs")
                    specs = path["participant_specs"]
                    for session, date in enumerate(source["dates"]):
                        line = next(stream, None)
                        require(line is not None, "archived raw ledger ended early")
                        day = json.loads(line)
                        require((day["seed_id"], day["variant"], day["basket_index"], day["trade_date"])
                                == (seed_id, cell["name"], bi, date), "raw ledger identity differs")
                        calls = day["portfolio_auction"]["asset_calls"]
                        require(sorted(calls) == source["baskets"][bi], "raw basket coverage differs")
                        for stock, call in calls.items():
                            args = (call, tick, specs, day["decisions"], day["issuer_information_receipts"], stock)
                            d, independent = diagnose_book(*args), independent_book(*args)
                            require(d == independent, "event sweep and independent eligibility enumeration differ")
                            prices = alternative_prices(independent, tick)
                            for rule, price in prices.items():
                                rebuilt = interval_price(call, source["model_design"]["venue"], rule)
                                if d["maximum_volume"]:
                                    interval = next(r for r in d["optimal_intervals"] if r["lower_minor"] <= price <= r["upper_minor"])
                                    imbalance = interval["demand"] - interval["supply"]
                                else:
                                    imbalance = d["prior_demand"] - d["prior_supply"]
                                require(rebuilt == (price, d["maximum_volume"], imbalance), "independent alternative selection differs")
                                cc["same_book_rule_checks"] += 1
                            if bi == 0 and session in cfg["dense_sessions"]:
                                lower, upper = call["price_bounds_minor"]
                                points = []
                                for p in range(lower, upper + tick, tick):
                                    b = sum(o["accepted_quantity"] for o in call["orders"] if o["side"] == "buy" and o["limit_price_minor"] >= p)
                                    s = sum(o["accepted_quantity"] for o in call["orders"] if o["side"] == "sell" and o["limit_price_minor"] <= p)
                                    points.append((min(b, s), abs(b - s), p))
                                score = max((v, -imb) for v, imb, _ in points)
                                optimal_ticks = {p for v, imb, p in points if (v, -imb) == score}
                                interval_ticks = {p for r in d["optimal_intervals"] for p in range(r["lower_minor"], r["upper_minor"] + tick, tick)}
                                require(optimal_ticks == interval_ticks, "dense full-band optimal tick set differs")
                                cc["dense_all_tick_books"] += 1
                            reference = panel[stock, date]
                            require(reference["price_before_minor"] == d["prior_minor"]
                                    and reference["price_after_minor"] == d["clearing_minor"]
                                    and reference["matched_volume"] == d["maximum_volume"], "archived scalar/ledger bridge differs")
                            known = reference["observed_return"] is not None
                            phase = "initial_internal_window" if session < 21 else "later_internal_window"
                            for group in (groups["full"], groups[phase]) + ((groups["source_known"],) if known else ()):
                                add_group(group, d, prices, tick)
                            compact = {k: v for k, v in d.items() if k != "orders"}
                            out.write(encoded({"seed_id": seed_id, "variant": cell["name"], "basket_index": bi,
                                "session": session, "stock_code": stock, "trade_date": date,
                                "source_target_known": known, "observed_return": reference["observed_return"],
                                "diagnostic": compact, "same_accepted_book_prices_minor": prices}))
                            cc["books"] += 1
                            cc["independent_book_checks"] += 1
                            cc["unknown_source_targets_preserved"] += int(not known)
                            cc["orders"] += len(d["orders"])
                        cc["ledger_records"] += 1
                require(next(stream, None) is None, "archived raw ledger contains extra records")
            require(cc["books"] == 7134 and cc["unknown_source_targets_preserved"] == 15
                    and groups["source_known"]["books"] == 7119, "whole book/target universe differs")
            scope = {k: finish_group(v) for k, v in groups.items()}
            flat = saved["variant"]["comparison"]["synthetic"]["zero_return_fraction"]
            require(scope["source_known"]["fractions"]["zero_return"] == flat, "source-known zero denominator differs")
            summaries.append({"seed_id": seed_id, **cell, "groups": scope, "checks": dict(cc)})
            counts.update(cc)
            print(f"Book diagnosis seed {si + 1}/5 condition {ci + 1}/16 independently checked.", flush=True)
    require(counts["books"] == cfg["per_seed_books"] and len(summaries) == 16, "full diagnostic seed scope differs")
    (stage / "summary.json").write_bytes(encoded({"status": "COMPLETE_FULL_BOOK_DIAGNOSIS_SEED",
        "protocol_sha256": file_sha256(CONFIG), "seed_id": seed_id, "checks": dict(counts), "conditions": summaries}))
    artifacts = {p.name: file_sha256(p) for p in stage.iterdir()}
    (stage / "checkpoint.json").write_bytes(encoded({"protocol_sha256": file_sha256(CONFIG), "artifacts": artifacts}))
    stage.replace(target)


def finish():
    cfg, source = load()
    target = OUTPUT / "results.json"
    require(not target.exists(), "preserve completed full book diagnosis")
    conditions, counts, artifacts = [], Counter(), {}
    for si, source_seed in enumerate(source["seeds"]):
        folder = OUTPUT / f"seed_{si}"
        seal = read(folder / "checkpoint.json")
        require(seal["protocol_sha256"] == file_sha256(CONFIG), "diagnosis checkpoint protocol differs")
        for name, digest in seal["artifacts"].items():
            require(file_sha256(folder / name) == digest, "diagnosis checkpoint changed")
        r = read(folder / "summary.json")
        require(r["status"] == "COMPLETE_FULL_BOOK_DIAGNOSIS_SEED" and r["seed_id"] == source_seed["seed_id"], "seed not complete")
        conditions.extend(r["conditions"])
        counts.update(r["checks"])
        artifacts[str((folder / "checkpoint.json").relative_to(ROOT)).replace("\\", "/")] = file_sha256(folder / "checkpoint.json")
    require(len(conditions) == 80 and len({(c["seed_id"], c["name"]) for c in conditions}) == 80
            and counts["books"] == 570720, "all eighty conditions required")
    distributions = {}
    for field in ("zero_return", "no_trade", "traded_flat", "positive_volume_among_zero", "multiple_optima_among_traded_flat"):
        vals = [c["groups"]["source_known"]["fractions"][field] for c in conditions]
        distributions[field] = {"minimum": min(vals), "median": statistics.median(vals), "maximum": max(vals)}
    for rule in RULES:
        for field in ("flat_fraction", "traded_flat_fraction_moved"):
            vals = [c["groups"]["source_known"]["same_book_rules"][rule][field] for c in conditions]
            distributions[rule + "_same_book_" + field] = {"minimum": min(vals), "median": statistics.median(vals), "maximum": max(vals)}
    target.write_bytes(encoded({"status": "COMPLETE_FULL_TEMPORAL_RAW_BOOK_DIAGNOSIS_2021_V1",
        "protocol_sha256": file_sha256(CONFIG), "checks": dict(counts), "conditions": conditions,
        "distributions_across_all_eighty_conditions": distributions, "artifacts": artifacts,
        "is_full_path_counterfactual": False, "new_default_selected": False, "goal_complete": False,
        "interpretation": cfg["interpretation"]}))
    print(json.dumps({"checks": dict(counts), "distributions": distributions}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=int)
    p.add_argument("--finish", action="store_true")
    args = p.parse_args()
    if args.finish:
        require(args.seed is None, "finish cannot specify a seed")
        finish()
    else:
        seed(args.seed)
