"""Reclear frozen books to isolate uninformed buy and sell quote channels."""

from __future__ import annotations

import argparse
import gzip
import json
import tempfile
from collections import Counter
from fractions import Fraction
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .agents import AgentParameters
from .issuer_quote_sides import CASES, reprice_orders, verify_repriced_orders, reclear_from_state
from .portfolio_audit import audit_portfolio_day
from .portfolio_market import initial_portfolio
from .run_pre_wuhan_background_response_2019 import BASE, REPLAY, initial_state, verify_hashes

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_issuer_quote_sides_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_issuer_quote_sides_2019_v1"
VERSION = "pre-wuhan-issuer-quote-side-state-reset-v1"
SOURCES = ["issuer_quarter_sigma_half", "issuer_one_sigma_half"]


def load_source() -> tuple:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (config["version"] != VERSION or config["source_variants"] != SOURCES or config["cases"] != list(CASES)
            or config["dense_audit_sessions"] != [0, 10]
            or config["state_policy"] != "reset_every_session_to_original_source_state; never_feed_counterfactual_state_into_later_sessions"
            or config["quantity_policy"] != "freeze_submitted_side_quantity_identity_and_priority; reapply_wallet_and_inventory_acceptance"
            or config["price_policy"] != "deliver_only_current_company_message_to_selected_originally_unreceived_submitted_orders"):
        raise ValueError("quote-side intervention design differs")
    source_dir = (CONFIG.parent / config["source_directory"]).resolve()
    inputs = {str(source_dir / "results.json"): config["source_results_sha256"],
              str(source_dir / "manifest.json"): config["source_manifest_sha256"], str(CONFIG): file_sha256(CONFIG)}
    verify_hashes(inputs)
    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    for field in ("inputs", "code_sha256"):
        verify_hashes(manifest[field])
        inputs.update(manifest[field])
    for name, info in manifest["artifacts"].items():
        path = source_dir / name
        if path.parent != source_dir or file_sha256(path) != info["sha256"]:
            raise ValueError("quote-side source artifact hash differs")
        inputs[str(path)] = info["sha256"]
    source = json.loads((source_dir / "results.json").read_text(encoding="utf-8"))["result"]
    if source["pipeline_version"] != "pre-wuhan-lagged-risk-private-issuer-valuation-v1":
        raise ValueError("quote-side reference pipeline differs")
    base = json.loads(BASE.read_text(encoding="utf-8"))
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    if str(BASE) not in inputs or str(REPLAY) not in inputs:
        raise ValueError("source does not bind initialization parameters")
    code_hashes = {str(path.resolve()): file_sha256(path) for path in (
        Path(__file__), ROOT / "research/simulation/issuer_quote_sides.py", ROOT / "research/simulation/portfolio_audit.py",
        ROOT / "research/simulation/portfolio_auction.py", ROOT / "research/simulation/call_auction.py")}
    return config, source_dir, source, base, replay, inputs, code_hashes


def information_flow(call: dict, specs: dict, original_receipts: dict) -> dict:
    result = Counter()
    for order in call["orders"]:
        name = order["owner"]
        category = "informed" if original_receipts[name]["received"] else "uninformed"
        side, kind = order["side"], specs[name]["kind"]
        for field, label in (("quantity", "requested"), ("accepted_quantity", "accepted"), ("filled_quantity", "filled")):
            result[f"{kind}_{category}_{side}_{label}"] += order[field]
        result["cash_clipped_buy_orders"] += int("cash_and_fee_reservation" in order["reasons"])
        result["inventory_clipped_sell_orders"] += int("sellable_inventory" in order["reasons"])
    for trade in call["trades"]:
        buyer = "informed" if original_receipts[trade["buyer"]]["received"] else "uninformed"
        seller = "informed" if original_receipts[trade["seller"]]["received"] else "uninformed"
        result[f"trade_original_{buyer}_buyer_{seller}_seller"] += trade["quantity"]
    if sum(value for key, value in result.items() if key.startswith("trade_original_")) != call["matched_volume"]:
        raise ValueError("source information trade categories do not conserve matched volume")
    return dict(result)


