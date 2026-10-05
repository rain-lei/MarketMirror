"""Independently reconstruct risk metrics with rational wealth/return arithmetic."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

from ..semantic.source_case_facts import ROOT, canonical

CONFIG = ROOT / "research/configs/prototype_risk_diagnostics_2026_v1.json"
ROLES = ("aggressive", "conservative", "institutional")
ROLE_METRICS = ("total_return", "step_return_sample_stdev", "max_close_drawdown",
                "filled_given_accepted", "filled_given_requested", "cash_clipped_quantity_fraction")
COUNT_FIELDS = ("account_steps", "post_close_risk_breach_steps", "post_close_concentration_breach_steps",
    "risk_liquidation_requested_steps", "confirmation_or_rebalance_wait_steps", "orders", "requested_quantity",
    "accepted_quantity", "filled_quantity", "fee_minor", "cash_clipped_orders", "cash_clipped_quantity",
    "accepted_unfilled_quantity", "buy_requested_quantity", "sell_requested_quantity", "signed_filled_quantity")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def equivalent(actual, expected, path="root"):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), "keys differ: " + path)
        for name, value in expected.items():
            equivalent(actual[name], value, path + "/" + name)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), "list scope differs: " + path)
        for index, value in enumerate(expected):
            equivalent(actual[index], value, path + f"/{index}")
    elif type(expected) is float:
        require(type(actual) in (int, float) and math.isfinite(actual)
                and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-14), "metric arithmetic differs: " + path)
    else:
        require(type(actual) is type(expected) and actual == expected, "exact value differs: " + path)


def fraction_stdev(values):
    n = len(values)
    variance = (sum(x * x for x in values) - sum(values) ** 2 / n) / (n - 1)
    require(variance >= 0, "negative rational sample variance")
    return math.sqrt(float(variance))


def performance(levels):
    returns = [Fraction(b - a, a) for a, b in zip(levels, levels[1:])]
    peak = levels[0]
    drawdowns = []
    for nav in levels[1:]:
        peak = max(peak, nav)
        drawdowns.append(Fraction(peak - nav, peak))
    return {"total_return": float(Fraction(levels[-1] - levels[0], levels[0])),
            "step_return_sample_stdev": fraction_stdev(returns),
            "max_close_drawdown": float(max(drawdowns)), "steps": len(returns)}


def nullable_ratio(n, d):
    return float(Fraction(n, d)) if d else None


def reconstruct(raw):
    result = raw["model_result"]
    specs, saved, trace = result["participant_specs"], result["summary"], result["trace"]
    assets = sorted(saved["assets"])
    require(len(assets) == 3 and len(trace) == 18 and len(specs) == 48, "original full path scope required")
    strategies = [n for n, s in specs.items() if s["kind"] == "strategy"]
    require(len(strategies) == 12, "all strategy accounts required")
    levels = {n: [a["initial_wealth_minor"]] for n, a in saved["accounts"].items()}
    closes = {r: [sum(levels[n][0] for n in strategies if specs[n]["parameters"]["role"] == r)] for r in ROLES}
    counts = {r: dict.fromkeys(COUNT_FIELDS, 0) for r in ROLES}
    account_risk = {n: [0, 0] for n in strategies}
    maxima = {r: {"single_asset_wealth_weight": 0., "total_stock_wealth_weight": 0., "covariance_portfolio_risk": 0.} for r in ROLES}
    market = dict.fromkeys(("asset_steps", "unavailable_asset_steps", "flat_asset_steps", "matched_but_flat_asset_steps", "matched_volume"), 0)
    market_returns, fees = [], 0
    prices_before = trace[0]["portfolio_auction"]["prices_before_minor"]
    for step, day in enumerate(trace):
        auction = day["portfolio_auction"]
        require(auction["prices_before_minor"] == prices_before and auction["session"] == step, "ledger state sequence differs")
        prices = {a: auction["asset_calls"][a]["price_after_minor"] for a in assets}
        market_returns.append(sum((Fraction(prices[a] - prices_before[a], prices_before[a]) for a in assets), Fraction(0)) / 3)
        for owner, account in auction["accounts"].items():
            nav = sum(account["wallets"].values()) + sum(account["shares"][a] * prices[a] for a in assets)
            require(nav > 0, "nonpositive realized account value")
            levels[owner].append(nav)
            if owner not in strategies:
                continue
            parameters = specs[owner]["parameters"]
            role = parameters["role"]
            weights = {a: Fraction(account["shares"][a] * prices[a], nav) for a in assets}
            variance = sum((weights[a] * Fraction(str(day["covariance"][a][b])) * weights[b]
                            for a in assets for b in assets), Fraction(0))
            risk = math.sqrt(max(0., float(variance)))
            risk_breach = risk > parameters["risk_budget"] + 1e-9 or float(sum(weights.values())) > parameters["max_weight"] + 1e-9
            concentration_breach = any(float(w) > day["decisions"][owner]["asset_weight_cap"] + 1e-9 for w in weights.values())
            account_risk[owner][0] += int(risk_breach)
            account_risk[owner][1] += int(concentration_breach)
            counter = counts[role]
            counter["account_steps"] += 1
            counter["post_close_risk_breach_steps"] += int(risk_breach)
            counter["post_close_concentration_breach_steps"] += int(concentration_breach)
            counter["risk_liquidation_requested_steps"] += int(day["decisions"][owner]["risk_liquidation"])
            counter["confirmation_or_rebalance_wait_steps"] += int("confirmation_or_rebalance_wait" in day["decisions"][owner]["reasons"])
            maxima[role]["single_asset_wealth_weight"] = max(maxima[role]["single_asset_wealth_weight"], float(max(weights.values())))
            maxima[role]["total_stock_wealth_weight"] = max(maxima[role]["total_stock_wealth_weight"], float(sum(weights.values())))
            maxima[role]["covariance_portfolio_risk"] = max(maxima[role]["covariance_portfolio_risk"], risk)
        for role in ROLES:
            closes[role].append(sum(levels[n][-1] for n in strategies if specs[n]["parameters"]["role"] == role))
        fee_increment = 0
        for a in assets:
            call = auction["asset_calls"][a]
            flat = prices[a] == prices_before[a]
            for k, v in {"asset_steps": 1, "unavailable_asset_steps": int(not call["execution_available"]),
                         "flat_asset_steps": int(flat), "matched_but_flat_asset_steps": int(flat and call["matched_volume"] > 0),
                         "matched_volume": call["matched_volume"]}.items():
                market[k] += v
            for order in call["orders"]:
                fee_increment += order["fee_minor"]
                if order["owner"] not in strategies:
                    continue
                counter = counts[specs[order["owner"]]["parameters"]["role"]]
                q, ac, fill = order["quantity"], order["accepted_quantity"], order["filled_quantity"]
                require(0 <= fill <= ac <= q, "invalid realized quantities")
                clipped = "cash_and_fee_reservation" in order["reasons"]
                additions = {"orders": 1, "requested_quantity": q, "accepted_quantity": ac, "filled_quantity": fill,
                    "fee_minor": order["fee_minor"], "cash_clipped_orders": int(clipped),
                    "cash_clipped_quantity": q - ac if clipped else 0, "accepted_unfilled_quantity": ac - fill,
                    "buy_requested_quantity": q if order["side"] == "buy" else 0,
                    "sell_requested_quantity": q if order["side"] == "sell" else 0,
                    "signed_filled_quantity": fill if order["side"] == "buy" else -fill}
                for k, v in additions.items():
                    counter[k] += v
        fees += fee_increment
        require(fees == auction["fee_pool_minor"], "cumulative real ledger fees differ")
        prices_before = prices
    for owner, navs in levels.items():
        require(navs[-1] == saved["accounts"][owner]["final_wealth_minor"], "raw final wealth differs")
        equivalent(performance(navs)["max_close_drawdown"], float(saved["accounts"][owner]["max_drawdown"]), owner + "/drawdown")
        if owner in account_risk:
            require(account_risk[owner] == [saved["accounts"][owner]["risk_breach_sessions"],
                                         saved["accounts"][owner]["concentration_breach_sessions"]], "raw realized risk summary differs")
    roles = {r: {**performance(closes[r]), "maxima": maxima[r], "counts": counts[r],
        "filled_given_accepted": nullable_ratio(counts[r]["filled_quantity"], counts[r]["accepted_quantity"]),
        "filled_given_requested": nullable_ratio(counts[r]["filled_quantity"], counts[r]["requested_quantity"]),
        "cash_clipped_quantity_fraction": nullable_ratio(counts[r]["cash_clipped_quantity"], counts[r]["requested_quantity"])} for r in ROLES}
    return {"case_id": raw["case_id"], "seed": raw["seed"], "roles": roles,
        "market": {"step_equal_weight_return_sample_stdev": fraction_stdev(market_returns),
                   "flat_asset_step_fraction": nullable_ratio(market["flat_asset_steps"], market["asset_steps"]),
                   "counts": market, "fee_pool_minor": fees},
        "scope": {"paths": 1, "portfolio_steps": 18, "asset_calls": 54, "account_closes": 864,
                  "strategy_account_closes": 216, "final_accounts": 48},
        "performance_is_model_diagnostic_not_real_prediction": True}


def range_direct(values):
    valid = sorted(v for v in values if v is not None)
    median = None if not valid else valid[len(valid) // 2] if len(valid) % 2 else (valid[len(valid) // 2 - 1] + valid[len(valid) // 2]) / 2
    return {"available": len(valid), "missing": len(values) - len(valid),
            "minimum": valid[0] if valid else None, "median": median, "maximum": valid[-1] if valid else None}


def audit(output):
    plan = json.loads(CONFIG.read_text(encoding="utf-8"))
    for name, expected in plan["bindings"].items():
        require(sha(ROOT / name) == expected, "risk input binding changed: " + name)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["protocol_sha256"] == sha(CONFIG) and manifest["bindings"] == plan["bindings"], "risk protocol differs")
    require(set(manifest["artifacts"]) == {"frozen_plan.json", "path_metrics.jsonl", "summary.json"}, "risk artifact scope differs")
    for name, expected in manifest["artifacts"].items():
        require(sha(output / name) == expected, "risk metric artifact changed: " + name)
    require(json.loads((output / "frozen_plan.json").read_text(encoding="utf-8")) == plan, "risk frozen plan differs")
    rows = [json.loads(line) for line in (output / "path_metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    require(len(rows) == len(plan["paths"]) == 240, "all 240 raw paths required")
    counts, role_groups, market_groups, by_key = Counter(), defaultdict(list), defaultdict(list), {}
    for index, (entry, saved) in enumerate(zip(plan["paths"], rows)):
        with gzip.open(ROOT / entry["path"], "rt", encoding="utf-8") as stream:
            raw = json.load(stream)
        expected = reconstruct(raw)
        expected.update(**{k: entry[k] for k in ("study", "mode", "duration_steps")},
                        source_path=entry["path"], source_sha256=sha(ROOT / entry["path"]))
        equivalent(saved, expected, entry["path"])
        counts.update(expected["scope"])
        key = (entry["study"], entry["case_id"], entry["mode"], entry["duration_steps"])
        market_groups[key].append(expected)
        by_key[(*key, entry["seed"])] = expected
        for role in ROLES:
            role_groups[(*key, role)].append(expected)
        if (index + 1) % 30 == 0:
            print(json.dumps({"independently_audited_paths": index + 1}), flush=True)
    aggregates, markets, pairs = [], [], []
    for key, values in sorted(role_groups.items()):
        role = key[-1]
        require(sorted(r["seed"] for r in values) == plan["seeds"], "risk seed coverage differs")
        aggregates.append({"study": key[0], "case_id": key[1], "mode": key[2], "duration_steps": key[3], "role": role,
            "seeds": plan["seeds"], "metrics": {k: range_direct([r["roles"][role][k] for r in values]) for k in ROLE_METRICS},
            "maxima": {k: range_direct([r["roles"][role]["maxima"][k] for r in values]) for k in ("single_asset_wealth_weight", "total_stock_wealth_weight", "covariance_portfolio_risk")},
            "counts": {k: sum(r["roles"][role]["counts"][k] for r in values) for k in COUNT_FIELDS}})
    for key, values in sorted(market_groups.items()):
        require(sorted(r["seed"] for r in values) == plan["seeds"], "market seed coverage differs")
        markets.append({"study": key[0], "case_id": key[1], "mode": key[2], "duration_steps": key[3],
            "metrics": {k: range_direct([r["market"][k] for r in values]) for k in ("step_equal_weight_return_sample_stdev", "flat_asset_step_fraction", "fee_pool_minor")},
            "counts": {k: sum(r["market"]["counts"][k] for r in values) for k in values[0]["market"]["counts"]}})
    for comparison in plan["comparisons"]:
        r, t = (by_key[tuple(comparison[k])] for k in ("reference_key", "treatment_key"))
        roles = {role: {"metric_differences": {k: t["roles"][role][k] - r["roles"][role][k]
                    if r["roles"][role][k] is not None and t["roles"][role][k] is not None else None for k in ROLE_METRICS},
            "risk_breach_step_difference": t["roles"][role]["counts"]["post_close_risk_breach_steps"] - r["roles"][role]["counts"]["post_close_risk_breach_steps"],
            "concentration_breach_step_difference": t["roles"][role]["counts"]["post_close_concentration_breach_steps"] - r["roles"][role]["counts"]["post_close_concentration_breach_steps"]} for role in ROLES}
        pairs.append({**comparison, "roles": roles})
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    equivalent(summary["role_aggregates"], aggregates, "role_aggregates")
    equivalent(summary["market_aggregates"], markets, "market_aggregates")
    equivalent(summary["pairs"], pairs, "pairs")
    counts.update(role_groups=len(aggregates), market_groups=len(markets), pairs=len(pairs))
    require(dict(counts) == plan["expected_counts"] == summary["counts"] == manifest["counts"], "risk audit complete scope differs")
    receipt = {"status": "PASS_ALL_240_ORIGINAL_PATHS_RATIONAL_WEALTH_RETURN_DRAWDOWN_RISK_EXECUTION_AND_ALL_GROUPS",
        "protocol_sha256": sha(CONFIG), "frozen_bindings_verified": len(plan["bindings"]), "counts": dict(counts),
        "auditor_sha256": sha(Path(__file__)), "new_market_paths": 0, "online_model_calls": 0,
        "historical_prediction_proven": False, "goal_complete": False}
    with (output / "independent_verification.json").open("xb") as stream:
        stream.write(canonical(receipt) + b"\n")
    print(json.dumps(receipt), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    audit(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
