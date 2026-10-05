"""Independently check frozen submissions, reset clocks and conditional statistics."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_issuer_quote_sides_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_issuer_quote_side_statistics_2019_v1.json"
CASES = ["frozen_half_book", "inform_unreceived_buyers", "inform_unreceived_sellers", "inform_unreceived_both"]


def compute() -> dict:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    inputs = {str(SOURCE / "manifest.json"): file_sha256(SOURCE / "manifest.json")}
    for field in ("inputs", "code_sha256"):
        inputs.update(manifest[field])
    for name, info in manifest["artifacts"].items():
        path = SOURCE / name
        if path.parent != SOURCE:
            raise ValueError("quote-side statistics artifact escaped source directory")
        inputs[str(path)] = info["sha256"]
    for name, digest in inputs.items():
        if file_sha256(Path(name)) != digest:
            raise ValueError("quote-side statistics source or code hash differs")
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    panels = {(variant["source_variant"], cell["name"]): {(row["stock_code"], row["trade_date"]): row for row in cell["daily_asset_rows"]}
              for variant in result["variants"] for cell in variant["cases"]}
    originals = {(variant["source_variant"], row["stock_code"], row["trade_date"]): row
                 for variant in result["variants"] for row in variant["cases"][0]["daily_asset_rows"]}
    observed = Counter()
    row_checks, order_checks, aggregate_checks, maximum = 0, 0, 0, 0.0

    def close(actual, expected):
        nonlocal aggregate_checks, maximum
        aggregate_checks += 1
        if expected is None:
            if actual is not None:
                raise ValueError("undefined quote-side statistic was filled")
            return
        if not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isfinite(actual):
            raise ValueError("quote-side statistic is nonfinite or not numeric")
        difference = abs(actual - expected)
        maximum = max(maximum, difference)
        if difference > 1e-12:
            raise ValueError("independent quote-side statistic differs")

    def check_summary(saved, rows):
        returns = [row["price_after_minor"] / row["price_before_minor"] - 1 for row in rows]
        effects = [(row["price_after_minor"] - row["source_price_after_minor"]) / row["price_before_minor"] for row in rows]
        close(saved["company_days"], len(rows))
        close(saved["mean_conditional_return_bps"], statistics.mean(returns) * 10000 if rows else None)
        close(saved["mean_paired_return_change_bps"], statistics.mean(effects) * 10000 if rows else None)
        for name, predicate in (("positive", lambda value: value > 0), ("negative", lambda value: value < 0), ("zero", lambda value: value == 0)):
            close(saved[name + "_return_fraction"], sum(predicate(value) for value in returns) / len(rows) if rows else None)
        close(saved["price_changed_company_days"], sum(row["price_after_minor"] != row["source_price_after_minor"] for row in rows))
        close(saved["accepted_quantity_changed_company_days"], sum(row["accepted_buy"] != row["source_accepted_buy"] or row["accepted_sell"] != row["source_accepted_sell"] for row in rows))
        for field in ("total_matched_volume", "source_total_matched_volume", "selected_orders", "changed_quotes"):
            row_field = {"total_matched_volume": "matched_volume", "source_total_matched_volume": "source_matched_volume"}.get(field, field)
            close(saved[field], sum(row[row_field] for row in rows))
        flows = Counter()
        for row in rows:
            flows.update(row["information_flow"])
        if saved["information_flow"] != dict(flows):
            raise ValueError("quote-side flow aggregates differ")

    for variant in result["variants"]:
        for cell in variant["cases"]:
            panel = panels[variant["source_variant"], cell["name"]]
            if len(panel) != 5289 or len(cell["daily_asset_rows"]) != len(panel):
                raise ValueError("quote-side statistics panel coverage differs")
            rows = list(panel.values())
            check_summary(cell["all"], rows)
            for label in ("positive", "negative", "zero"):
                subset = [row for row in rows if ((row["hypothetical_message_bps"] > 0) - (row["hypothetical_message_bps"] < 0)) == {"positive": 1, "negative": -1, "zero": 0}[label]]
                check_summary(cell["by_message_sign"][label], subset)
    baseline = None
    with gzip.open(SOURCE / "ledger.jsonl.gz", "rt", encoding="utf-8") as ledger:
        for line in ledger:
            record = json.loads(line)
            key = (record["source_variant"], record["basket_index"], record["trade_date"])
            case = record["case"]
            ordinal = observed[key]
            if ordinal >= len(CASES) or case != CASES[ordinal]:
                raise ValueError("quote-side ledger case order or coverage differs")
            observed[key] += 1
            if ordinal == 0:
                baseline = record
            elif (any(record[field] != baseline[field] for field in ("signal_cutoff_date", "execution_reference_date", "original_information_by_stock"))
                  or record["portfolio_auction"]["prices_before_minor"] != baseline["portfolio_auction"]["prices_before_minor"]):
                raise ValueError("quote-side source information or prior prices were carried from a counterfactual")
            treatment_log = {(item["stock_code"], item["order_id"]): item for item in record["quote_treatments"]}
            if len(treatment_log) != len(record["quote_treatments"]):
                raise ValueError("duplicate quote treatment")
            selected_count = 0
            for stock, call in record["portfolio_auction"]["asset_calls"].items():
                row = panels[record["source_variant"], case][stock, record["trade_date"]]
                source_row = originals[record["source_variant"], stock, record["trade_date"]]
                if (row["hypothetical_message_bps"] != source_row["hypothetical_message_bps"]
                        or row["source_price_after_minor"] != source_row["price_after_minor"]
                        or any(row[field] != call[field] for field in ("price_before_minor", "price_after_minor", "matched_volume"))):
                    raise ValueError("quote-side metric price, message or volume differs from ledger")
                row_checks += 1
                base_orders = {order["order_id"]: order for order in baseline["portfolio_auction"]["asset_calls"][stock]["orders"]}
                if set(base_orders) != {order["order_id"] for order in call["orders"]}:
                    raise ValueError("quote intervention inserted or deleted submitted orders")
                accepted = Counter()
                for order in call["orders"]:
                    old = base_orders[order["order_id"]]
                    if any(order[field] != old[field] for field in ("owner", "side", "quantity", "sequence")):
                        raise ValueError("quote intervention changed a frozen submission")
                    accepted[order["side"]] += order["accepted_quantity"]
                    receipt = record["original_information_by_stock"][stock][order["owner"]]
                    if type(receipt) is not bool:
                        raise ValueError("original receipt flag is not boolean")
                    selected = not receipt and (case == CASES[3] or case == CASES[1] and order["side"] == "buy" or case == CASES[2] and order["side"] == "sell")
                    item = treatment_log.get((stock, order["order_id"]))
                    if selected:
                        selected_count += 1
                        if (item is None or item["source_limit_price_minor"] != old["limit_price_minor"]
                                or item["counterfactual_limit_price_minor"] != order["limit_price_minor"]
                                or item["delivered_valuation_shift_bps"] != row["hypothetical_message_bps"]
                                or item["quote_changed"] != (old["limit_price_minor"] != order["limit_price_minor"])):
                            raise ValueError("selected quote treatment differs from archived order")
                    elif item is not None or order["limit_price_minor"] != old["limit_price_minor"]:
                        raise ValueError("quote treatment changed an informed or unselected order")
                    order_checks += 1
                if (row["accepted_buy"] != accepted["buy"] or row["accepted_sell"] != accepted["sell"]
                        or sum(trade["quantity"] for trade in call["trades"]) != row["matched_volume"]):
                    raise ValueError("quote-side accepted quantities or trades differ")
            if selected_count != len(treatment_log):
                raise ValueError("quote treatment log contains an extra order")
    if len(observed) != 3526 or any(value != 4 for value in observed.values()) or row_checks != 42312:
        raise ValueError("independent quote-side ledger coverage differs")
    return {"pipeline_version": "pre-wuhan-issuer-quote-side-independent-statistics-v1", "asset_ledger_rows_checked": row_checks,
            "frozen_submitted_orders_checked": order_checks, "aggregate_scalar_checks": aggregate_checks,
            "maximum_absolute_difference": maximum, "tolerance": 1e-12,
            "method": "Recompute conditional summaries from archived prices; verify every archived intervention against its baseline submission, current message and original receipt flags. No production quote transformation or summary function is called. Settlement and rational quote audits are separate in the main runner.",
            "inputs": dict(sorted(inputs.items())), "code_sha256": file_sha256(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("quote-side statistics must be under research_outputs")
    result = compute()
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if output.read_bytes() != payload:
            raise ValueError("quote-side statistics archive differs byte-for-byte")
    else:
        with output.open("xb") as handle:
            handle.write(payload)
    print(json.dumps({key: value for key, value in result.items() if key not in {"inputs", "code_sha256"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
