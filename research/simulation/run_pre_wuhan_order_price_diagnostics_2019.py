"""Full archived factorial book reader; never changes a simulation trace."""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import tempfile
from collections import Counter
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .order_price_diagnostics import VERSION, diagnose_book, summarize

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_order_price_diagnostics_2019.json"
SOURCE = ROOT / "research_outputs/pre_wuhan_price_feedback_channels_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_order_price_diagnostics_2019_v1"
PRIOR = ROOT / "research_outputs/pre_wuhan_price_feedback_channels_final_verification_20261002.json"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def verify_bindings(bindings):
    for name, expected in bindings.items():
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        if file_sha256(path) != expected:
            raise ValueError("order/price diagnostic binding changed: " + name)


def load_source():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (cfg["version"] != VERSION or cfg["agent_signal_enabled"] is not False
            or cfg["dense_check_sessions"] != [0, 10] or cfg["price_tie_break"] != "nearest_prior"
            or cfg["session_phases"] != {"initial": [0, 0], "warmup_1_9": [1, 9], "later_10_42": [10, 42]}):
        raise ValueError("frozen read-only order/price diagnostic protocol differs")
    verify_bindings(cfg["inputs"])
    prior = json.loads(PRIOR.read_text(encoding="utf-8"))
    if prior["status"] != "PASS_CURRENT_BINDINGS_REBUILDS_AND_FULL_CHANNEL_AUDIT":
        raise ValueError("source channel audit is not complete")
    verify_bindings(prior["inputs"])
    bindings = dict(prior["inputs"])
    for name, expected in cfg["inputs"].items():
        name = str((ROOT / name).resolve()) if not Path(name).is_absolute() else name
        if name in bindings and bindings[name] != expected:
            raise ValueError("diagnostic protocol shadows a prior frozen binding")
        bindings[name] = expected
    bindings[str(CONFIG)] = file_sha256(CONFIG)
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    sample = result["sample"]
    expected = {"conditions": len(result["variants"]), "paths": len(result["path_summaries"]),
                "basket_days": len(result["path_summaries"]) * sample["sessions"],
                "asset_books": len(result["variants"]) * sample["company_days_per_variant"]}
    if expected != cfg["expected_coverage"] or (sample["companies"], sample["sessions"], sample["baskets"]) != (123, 43, 41):
        raise ValueError("source factorial dimensions differ")
    base = json.loads((ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json").read_text(encoding="utf-8"))
    return cfg, bindings, result, base


def source_books(result):
    paths = {(p["seed_id"], p["variant"], p["basket_index"]): p for p in result["path_summaries"]}
    if len(paths) != len(result["path_summaries"]):
        raise ValueError("duplicated source path identities")
    seen = Counter()
    calendar = {}
    codes = result["sample"]["selected_stock_codes"]
    with gzip.open(SOURCE / "ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            day = json.loads(line)
            key = day["seed_id"], day["variant"], day["basket_index"]
            session = day["portfolio_auction"]["session"]
            if key not in paths or type(session) is not int or session != seen[key] or session >= 43:
                raise ValueError("source path identity/session differs")
            seen[key] += 1
            dates = day["trade_date"], day["signal_cutoff_date"], day["execution_reference_date"]
            if not dates[1] < dates[2] < dates[0] or calendar.setdefault(session, dates) != dates:
                raise ValueError("source diagnostic calendar differs")
            assets = codes[3 * key[2]:3 * key[2] + 3]
            calls = day["portfolio_auction"]["asset_calls"]
            if set(assets) != set(calls):
                raise ValueError("source diagnostic basket assets differ")
            for stock in sorted(calls):
                yield day, stock, calls[stock], paths[key]["participant_specs"]
    if set(seen) != set(paths) or any(n != 43 for n in seen.values()) or len(calendar) != 43:
        raise ValueError("source diagnostic path coverage is incomplete")


def diagnostic_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def condition_measures(g):
    n, down = g["books"], g["directions"].get("down", 0)
    totals = {stage + "_" + side: sum(r.get(stage + "_" + side, 0) for r in g["role_orders"].values())
              for stage in ("requested", "accepted") for side in ("buy", "sell")}
    return {"mean_return": g["mean_return"], "down_fraction": down / n,
            "up_fraction": g["directions"].get("up", 0) / n,
            "flat_fraction": g["directions"].get("flat", 0) / n,
            "volume_down_fraction": g["criteria"].get("volume_requires_move|down", 0) / n,
            "imbalance_down_fraction": g["criteria"].get("imbalance_requires_move|down", 0) / n,
            "no_match_fraction": g["criteria"].get("no_match|flat", 0) / n,
            "halted_fraction": g["criteria"].get("halted|flat", 0) / n,
            "trading_flat_fraction": g["criteria"].get("prior_optimal|flat", 0) / n,
            "down_with_requested_buy_excess_fraction": g["net_requested_by_direction"].get("down|buy_excess", 0) / down if down else None,
            "down_with_accepted_buy_excess_fraction": g["net_accepted_by_direction"].get("down|buy_excess", 0) / down if down else None,
            "buy_acceptance_fraction": totals["accepted_buy"] / totals["requested_buy"] if totals["requested_buy"] else None,
            "sell_acceptance_fraction": totals["accepted_sell"] / totals["requested_sell"] if totals["requested_sell"] else None}


def run(output=OUTPUT, audit_existing=False):
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or output.exists() and not audit_existing:
        raise ValueError("diagnostics require a fresh direct research_outputs directory")
    cfg, bindings, result, base = load_source()
    with tempfile.TemporaryDirectory(prefix="marketmirror-book-diagnostic-") as temporary:
        target = Path(temporary)
        count, paths, orders = 0, set(), 0
        with (target / "books.jsonl.gz").open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
                for day, stock, call, specs in source_books(result):
                    record = {k: day[k] for k in ("seed_id", "variant", "basket_index", "trade_date")}
                    record.update(stock_code=stock, session=day["portfolio_auction"]["session"],
                        diagnostic=diagnose_book(call, base["venue"]["tick_minor"], specs,
                                                 day["decisions"], day["issuer_information_receipts"], stock))
                    stream.write(encoded(record))
                    count += 1
                    orders += len(call["orders"])
                    paths.add((day["seed_id"], day["variant"], day["basket_index"]))
                    if count % 15000 == 0:
                        print(f"diagnosed {count} accepted asset books", flush=True)
        groups = summarize(diagnostic_rows(target / "books.jsonl.gz"))
        coverage = {"conditions": len(groups), "paths": len(paths), "basket_days": count // 3, "asset_books": count}
        if coverage != cfg["expected_coverage"]:
            raise ValueError("full accepted-book diagnostic coverage differs")
        primary = {v["seed_id"] + "|" + v["name"]: v for v in result["variants"]}
        differences = []
        for key, group in groups.items():
            expected = primary[key]
            delta = abs(group["mean_return"] - expected["comparison"]["synthetic"]["mean"])
            if delta > 1e-12 or group["matched_volume"] != expected["total_matched_volume"]:
                raise ValueError("diagnostic returns/volume differ from the source factorial")
            if abs(group["directions"].get("flat", 0) / group["books"]
                   - expected["comparison"]["synthetic"]["zero_return_fraction"]) > 1e-12:
                raise ValueError("diagnostic flat fraction differs from the source factorial")
            differences.append(delta)
        measures = {key: condition_measures(g) for key, g in groups.items()}
        seeds = [row["seed_id"] for row in result["seeds"]]
        variants = list(dict.fromkeys(row["name"] for row in result["variants"]))
        medians = {variant: {field: statistics.median(values) if values else None
            for field in next(iter(measures.values()))
            for values in [[measures[seed + "|" + variant][field] for seed in seeds
                            if measures[seed + "|" + variant][field] is not None]]} for variant in variants}
        summary = {"version": VERSION, "source_config_sha256": file_sha256(CONFIG),
            "coverage": coverage, "orders": orders, "source_mean_max_absolute_difference": max(differences),
            "condition_measures": measures, "seed_column_medians": medians, "conditions": groups,
            "interpretation": cfg["interpretation"]}
        (target / "summary.json").write_bytes(encoded(summary))
        manifest = {"version": VERSION, "inputs": bindings, "artifacts": {
            name: {"sha256": file_sha256(target / name), "bytes": (target / name).stat().st_size}
            for name in ("books.jsonl.gz", "summary.json")}}
        (target / "manifest.json").write_bytes(encoded(manifest))
        if audit_existing:
            for name in ("books.jsonl.gz", "summary.json", "manifest.json"):
                if file_sha256(output / name) != file_sha256(target / name):
                    raise ValueError("diagnostic artifact did not rebuild byte-identically: " + name)
            print("All three accepted-book diagnostic artifacts rebuilt byte-identically.", flush=True)
        else:
            output.mkdir()
            for name in ("books.jsonl.gz", "summary.json", "manifest.json"):
                (output / name).write_bytes((target / name).read_bytes())
            print(json.dumps(coverage, sort_keys=True), flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    run(args.output, args.audit_existing)