def summary(rows: list[dict]) -> dict:
    size = len(rows)
    returns = [Fraction(row["price_after_minor"] - row["price_before_minor"], row["price_before_minor"]) for row in rows]
    effects = [Fraction(row["price_after_minor"] - row["source_price_after_minor"], row["price_before_minor"]) for row in rows]
    flow = Counter()
    for row in rows:
        flow.update(row["information_flow"])
    return {"company_days": size,
            "mean_conditional_return_bps": float(sum(returns) * 10000 / size) if size else None,
            "mean_paired_return_change_bps": float(sum(effects) * 10000 / size) if size else None,
            "positive_return_fraction": sum(value > 0 for value in returns) / size if size else None,
            "negative_return_fraction": sum(value < 0 for value in returns) / size if size else None,
            "zero_return_fraction": sum(value == 0 for value in returns) / size if size else None,
            "price_changed_company_days": sum(row["price_after_minor"] != row["source_price_after_minor"] for row in rows),
            "accepted_quantity_changed_company_days": sum(row["accepted_buy"] != row["source_accepted_buy"] or row["accepted_sell"] != row["source_accepted_sell"] for row in rows),
            "total_matched_volume": sum(row["matched_volume"] for row in rows),
            "source_total_matched_volume": sum(row["source_matched_volume"] for row in rows),
            "selected_orders": sum(row["selected_orders"] for row in rows), "changed_quotes": sum(row["changed_quotes"] for row in rows),
            "information_flow": dict(flow)}


