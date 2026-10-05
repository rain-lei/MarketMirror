"""Reconstruct four full ledgers and decompose ordinary portfolio order changes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from .allocation_attribution import COMPONENTS, close, decompose, submitted_quantity
from .audit_feedback import check_feedback
from .audit_quantity_controls import verify_quantity_day
from .industry_shocks import subset_industry_shocks
from .issuer_valuation import subset_issuer_valuation
from .portfolio_audit import audit_portfolio_day

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_allocation_attribution_2019.json"
SOURCE = ROOT / "research_outputs/pre_wuhan_quantity_controls_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_allocation_attribution_2019_v1"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def initial_from_specs(specs, assets, base, case):
    """Independent initial resource reconstruction; no simulation initializer."""
    accounts = {}
    if case["cash_mode"] != "separated" or case.get("initial_cash_weights") is not None:
        raise ValueError("frozen attribution source must use equal separated initial wallets")
    for name, spec in specs.items():
        if spec["kind"] == "strategy":
            cash = Decimal(str(spec["parameters"]["initial_cash"])) * 100
            if cash != cash.to_integral_value():
                raise ValueError("initial strategy cash is not integral minor units")
            index = int(name.rsplit("_", 1)[1])
            amount = base["core"]["inventory"][index]
            wallets = {a: int(cash) for a in assets}
            shares = dict.fromkeys(assets, amount)
        elif spec["kind"] == "background":
            own = spec["asset"]
            cash = Decimal(str(base["background"]["initial_cash"])) * 100
            if own not in assets or cash != cash.to_integral_value():
                raise ValueError("initial background identity or cash differs")
            wallets = {a: int(cash) if a == own else 0 for a in assets}
            shares = {a: base["background"]["initial_shares"] if a == own else 0 for a in assets}
        else:
            raise ValueError("unknown initial participant kind")
        accounts[name] = {"wallets": wallets, "shares": shares, "sellable": dict(shares)}
    return {
        "accounts": accounts, "prices": dict.fromkeys(assets, base["venue"]["price_start_minor"]), "fee_pool_minor": 0,
        "initial_cash_minor": sum(sum(v["wallets"].values()) for v in accounts.values()),
        "initial_shares": {a: sum(v["shares"][a] for v in accounts.values()) for a in assets},
    }


def independent_covariance(day, histories, settings):
    assets = sorted(histories)
    n = settings["volatility_sessions"]
    data = {}
    for a in assets:
        values = [r["price_after_minor"] / r["price_before_minor"] - 1 for r in histories[a] if r["trade_date"] <= day["signal_cutoff_date"]]
        data[a] = ([0.0] * n + values)[-n:]
        check_feedback({"signal_cutoff_date": day["signal_cutoff_date"], "feedback": day["observations"][a]}, histories[a], settings)
    means = {a: sum(v) / n for a, v in data.items()}
    for a in assets:
        for b in assets:
            covariance = sum((x - means[a]) * (y - means[b]) for x, y in zip(data[a], data[b], strict=True)) / (n - 1)
            expected = max(settings["volatility_floor"] ** 2, covariance) if a == b else .75 * covariance
            close(day["covariance"][a][b], expected, "independent covariance")


def empty_group():
    return {"positions": 0, "component_share_equivalent_sums": dict.fromkeys(COMPONENTS, 0.0),
            "factual_delta_share_equivalent_sum": 0.0, "submitted_buy": 0, "submitted_sell": 0,
            "accepted_buy": 0, "accepted_sell": 0, "filled_buy": 0, "filled_sell": 0}


def add_position(group, row):
    group["positions"] += 1
    for component, value in row["component_share_equivalents"].items():
        group["component_share_equivalent_sums"][component] += value
    group["factual_delta_share_equivalent_sum"] += row["factual_delta_share_equivalent"]
    if row["submitted_quantity"]:
        side = row["submitted_side"]
        for stage in ("submitted", "accepted", "filled"):
            group[stage + "_" + side] += row[stage + "_quantity"]


def run(output=OUTPUT, audit_existing=False):
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or (output.exists() and not audit_existing):
        raise ValueError("allocation attribution requires a fresh direct research_outputs directory")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["components"] != COMPONENTS or config["agent_signal_enabled"] is not False:
        raise ValueError("frozen allocation decomposition or disabled gate changed")
    bindings = {str(CONFIG): digest(CONFIG)}
    for path, expected in config["inputs"].items():
        bindings[str((ROOT / path).resolve())] = expected
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    for key in ("inputs", "code_sha256"):
        for path, expected in manifest[key].items():
            resolved = str((ROOT / path).resolve())
            if resolved in bindings and bindings[resolved] != expected:
                raise ValueError("conflicting allocation source bindings")
            bindings[resolved] = expected
    for name, record in manifest["artifacts"].items():
        bindings[str(SOURCE / name)] = record["sha256"]
    paths = [ROOT / ("research/simulation/" + name + ".py") for name in (
        "allocation_attribution", "audit_pre_wuhan_allocation_attribution_2019", "portfolio_audit",
        "audit_quantity_controls", "audit_issuer_valuation", "audit_background_response", "audit_feedback",
        "audit_auction", "industry_shocks", "issuer_valuation")]
    code = {str(p): digest(p) for p in paths}

    def verify():
        for path, expected in {**bindings, **code}.items():
            if digest(path) != expected:
                raise ValueError("allocation source or program hash differs: " + path)

    verify()
    source = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    base = json.loads((ROOT / config["base_config"]).read_text(encoding="utf-8"))
    case = next(c for c in base["cases"] if c["case_id"] == "separated_institution35")
    variants = {v["name"]: v for v in source["variants"]}
    if set(variants) != set(config["variants"]) or source["sample"]["companies"] != 123 or source["sample"]["sessions"] != 43:
        raise ValueError("allocation source grid differs")
    paths_by_key = {(p["variant"], p["basket_index"]): p for p in source["path_summaries"]}
    if len(paths_by_key) != config["expected_paths"]:
        raise ValueError("allocation source paths duplicate or differ")
    codes = source["sample"]["selected_stock_codes"]
    states, signs, histories, clocks, groups, decisions, decision_counts, checks = {}, {}, {}, {}, {}, {}, Counter(), Counter()
    maximum_identity_error = 0.0
    with tempfile.TemporaryDirectory(prefix="allocation-attribution-stage-", dir=output.parent) as temp:
        stage = Path(temp)
        with (stage / "positions.jsonl.gz").open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as archive:
            with gzip.open(SOURCE / "ledger.jsonl.gz", "rt", encoding="utf-8") as handle:
                for line in handle:
                    day = json.loads(line)
                    variant, basket = day["variant"], day["basket_index"]
                    key = (variant, basket)
                    if key not in paths_by_key:
                        raise ValueError("allocation ledger has unknown path")
                    specs = paths_by_key[key]["participant_specs"]
                    assets = sorted(day["observations"])
                    if assets != codes[basket * 3:basket * 3 + 3]:
                        raise ValueError("allocation basket membership changed")
                    if key not in states:
                        states[key] = initial_from_specs(specs, assets, base, case)
                        signs[key] = {n: {"sign": 0, "streak": 0} for n, s in specs.items() if s["kind"] == "strategy"}
                        histories[key] = {a: [] for a in assets}
                        clocks[key] = []
                    previous = states[key]
                    session = len(clocks[key])
                    if day["portfolio_auction"]["session"] != session or day["trade_date"] in clocks[key]:
                        raise ValueError("allocation path sessions omitted, reordered or duplicated")
                    if clocks[key] and day["trade_date"] <= clocks[key][-1]:
                        raise ValueError("allocation source dates are not increasing")
                    controls = day.get("quantity_control_parameters")
                    selected = next(v for v in config["variants"] if v == variant)
                    if config["variant_controls"][selected] != controls:
                        raise ValueError("allocation source applied controls changed")
                    independent_covariance(day, histories[key], base["feedback_parameters"])
                    shocks = {"scenario_id": "common_component", "common": source["shared_design"]["common"],
                              "asset_specific": {a: [0.0] * 43 for a in assets}}
                    counts = verify_quantity_day(
                        day, previous, specs, base["venue"], base["background"], source["background_response"], shocks,
                        subset_industry_shocks(source["shared_design"]["industry"], assets),
                        subset_issuer_valuation(source["issuer_design"], assets), controls, case, signs[key], session)
                    checks.update(counts)
                    strategy_orders = {}
                    for a, call in day["portfolio_auction"]["asset_calls"].items():
                        for order in call["orders"]:
                            checks["all_submitted_orders_reconstructed"] += 1
                            if specs[order["owner"]]["kind"] == "strategy":
                                order_key = (order["owner"], a)
                                if order_key in strategy_orders:
                                    raise ValueError("allocation strategy submitted duplicate asset orders")
                                strategy_orders[order_key] = order
                    for owner, decision in day["decisions"].items():
                        spec = specs[owner]
                        p = spec["parameters"]
                        analysis = decompose(p, decision, day["covariance"])
                        branch = ("risk_liquidation" if decision["risk_liquidation"] else
                                  "confirmation_or_rebalance_wait" if "confirmation_or_rebalance_wait" in decision["reasons"] else "ordinary_allocation")
                        decision_key = variant + ":" + p["role"] + ":" + branch
                        decision_counts[decision_key] += 1
                        checks["decisions_decomposed"] += 1
                        for a in assets:
                            account = previous["accounts"][owner]
                            side, quantity = submitted_quantity(decision["order_weight_changes"][a], decision["nav_minor"], previous["prices"][a], account["shares"][a], base["venue"]["lot_size"])
                            order = strategy_orders.get((owner, a))
                            if (bool(quantity) != bool(order) or order and (order["side"] != side or order["quantity"] != quantity)):
                                raise ValueError("allocation actual submission differs from factual delta/lot/inventory")
                            multiplier = decision["nav_minor"] / previous["prices"][a]
                            values = {c: analysis["component_weight_changes"][c][a] * multiplier for c in COMPONENTS}
                            actual = decision["order_weight_changes"][a] * multiplier
                            maximum_identity_error = max(maximum_identity_error, abs(sum(values.values()) - actual))
                            if not math.isclose(sum(values.values()), actual, rel_tol=1e-12, abs_tol=1e-9):
                                raise ValueError("allocation share-equivalent telescope differs")
                            row = {"variant": variant, "basket_index": basket, "trade_date": day["trade_date"], "session": session,
                                   "owner": owner, "role": p["role"], "stock_code": a, "branch": branch,
                                   "portfolio_belief_score": analysis["portfolio_belief_score"], "asset_belief": decision["beliefs"][a],
                                   "current_weight": decision["current_weights"][a], "desired_weight": decision["desired_weights"][a],
                                   "factual_delta_share_equivalent": actual, "component_share_equivalents": values,
                                   "submitted_side": side if quantity else "hold", "submitted_quantity": quantity,
                                   "accepted_quantity": order["accepted_quantity"] if order else 0,
                                   "filled_quantity": order["filled_quantity"] if order else 0}
                            for label in ("all", "branch:" + branch, "role:" + p["role"], "role_branch:" + p["role"] + ":" + branch,
                                          "score:" + ("positive" if analysis["portfolio_belief_score"] > 1e-12 else "negative" if analysis["portfolio_belief_score"] < -1e-12 else "zero") + ":" + branch):
                                group = groups.setdefault(variant, {}).setdefault(label, empty_group())
                                add_position(group, row)
                            archive.write((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"))
                            checks["asset_positions_decomposed"] += 1
                    states[key] = audit_portfolio_day(day, previous, base["venue"], session, dense=session in config["dense_audit_sessions"])
                    checks["settlement_asset_calls_reconstructed"] += len(assets)
                    checks["dense_asset_price_checks"] += len(assets) if session in config["dense_audit_sessions"] else 0
                    for a, call in day["portfolio_auction"]["asset_calls"].items():
                        histories[key][a].append({"trade_date": day["trade_date"], "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"]})
                    clocks[key].append(day["trade_date"])
                    checks["source_ledger_records"] += 1
                    if checks["source_ledger_records"] % 500 == 0:
                        print("Allocation attribution: audited", checks["source_ledger_records"], "/", config["expected_ledger_records"], flush=True)
        for key, path in paths_by_key.items():
            summary, rebuilt = path["summary"], states[key]
            projected_accounts = {n: {k: a[k] for k in ("wallets", "shares", "sellable")} for n, a in summary["accounts"].items()}
            if (len(clocks[key]) != 43 or rebuilt["accounts"] != projected_accounts or rebuilt["prices"] != summary["final_prices_minor"]
                    or rebuilt["fee_pool_minor"] != summary["fee_pool_minor"]):
                raise ValueError("allocation reconstructed full path differs from final resources")
        if (checks["source_ledger_records"] != config["expected_ledger_records"]
                or checks["decisions_decomposed"] != config["expected_decisions"]
                or checks["asset_positions_decomposed"] != config["expected_asset_positions"]):
            raise ValueError("allocation decomposition coverage differs from frozen source")
        for variant, values in groups.items():
            saved = variants[variant]["strategy_branch_order_quantities"]
            for branch in ("ordinary_allocation", "risk_liquidation", "confirmation_or_rebalance_wait"):
                record = values.get("branch:" + branch, empty_group())
                for side in ("buy", "sell"):
                    for stage_name, saved_name in (("submitted", "requested"), ("accepted", "accepted"), ("filled", "filled")):
                        expected = sum(v for k, v in saved.items() if k.endswith(":" + branch + ":" + side + ":" + saved_name))
                        if record[stage_name + "_" + side] != expected:
                            raise ValueError("allocation factual flow differs from frozen branch totals")
        verify()
        result = {"pipeline_version": config["version"], "sample": source["sample"], "components": COMPONENTS,
                  "groups": groups, "decision_counts": dict(decision_counts), "checks": dict(checks),
                  "maximum_position_share_equivalent_identity_error": maximum_identity_error,
                  "agent_signal_enabled": False, "interpretation": config["interpretation"]}
        (stage / "results.json").write_bytes(serialize(result))
        manifest_out = {"pipeline_version": config["version"], "inputs": dict(sorted(bindings.items())), "code_sha256": code,
                        "artifacts": {name: {"sha256": digest(stage / name)} for name in ("positions.jsonl.gz", "results.json")}}
        (stage / "manifest.json").write_bytes(serialize(manifest_out))
        if audit_existing:
            if any((output / name).read_bytes() != (stage / name).read_bytes() for name in ("positions.jsonl.gz", "results.json", "manifest.json")):
                raise ValueError("allocation attribution archive differs from byte-identical reconstruction")
            print("Allocation attribution artifacts rebuilt byte-identically.")
        else:
            output.mkdir(exist_ok=False)
            for name in ("positions.jsonl.gz", "results.json", "manifest.json"):
                with (output / name).open("xb") as h:
                    h.write((stage / name).read_bytes())
    print(json.dumps({"checks": result["checks"], "maximum_position_share_equivalent_identity_error": maximum_identity_error}, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    run(args.output_dir, args.audit_existing)


if __name__ == "__main__":
    main()
