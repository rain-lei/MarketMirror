"""Independent full raw cash-resource ledger, funding, statistics and legacy audit."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .audit_cash_resource_controls import independent_funding, initial_state, verify_initial_wealth
from .audit_own_price_feedback import verify_arrival, verify_final_resources
from .audit_pre_wuhan_allocation_attribution_2019 import independent_covariance
from .audit_price_feedback_channels import verify_day, verify_specs
from .industry_shocks import subset_industry_shocks
from .issuer_valuation import subset_issuer_valuation
from .portfolio_audit import audit_portfolio_day
from .run_pre_wuhan_cash_resource_controls_2019 import CONFIG, OUTPUT as SOURCE, ROOT, load_inputs
from .run_pre_wuhan_own_price_feedback_2019_v2 import controls_for
from .verify_pre_wuhan_order_price_diagnostics_2019 import compare
from .verify_pre_wuhan_own_price_feedback_2019_v2 import independent_issuer, independent_metrics

OUTPUT = ROOT / "research_outputs/pre_wuhan_cash_resource_statistics_2019_v1.json"
LEGACY = ROOT / "research_outputs/pre_wuhan_price_feedback_channels_2019_v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def primary_values(row):
    a, c = row["comparison"]["synthetic"], row["common_factor_metrics"]["synthetic"]
    return {"mean": a["mean"], "volatility": a["standard_deviation"], "zero_fraction": a["zero_return_fraction"],
            "stock_correlation": c["mean_pairwise_stock_return_correlation"], "portfolio_volatility": c["equal_weight_daily_return_std"]}


def independent_summary(metrics, cells, seeds):
    output, effects = {}, {}
    cell_keys = {(c["background_anchor"], c["feedback_scale"], c["cash_name"]): c["name"] for c in cells}
    for c in cells:
        name = c["name"]
        rows = [metrics[s + "|" + name] for s in seeds]
        changes = []
        for seed, row in zip(seeds, rows):
            base = metrics[seed + "|" + cell_keys[c["background_anchor"], c["feedback_scale"], "original_cash"]]
            def gaps(v):
                return {"mean": v["absolute_mean_gap"], "volatility": abs(v["volatility_ratio"] - 1),
                        "zero_fraction": v["zero_fraction_gap"], "stock_correlation": v["stock_correlation_gap"],
                        "portfolio_volatility": abs(v["portfolio_volatility_ratio"] - 1)}
            now, original = gaps(row["joint_values"]), gaps(base["joint_values"])
            delta = {k: now[k] - original[k] if now[k] is not None and original[k] is not None else None for k in now}
            changes.append({"seed_id": seed, "gap_changes": delta,
                            "all_five_nonworse": all(v is not None and v <= 1e-12 for v in delta.values())})
        output[name] = {"seed_count": len(rows), "joint_pass_seeds": sum(r["all_five_pass"] for r in rows),
            "metric_medians": {k: statistics.median(vals) if vals else None for k in primary_values(rows[0])
                for vals in [[primary_values(r)[k] for r in rows if primary_values(r)[k] is not None]]},
            "paired_gap_changes": changes, "all_five_nonworse_seeds": sum(r["all_five_nonworse"] for r in changes)}
    for anchor in ("initial_inventory", "current_inventory"):
        effects[anchor] = []
        for seed in seeds:
            entry = {"seed_id": seed}
            for cash in ("less_background", "more_background"):
                for f in (1.0, 0.0):
                    base, changed = [primary_values(metrics[seed + "|" + cell_keys[anchor, f, c]]) for c in ("original_cash", cash)]
                    entry[cash + "|" + str(f)] = {k: changed[k] - base[k] if base[k] is not None and changed[k] is not None else None for k in base}
                a, b = entry[cash + "|0.0"], entry[cash + "|1.0"]
                entry[cash + "|interaction"] = {k: a[k] - b[k] if a[k] is not None and b[k] is not None else None for k in a}
            effects[anchor].append(entry)
    return output, effects


def legacy_day(stream, expected):
    for line in stream:
        row = json.loads(line)
        if row["variant"].endswith(("_target_off", "_quote_off")):
            continue
        key = row["seed_id"], row["variant"], row["basket_index"], row["portfolio_auction"]["session"]
        if key != expected:
            raise ValueError("independent original cash bridge identity differs")
        return {k: v for k, v in row.items() if k not in {"seed_id", "variant", "basket_index"}}
    raise ValueError("independent legacy source ended early")


def compute():
    cfg, base, replay, market, codes, dates, joined, inputs, code_hashes, shared, _, _, industry_cfg, legacy = load_inputs()
    bindings = dict(inputs)
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    for field in ("inputs", "code_sha256"):
        for name, expected in manifest[field].items():
            if name in bindings and bindings[name] != expected:
                raise ValueError("independent cash source binding conflicts")
            bindings[name] = expected
    bindings[str(SOURCE / "manifest.json")] = file_sha256(SOURCE / "manifest.json")
    for name, info in manifest["artifacts"].items():
        bindings[str(SOURCE / name)] = info["sha256"]
    for name, expected in bindings.items():
        if file_sha256(Path(name)) != expected:
            raise ValueError("independent cash input/artifact differs: " + name)
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    cells, seeds = cfg["variants"], {s["seed_id"]: s for s in cfg["seeds"]}
    cell_index = {c["name"]: c for c in cells}
    expected_paths = {(s, c["name"], b) for s in seeds for c in cells for b in range(41)}
    paths = {(p["seed_id"], p["variant"], p["basket_index"]): p for p in result["path_summaries"]}
    variants = {(p["seed_id"], p["name"]): p for p in result["variants"]}
    if (set(paths) != expected_paths or len(paths) != len(result["path_summaries"])
            or set(variants) != {(s, c["name"]) for s in seeds for c in cells} or len(variants) != 60):
        raise ValueError("independent cash full grid/path identities differ")
    old_paths = {(p["seed_id"], p["variant"], p["basket_index"]): p for p in legacy["path_summaries"]}
    quantity = json.loads((ROOT / "research_outputs/pre_wuhan_quantity_controls_2019_v1/results.json").read_text(encoding="utf-8"))["result"]
    issuer_seeds = {s: independent_issuer(quantity["issuer_design"], seed) for s, seed in seeds.items()}
    case = next(c for c in base["cases"] if c["case_id"] == "separated_institution35")
    panels = {k: [] for k in variants}
    flows, funding_groups, checks = {k: Counter() for k in variants}, {}, Counter()
    active, counts, receipts = None, Counter(), {}
    with gzip.open(SOURCE / "ledger.jsonl.gz", "rt", encoding="utf-8") as stream, \
         gzip.open(SOURCE / "funding_only.jsonl.gz", "rt", encoding="utf-8") as funding, \
         gzip.open(LEGACY / "ledger.jsonl.gz", "rt", encoding="utf-8") as old:
        for line in stream:
            saved = json.loads(line)
            key = saved["seed_id"], saved["variant"], saved["basket_index"]
            if key not in paths:
                raise ValueError("independent ledger contains undeclared path")
            seed, cell = seeds[key[0]], cell_index[key[1]]
            cash, scale, anchor = cell["cash_control"], cell["feedback_scale"], cell["background_anchor"]
            basket = codes[3 * key[2]:3 * key[2] + 3]
            specs, path = paths[key]["participant_specs"], paths[key]
            day = {k: v for k, v in saved.items() if k not in {"seed_id", "variant", "basket_index"}}
            session = day["portfolio_auction"]["session"]
            if session != counts[key] or session >= 43:
                raise ValueError("independent cash session duplicates or skips")
            if key != active:
                if active is not None:
                    raise ValueError("independent cash paths interleave before completion")
                state = initial_state(specs, basket, base, case, cash, path["initial_cash_resource_receipt"])
                verify_initial_wealth(state, path["summary"])
                baseline = old_paths[key[0], anchor + "_feedback_full", key[2]]
                verify_specs(specs, baseline["participant_specs"], scale, scale)
                core = {**base["core"], "seed": seed["arrival_seed"]}
                background = {**base["background"], "seed": seed["background_seed"]}
                controls = controls_for(anchor)
                shocks = {"scenario_id": "common_component", "common": shared["common"], "asset_specific": {a: [0.0] * 43 for a in basket}}
                industry = subset_industry_shocks(shared["industry"], basket)
                issuer = subset_issuer_valuation(issuer_seeds[key[0]], basket)
                histories = {a: [] for a in basket}
                streaks = {n: {"sign": 0, "streak": 0} for n, s in specs.items() if s["kind"] == "strategy"}
                hasher = hashlib.sha256(b"[")
                wealth = {n: sum(a["wallets"].values()) + sum(a["shares"][s] * state["prices"][s] for s in basket) for n, a in state["accounts"].items()}
                peaks, drawdowns = dict(wealth), dict.fromkeys(specs, 0.0)
                risks = {n: {"risk_breach_sessions": 0, "concentration_breach_sessions": 0} for n in specs}
                path_orders = Counter()
                active = key
                if cell["cash_name"] != "original_cash":
                    checks["initial_resource_receipts_audited"] += 1
                    old_name = anchor + "_feedback_" + ("full" if scale else "both_off")
                else:
                    old_name = anchor + "_feedback_" + ("full" if scale else "both_off")
                    original = old_paths[key[0], old_name, key[2]]
                    compare(path["summary"], original["summary"])
                    compare(specs, original["participant_specs"])
                    if path["initial_cash_resource_receipt"] is not None:
                        raise ValueError("baseline resource receipt should be absent")
                    checks["legacy_complete_paths_matched"] += 1
            if (day["trade_date"] != dates[session] or day["signal_cutoff_date"] != joined[basket[0]][session]["signal_cutoff_date"]
                    or day["execution_reference_date"] != joined[basket[0]][session]["execution_reference_date"]
                    or day.get("initial_cash_resource_control") != (None if cash == {"numerator": 1, "denominator": 1} else cash)
                    or day.get("quantity_control_parameters") != controls):
                raise ValueError("independent cash applied control/calendar differs")
            if set(day["issuer_information_receipts"]) != set(specs):
                raise ValueError("independent cash private receipt owner scope differs")
            for name, spec in specs.items():
                assets = set(basket) if spec["kind"] == "strategy" else {spec["asset"]}
                if set(day["issuer_information_receipts"][name]) != assets or any(type(r["received"]) is not bool for r in day["issuer_information_receipts"][name].values()):
                    raise ValueError("independent cash private asset/flag scope differs")
            verify_arrival(day, specs, core, session)
            independent_covariance(day, histories, base["feedback_parameters"])
            verify_day(day, state, specs, base["venue"], background, industry_cfg["background_response"],
                       shocks, industry, issuer, controls, case, streaks, session, scale, scale)
            h = digest(day["issuer_information_receipts"])
            rk = key[0], key[2], session
            if cell == cells[0]:
                receipts[rk] = h
            elif h != receipts[rk]:
                raise ValueError("independent full cash paths altered private receipt values")
            else:
                checks["paired_private_receipt_values"] += sum(len(v) for v in day["issuer_information_receipts"].values())
            if cell["cash_name"] == "original_cash":
                compare(day, legacy_day(old, (key[0], old_name, key[2], session)))
                checks["legacy_daily_records_matched"] += 1
                # Baseline acceptance/escrows are factual; alternatives carry no fills.
                actual = independent_funding(day, state, specs, base["venue"], {"numerator": 1, "denominator": 1})
                compare(actual["escrows"], day["portfolio_auction"]["escrows"])
                compare(actual["orders"], {a: [{k: o[k] for k in ("order_id", "owner", "side", "quantity", "limit_price_minor", "sequence", "accepted_quantity")}
                                             for o in c["orders"]] for a, c in day["portfolio_auction"]["asset_calls"].items()})
                checks["baseline_funding_records_audited"] += 1
                for name, ctrl in (("less_background", {"numerator": 2, "denominator": 5}), ("more_background", {"numerator": 4, "denominator": 1})):
                    line = next(funding, None)
                    if line is None:
                        raise ValueError("funding-only archive ended early")
                    row = json.loads(line)
                    expected = {"seed_id": key[0], "base_variant": key[1], "background_anchor": anchor,
                        "feedback_scale": scale, "basket_index": key[2], "session": session, "cash_name": name,
                        "prior_state_sha256": digest(state), **independent_funding(day, state, specs, base["venue"], ctrl)}
                    compare(row, expected)
                    gkey = f"{key[0]}|{anchor}|{scale}|{name}"
                    g = funding_groups.setdefault(gkey, {"records": 0, "role_quantities": {}})
                    g["records"] += 1
                    for orders in expected["orders"].values():
                        for o in orders:
                            role = "background" if specs[o["owner"]]["kind"] == "background" else specs[o["owner"]]["parameters"]["role"]
                            t = g["role_quantities"].setdefault(role, Counter())
                            t["requested_" + o["side"]] += o["quantity"]
                            t["accepted_" + o["side"]] += o["accepted_quantity"]
                    checks["same_state_funding_records"] += 1
            for a, call in day["portfolio_auction"]["asset_calls"].items():
                for o in call["orders"]:
                    role = "background" if specs[o["owner"]]["kind"] == "background" else specs[o["owner"]]["parameters"]["role"]
                    for label, field in (("requested", "quantity"), ("accepted", "accepted_quantity"), ("filled", "filled_quantity")):
                        flows[key[:2]][role + "|" + o["side"] + "|" + label] += o[field]
                        if role != "background":
                            path_orders[label] += o[field]
                    if "cash_and_fee_reservation" in o["reasons"]:
                        flows[key[:2]][role + "|" + o["side"] + "|cash_clipped"] += o["quantity"] - o["accepted_quantity"]
                        if role != "background":
                            path_orders["cash_clipped_orders"] += 1
                panels[key[:2]].append({"stock_code": a, "trade_date": dates[session], "signal_cutoff_date": day["signal_cutoff_date"],
                    "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"], "matched_volume": call["matched_volume"],
                    "observed_return": joined[a][session]["observed_return"]})
                histories[a].append({"trade_date": day["trade_date"], "price_before_minor": call["price_before_minor"], "price_after_minor": call["price_after_minor"]})
            state = audit_portfolio_day(day, state, base["venue"], session, dense=session in cfg["dense_audit_sessions"])
            for n, account in state["accounts"].items():
                value = sum(account["wallets"].values()) + sum(account["shares"][a] * state["prices"][a] for a in basket)
                peaks[n] = max(peaks[n], value)
                drawdowns[n] = max(drawdowns[n], 1 - value / peaks[n])
                if specs[n]["kind"] == "strategy":
                    weights = {a: account["shares"][a] * state["prices"][a] / value for a in basket}
                    risk = math.sqrt(max(0.0, sum(weights[a] * day["covariance"][a][b] * weights[b] for a in basket for b in basket)))
                    p = specs[n]["parameters"]
                    risks[n]["risk_breach_sessions"] += int(risk > p["risk_budget"] + 1e-9 or sum(weights.values()) > p["max_weight"] + 1e-9)
                    risks[n]["concentration_breach_sessions"] += int(any(w > day["decisions"][n]["asset_weight_cap"] + 1e-9 for w in weights.values()))
            if session:
                hasher.update(b", ")
            hasher.update(json.dumps(day, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8"))
            counts[key] += 1
            checks["full_path_ledger_records"] += 1
            checks["asset_calls"] += 3
            checks["strategy_allocation_checks"] += 12
            if session in cfg["dense_audit_sessions"]:
                checks["dense_asset_calls"] += 3
            if session == 42:
                verify_final_resources(state, path["summary"])
                hasher.update(b"]")
                if path["summary"]["trace_sha256"] != hasher.hexdigest():
                    raise ValueError("independent complete cash trace hash differs")
                for n, s in path["summary"]["accounts"].items():
                    compare(s["max_drawdown"], drawdowns[n])
                    compare(s["wealth_multiple"], s["final_wealth_minor"] / wealth[n])
                    compare({k: s[k] for k in risks[n]}, risks[n])
                for label in ("requested", "accepted", "filled"):
                    compare(path["summary"]["strategy_" + label], path_orders[label])
                compare(path["summary"]["cash_clipped_orders"], path_orders["cash_clipped_orders"])
                # Keep only known immutable scenario metadata from the frozen
                # original-resource summary; rebuild every changed account/stat.
                rebuilt = dict(old_paths[key[0], old_name, key[2]]["summary"])
                account_summary = {}
                for n, account in state["accounts"].items():
                    final = sum(account["wallets"].values()) + sum(account["shares"][a] * state["prices"][a] for a in basket)
                    account_summary[n] = {"kind": specs[n]["kind"], "role": specs[n].get("parameters", {}).get("role"),
                        **account, "initial_wealth_minor": wealth[n], "final_wealth_minor": final,
                        "wealth_multiple": final / wealth[n], "max_drawdown": drawdowns[n], **risks[n]}
                rebuilt.update(accounts=account_summary, final_prices_minor=state["prices"], fee_pool_minor=state["fee_pool_minor"],
                    strategy_requested=path_orders["requested"], strategy_accepted=path_orders["accepted"],
                    strategy_filled=path_orders["filled"], cash_clipped_orders=path_orders["cash_clipped_orders"],
                    strategy_fill_fraction=path_orders["filled"] / path_orders["accepted"] if path_orders["accepted"] else 0.0,
                    strategy_requested_fill_fraction=path_orders["filled"] / path_orders["requested"] if path_orders["requested"] else 0.0,
                    trace_sha256=hasher.hexdigest(), role_wealth_multiple={role:
                        sum(a["final_wealth_minor"] for a in account_summary.values() if a["role"] == role)
                        / sum(a["initial_wealth_minor"] for a in account_summary.values() if a["role"] == role)
                        for role in ("aggressive", "conservative", "institutional")})
                if cell["cash_name"] != "original_cash":
                    rebuilt["initial_cash_resource_control"] = cash
                compare(path["summary"], rebuilt, "entire_cash_path_summary")
                active = None
            if checks["full_path_ledger_records"] % 5000 == 0:
                print(f"independently audited {checks['full_path_ledger_records']} cash-resource days", flush=True)
        if active is not None or next(funding, None) is not None or next(old, None) is not None:
            raise ValueError("cash-resource archives or bridges have unfinished/extra records")
    checks["conditions"], checks["paths"] = len(variants), len(paths)
    compare(dict(checks), cfg["derived_expected_coverage"])
    compare(dict(checks), result["checks"])
    compare(funding_groups, result["funding_only_groups"])
    metrics = {}
    for key, rows in panels.items():
        saved = variants[key]
        compare(rows, saved["daily_asset_rows"])
        compare(dict(flows[key]), saved["role_order_quantities"])
        measured = independent_metrics(rows, codes, dates, cfg["joint_limits"])
        compare(measured["comparison"], saved["comparison"])
        compare(measured["common_factor_metrics"], saved["common_factor_metrics"])
        compare(measured["joint_values"], saved["joint_checks"]["values"])
        compare(measured["joint_criteria"], saved["joint_checks"]["criteria"])
        compare(measured["all_five_pass"], saved["joint_checks"]["all_five_pass"])
        compare(sum(r["matched_volume"] for r in rows), saved["total_matched_volume"])
        metrics[key[0] + "|" + key[1]] = measured
    summaries, effects = independent_summary(metrics, cells, list(seeds))
    compare(summaries, result["seed_summary"])
    compare(effects, result["paired_cash_effects_and_feedback_interaction"])
    for name, expected in bindings.items():
        if file_sha256(Path(name)) != expected:
            raise ValueError("cash input/output changed during independent read")
    return {"version": "independent-fixed-total-cash-resource-statistics-v1", "status": "PASS", "inputs": bindings,
        "checks": dict(checks), "independent_variant_metrics": metrics, "seed_summary": summaries,
        "paired_cash_effects_and_feedback_interaction": effects, "funding_only_groups": funding_groups,
        "strict_private_receipt_scope_positions": checks["full_path_ledger_records"] * 72,
        "complete_trace_hashes_checked": len(paths), "initial_and_final_wealth_drawdown_risk_checked": len(paths) * 48,
        "interpretation": cfg["interpretation"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    audit = parser.parse_args().audit_existing
    if OUTPUT.exists() and not audit:
        raise ValueError("independent cash output already exists")
    result = compute()
    encoded = (json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if audit:
        if OUTPUT.read_bytes() != encoded:
            raise ValueError("independent cash statistics did not rebuild byte-identically")
        print("Independent fixed-total cash statistics rebuilt byte-identically.", flush=True)
    else:
        OUTPUT.write_bytes(encoded)
        print("Independent fixed-total cash paths, funding-only probes and all statistics audited.", flush=True)