def run(output: Path = OUTPUT, audit_existing: bool = False) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or (not audit_existing and output.exists()):
        raise ValueError("quote-side output requires a fresh direct research_outputs directory")
    config, source_dir, source, base, replay, inputs, code_hashes = load_source()
    codes = source["sample"]["selected_stock_codes"]
    if len(codes) != 123 or codes != sorted(set(codes)):
        raise ValueError("quote-side cohort differs")
    baskets = [codes[index:index + 3] for index in range(0, len(codes), 3)]
    agents = [AgentParameters(**row) for row in replay["agents"]]
    case = next(row for row in base["cases"] if row["case_id"] == "separated_institution35")
    panels = {variant["name"]: {(row["stock_code"], row["trade_date"]): row for row in variant["daily_asset_rows"]}
              for variant in source["variants"] if variant["name"] in SOURCES}
    specs_by_path = {(row["variant"], row["basket_index"]): row["participant_specs"] for row in source["path_summaries"] if row["variant"] in SOURCES}
    dates = sorted({day for _, day in panels[SOURCES[0]]})
    if len(dates) != 43 or any(len(panel) != 5289 or set(panel) != {(stock, day) for stock in codes for day in dates} for panel in panels.values()):
        raise ValueError("quote-side source panel is incomplete")
    all_rows = {(variant, treatment): [] for variant in SOURCES for treatment in CASES}
    states, sessions, seen = {}, {}, set()
    checks = Counter()
    with tempfile.TemporaryDirectory(prefix="quote-side-stage-", dir=output.parent) as temporary:
        stage = Path(temporary)
        with (stage / "ledger.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as ledger:
            with gzip.open(source_dir / "ledger.jsonl.gz", "rt", encoding="utf-8") as source_ledger:
                for line in source_ledger:
                    day = json.loads(line)
                    variant = day["variant"]
                    if variant not in SOURCES:
                        continue
                    basket_index = day["basket_index"]
                    if type(basket_index) is not int or not 0 <= basket_index < len(baskets):
                        raise ValueError("quote-side source basket index invalid")
                    basket, key = baskets[basket_index], (variant, basket_index)
                    session = sessions.get(key, 0)
                    if session >= len(dates) or day["trade_date"] != dates[session]:
                        raise ValueError("quote-side source dates duplicate or reorder")
                    if key not in states:
                        states[key] = initial_state(agents, base, basket, case)
                        _, _, rebuilt_specs, _ = initial_portfolio(agents, base["core"], base["background"], basket, case, base["venue"])
                        if specs_by_path[key] != rebuilt_specs:
                            raise ValueError("source participant initialization differs")
                    previous, specs = states[key], specs_by_path[key]
                    if set(day["portfolio_auction"]["asset_calls"]) != set(basket):
                        raise ValueError("quote-side source asset coverage differs")
                    source_rows = {stock: panels[variant][stock, day["trade_date"]] for stock in basket}
                    messages = {stock: source_rows[stock]["hypothetical_issuer_valuation_bps"] for stock in basket}
                    receipts = {stock: {name: stocks[stock] for name, stocks in day["issuer_information_receipts"].items() if stock in stocks} for stock in basket}
                    dense = session in config["dense_audit_sessions"]
                    next_original = None
                    for treatment in CASES:
                        books, changes = reprice_orders(day, specs, messages, base["venue"], base["background"], source["background_response"], treatment)
                        verify_repriced_orders(day, specs, messages, base["venue"], base["background"], source["background_response"], treatment, books, changes)
                        cleared = reclear_from_state(previous, base["venue"], day, books, session)
                        if treatment == "frozen_half_book" and cleared != day["portfolio_auction"]:
                            raise ValueError("unchanged source book does not reproduce the entire original auction")
                        record = {"source_variant": variant, "basket_index": basket_index, "case": treatment,
                                  **{field: day[field] for field in ("trade_date", "signal_cutoff_date", "execution_reference_date")},
                                  "quote_treatments": changes,
                                  "original_information_by_stock": {stock: {name: receipt["received"] for name, receipt in values.items()} for stock, values in receipts.items()},
                                  "portfolio_auction": cleared}
                        audited = audit_portfolio_day(record, previous, base["venue"], session, dense=dense)
                        if treatment == "frozen_half_book":
                            next_original = audited
                            checks["whole_original_auction_objects_reproduced"] += 1
                        checks["portfolio_ledger_records"] += 1
                        checks["asset_ledger_checks"] += len(basket)
                        checks["dense_asset_price_checks"] += len(basket) if dense else 0
                        ledger.write((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                        for stock in basket:
                            call = cleared["asset_calls"][stock]
                            saved = day["portfolio_auction"]["asset_calls"][stock]
                            identity = (variant, treatment, stock, day["trade_date"])
                            if identity in seen or call["price_before_minor"] != saved["price_before_minor"]:
                                raise ValueError("counterfactual state was carried forward or company-day duplicated")
                            seen.add(identity)
                            selected = [change for change in changes if change["stock_code"] == stock]
                            if any(source_rows[stock][field] != saved[field] for field in ("price_before_minor", "price_after_minor", "matched_volume")):
                                raise ValueError("source ledger prices or volume differ from source metric panel")
                            row = {"stock_code": stock, "trade_date": day["trade_date"], "hypothetical_message_bps": messages[stock],
                                   "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"],
                                   "source_price_after_minor": saved["price_after_minor"], "matched_volume": call["matched_volume"],
                                   "source_matched_volume": saved["matched_volume"], "selected_orders": len(selected),
                                   "changed_quotes": sum(change["quote_changed"] for change in selected),
                                   "accepted_buy": sum(order["accepted_quantity"] for order in call["orders"] if order["side"] == "buy"),
                                   "accepted_sell": sum(order["accepted_quantity"] for order in call["orders"] if order["side"] == "sell"),
                                   "source_accepted_buy": sum(order["accepted_quantity"] for order in saved["orders"] if order["side"] == "buy"),
                                   "source_accepted_sell": sum(order["accepted_quantity"] for order in saved["orders"] if order["side"] == "sell"),
                                   "information_flow": information_flow(call, specs, receipts[stock])}
                            all_rows[variant, treatment].append(row)
                    # Discard every counterfactual next state. Only the exactly
                    # reproduced original auction advances the observed history.
                    states[key] = next_original
                    sessions[key] = session + 1
                    if session == len(dates) - 1 and ((basket_index + 1) % 10 == 0 or basket_index + 1 == len(baskets)):
                        print(f"{variant}: audited {basket_index + 1}/{len(baskets)} source baskets with 4 quote treatments", flush=True)
        if len(states) != 82 or any(value != 43 for value in sessions.values()) or len(seen) != 42312:
            raise ValueError("quote-side full coverage differs")
        variants = []
        for variant in SOURCES:
            cases = []
            for treatment in CASES:
                rows = all_rows[variant, treatment]
                groups = {"positive": summary([row for row in rows if row["hypothetical_message_bps"] > 0]),
                          "negative": summary([row for row in rows if row["hypothetical_message_bps"] < 0]),
                          "zero": summary([row for row in rows if row["hypothetical_message_bps"] == 0])}
                cases.append({"name": treatment, "all": summary(rows), "by_message_sign": groups, "daily_asset_rows": rows})
            variants.append({"source_variant": variant, "cases": cases})
        result = {"pipeline_version": VERSION, "source_sample": source["sample"], "design": {key: config[key] for key in ("source_variants", "cases", "quantity_policy", "state_policy", "price_policy")},
                  "variants": variants, "checks": dict(checks), "interpretation": config["interpretation"]}
        (stage / "results.json").write_text(json.dumps({"result": result}, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        verify_hashes(inputs)
        verify_hashes(code_hashes)
        manifest = {"pipeline_version": VERSION, "inputs": inputs, "code_sha256": code_hashes,
                    "artifacts": {name: {"sha256": file_sha256(stage / name)} for name in ("results.json", "ledger.jsonl.gz")}}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if audit_existing:
            for name in ("results.json", "ledger.jsonl.gz", "manifest.json"):
                if file_sha256(stage / name) != file_sha256(output / name):
                    raise ValueError(f"quote-side artifact differs byte-for-byte: {name}")
        else:
            stage.replace(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = run(args.output_dir, args.audit_existing)
    print(json.dumps({"checks": result["checks"], "variants": [{"source": row["source_variant"],
                       "cases": [{"name": cell["name"], "all_mean_return_bps": cell["all"]["mean_conditional_return_bps"],
                                  "positive_message_mean_return_bps": cell["by_message_sign"]["positive"]["mean_conditional_return_bps"],
                                  "negative_message_mean_return_bps": cell["by_message_sign"]["negative"]["mean_conditional_return_bps"]} for cell in row["cases"]]} for row in result["variants"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
