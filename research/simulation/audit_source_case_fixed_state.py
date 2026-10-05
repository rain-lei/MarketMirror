"""Audit saved fixed-state comparisons directly against frozen source paths."""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from ..semantic.source_case_facts import ROOT, canonical
from .agents import AgentParameters
from .portfolio_auction import PortfolioAccount
from .portfolio_market import portfolio_decision
from .run_source_case_decisions import load_study
from .source_case_decision_link import case_inputs

CONFIG = ROOT / "research/configs/source_case_fixed_state_2026_v1.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def request_signature(row):
    return [(a, o["action"], o["whole_lot_quantity"])
            for a, o in sorted(row["explanation"]["requested_orders"].items())]


def audit_explanation(row, state, parameters):
    """Check exact rule replay plus the saved explanatory arithmetic and requests."""
    agent = AgentParameters(**parameters)
    account = PortfolioAccount(**copy.deepcopy(state["account"]))
    memory = copy.deepcopy(state["memory"])
    use_text = row["mode"] != "no_text"
    decision = portfolio_decision(agent, state["profile"], account, state["prices"], row["observations"],
                                  state["covariance"], state["portfolio_case"], memory, state["session"], use_text)
    check(row["decision"] == decision, "counterfactual decision differs from the unchanged rule")
    ex = row["explanation"]
    expected = {"version": "portfolio-agent-decision-explanation-v1", "actor": agent.name, "role": agent.role,
        "session": state["session"], "parameters": parameters, "profile": state["profile"],
        "account_before": state["account"], "prices_before_minor": state["prices"],
        "memory_before": state["memory"], "memory_after": memory,
        "desired_weights": decision["desired_weights"], "decision_reasons": decision["reasons"]}
    for name, value in expected.items():
        check(ex[name] == value, "saved explanation input differs: " + name)
    assets = sorted(state["prices"])
    for asset in assets:
        obs = row["observations"][asset]
        raw_market = (obs["market_signal"] * state["profile"]["momentum_loading"]
                      + state["profile"]["market_bias"] + obs.get("scenario_shock", 0))
        bounded = max(-1., min(1., raw_market))
        market = agent.market_sensitivity * bounded
        text = agent.text_sensitivity * obs["text_signal"] if use_text else 0.
        uncertainty = -agent.uncertainty_aversion * obs["text_uncertainty"] if use_text else 0.
        terms = {"raw_market_signal": raw_market, "bounded_market_signal": bounded,
            "market_contribution": market, "text_contribution": text, "uncertainty_contribution": uncertainty,
            "belief": market + text + uncertainty, "text_channel_enabled": use_text,
            "text_evidence_declared": obs["text_evidence"],
            "text_evidence_used": obs["text_evidence"] if use_text else None,
            "scenario_evidence": obs.get("scenario_evidence"),
            "market_history_sha256": obs.get("window_return_sha256")}
        check(ex["belief_terms"][asset] == terms, "source-grounded belief explanation differs")
    beliefs = decision["beliefs"]
    mean = sum(beliefs.values()) / len(assets)
    exponent = {a: math.exp(beliefs[a] - max(beliefs.values())) for a in assets}
    distribution = {a: exponent[a] / sum(exponent.values()) for a in assets}
    unbounded = agent.base_weight + 0.4 * mean
    bounded_total = min(decision["risk_weight_cap"], max(0., unbounded))
    weights = {a: bounded_total * distribution[a] for a in assets}
    arithmetic = {"average_belief": mean, "allocation_distribution": distribution,
        "unconstrained_total_weight": unbounded, "bounded_total_weight": bounded_total,
        "weights_before_concentration_cap": weights,
        "weights_after_concentration_cap": {a: min(decision["asset_weight_cap"], weights[a]) for a in assets}}
    for name, value in arithmetic.items():
        check(ex[name] == value, "target arithmetic differs: " + name)
    gates = {"confirmation_wait_condition": bool(memory["sign"] and memory["streak"] < agent.confirmation_steps),
        "rebalance_wait_condition": bool(state["session"] % agent.rebalance_interval),
        "waiting_applied": "confirmation_or_rebalance_wait" in decision["reasons"],
        "risk_liquidation_priority": decision["risk_liquidation"],
        "actual_portfolio_risk_cap": "actual_portfolio_risk_cap" in decision["reasons"],
        "minimum_trade_applied": "minimum_portfolio_trade" in decision["reasons"],
        "turnover_cap_applied": "portfolio_turnover_cap" in decision["reasons"]}
    check(ex["gates"] == gates, "confirmation, risk or turnover gates differ")
    requests = {}
    for a, delta in decision["order_weight_changes"].items():
        quantity = math.floor(abs(delta) * decision["nav_minor"] / (state["prices"][a] * state["lot_size"])) * state["lot_size"]
        if delta < 0:
            quantity = min(quantity, account.shares[a])
        requests[a] = {"weight_change": delta,
            "action": "buy" if quantity and delta > 0 else "sell" if quantity else "hold",
            "whole_lot_quantity": quantity, "positive_weight_change_below_lot": bool(delta > 0 and quantity == 0)}
    check(ex["requested_orders"] == requests, "signed whole-lot requests differ")
    return True


def audit(output):
    plan = json.loads(CONFIG.read_text(encoding="utf-8"))
    for name, expected in plan["bindings"].items():
        check(sha(ROOT / name) == expected, "frozen fixed-state binding changed: " + name)
    _, cases, records, mapping, mechanism = load_study()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    check(manifest["protocol_sha256"] == sha(CONFIG), "protocol hash differs")
    check(manifest["bindings"] == plan["bindings"], "manifest bindings differ")
    check(json.loads((output / "frozen_plan.json").read_text(encoding="utf-8")) == plan, "frozen plan differs")
    expected_names = {f"case{i}_seed{s}_fixed_state.json.gz" for i in range(6) for s in plan["seeds"]}
    check(set(manifest["artifacts"]) == expected_names | {"summary.json", "frozen_plan.json"}, "artifact scope reduced")
    check({p.name for p in output.glob("*.json.gz")} == expected_names, "saved case coverage differs")
    for name, expected in manifest["artifacts"].items():
        check(sha(output / name) == expected, "saved fixed-state artifact changed: " + name)
    counts, aggregates = Counter(), {}
    variants = [("no_text", 1.), ("keywords", 1.)] + [
        (m, s) for m in ("reviewed_llm", "llm_asset_placebo") for s in (0.5, 1., 1.5)]
    pairs = [("no_text:1", "keywords:1"), ("no_text:1", "reviewed_llm:1"),
             ("keywords:1", "reviewed_llm:1"), ("reviewed_llm:1", "llm_asset_placebo:1"),
             ("reviewed_llm:1", "reviewed_llm:0.5"), ("reviewed_llm:1", "reviewed_llm:1.5")]
    for index, case in enumerate(cases):
        source_inputs = {m: case_inputs(case, records[case["case_id"]], mapping, m)
                         for m in ("no_text", "keywords", "reviewed_llm", "llm_asset_placebo")}
        for seed in plan["seeds"]:
            baseline_path = ROOT / plan["reference_directory"] / f"case{index}_seed{seed}_no_text.json.gz"
            with gzip.open(baseline_path, "rt", encoding="utf-8") as stream:
                baseline = json.load(stream)
            name = f"case{index}_seed{seed}_fixed_state.json.gz"
            with gzip.open(output / name, "rt", encoding="utf-8") as stream:
                raw = json.load(stream)
            check(raw["case_id"] == case["case_id"] and raw["seed"] == seed
                  and raw["case_text_sha256"] == case["case_text_sha256"], "case identity differs")
            check(raw["reference_path"] == baseline_path.relative_to(ROOT).as_posix()
                  and raw["reference_sha256"] == sha(baseline_path), "baseline reference differs")
            check(raw["no_new_market_paths_or_model_calls"] is True and raw["historical_market_effect_identified"] is False,
                  "counterfactual interpretation changed")
            links = baseline["source_links"]
            check(links == source_inputs["no_text"][1], "baseline source clock differs")
            visible = min(r["step"] for r in links if r["source_visible"])
            last_active = max(r["step"] for r in links if r["information_active"])
            inactive = min(r["step"] for r in links if r["step"] > last_active and not r["information_active"])
            checkpoints = {"before_publication": visible - 1, "first_visible": visible, "first_inactive": inactive}
            check(raw["checkpoints"] == checkpoints, "checkpoint scope differs")
            specs = baseline["model_result"]["participant_specs"]
            owners = sorted(n for n, v in specs.items() if v["kind"] == "strategy")
            saved = {(r["session"], r["actor"]): r for r in baseline["decision_explanations"]}
            units = [("within_actor", n, n) for n in owners] + [
                ("common_role_state", r + "_00", "aggressive_00") for r in ("aggressive", "conservative", "institutional")]
            expected_groups = [(c, context, actor, origin) for c in checkpoints for context, actor, origin in units]
            check([(g["checkpoint"], g["context"], g["actor"], g["state_source_actor"]) for g in raw["groups"]]
                  == expected_groups, "matched state or role coverage differs")
            common_hashes = {}
            counts.update(reference_paths=1, checkpoints=3, groups=len(expected_groups))
            for group in raw["groups"]:
                step = checkpoints[group["checkpoint"]]
                day = baseline["model_result"]["trace"][step]
                original = saved[step, group["state_source_actor"]]
                state = {"account": original["account_before"], "prices": original["prices_before_minor"],
                    "observations": day["observations"], "covariance": day["covariance"],
                    "memory": original["memory_before"], "session": step, "profile": original["profile"],
                    "portfolio_case": mechanism["portfolio_case"], "lot_size": mechanism["venue"]["lot_size"],
                    "signal_cutoff_date": day["signal_cutoff_date"], "trade_date": day["trade_date"]}
                state_hash = hashlib.sha256(canonical(state)).hexdigest()
                check(group["state"] == state and group["state_sha256"] == state_hash, "fixed state is confounded")
                check(group["actual_fills_computed"] is False, "unexecuted orders presented as fills")
                if group["context"] == "common_role_state":
                    prior = common_hashes.setdefault(group["checkpoint"], state_hash)
                    check(prior == state_hash, "three roles did not share the same state")
                parameters = specs[group["actor"]]["parameters"]
                check(group["role"] == parameters["role"], "role differs from preset parameters")
                check([(r["mode"], r["strength"]) for r in group["rows"]] == variants, "mapping strength scope differs")
                by_variant = {}
                for row in group["rows"]:
                    mode, strength = row["mode"], row["strength"]
                    check(row["variant"] == f"{mode}:{strength:g}" and row["fixed_state_sha256"] == state_hash,
                          "paired state fingerprint differs")
                    observations = copy.deepcopy(state["observations"])
                    joined, source_links = source_inputs[mode]
                    for a, obs in observations.items():
                        if mode != "no_text":
                            source = joined[a][step]
                            obs.update(text_signal=max(-1., min(1., source["text_signal"] * strength)),
                                       text_uncertainty=max(0., min(1., source["text_uncertainty"] * strength)),
                                       text_evidence=source["text_evidence"] + f":assumed-strength:{strength:g}")
                    check(row["observations"] == observations, "non-text state or as-of mapping changed")
                    expected_link = {**copy.deepcopy(source_links[step]), "mapping_strength": strength,
                        "strength_is_assumed": True, "applied_by_asset": {
                            a: {"signal": o["text_signal"], "uncertainty": o["text_uncertainty"]} for a, o in observations.items()}}
                    check(row["source_link"] == expected_link, "exact source linkage differs")
                    audit_explanation(row, state, parameters)
                    by_variant[row["variant"]] = row
                expected_pairs = []
                for reference, treatment in pairs:
                    r, t = by_variant[reference], by_variant[treatment]
                    expected_pair = {"reference": reference, "treatment": treatment,
                        "beliefs_changed": r["decision"]["beliefs"] != t["decision"]["beliefs"],
                        "targets_changed": r["decision"]["desired_weights"] != t["decision"]["desired_weights"],
                        "requests_changed": request_signature(r) != request_signature(t),
                        "gates_changed": r["explanation"]["gates"] != t["explanation"]["gates"],
                        "actual_fills_computed": False}
                    if group["checkpoint"] != "first_visible":
                        check(r["decision"] == t["decision"], "unavailable or expired text changed a fixed-state decision")
                    expected_pairs.append(expected_pair)
                    key = "|".join((case["case_id"], group["context"], group["checkpoint"], group["role"], reference, treatment))
                    aggregate = aggregates.setdefault(key, Counter())
                    aggregate.update(comparisons=1)
                    aggregate.update({k: int(expected_pair[k]) for k in ("beliefs_changed", "targets_changed", "requests_changed", "gates_changed")})
                check(group["comparisons"] == expected_pairs, "direct counterfactual pairs differ")
                counts.update(decisions=8, asset_explanations=24, mode_pairs=6)
            print(json.dumps({"audited_case": index, "seed": seed, "decisions": counts["decisions"]}), flush=True)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    check(dict(counts) == plan["expected_counts"] == summary["counts"] == manifest["counts"], "complete audit scope differs")
    check(summary["aggregates"] == {k: dict(v) for k, v in sorted(aggregates.items())}, "reported aggregates differ")
    check(summary["independent_event_count"] == 6 and summary["new_market_paths"] == 0 and summary["online_model_calls"] == 0
          and summary["actual_fills_computed"] is False and summary["historical_market_effect_identified"] is False,
          "study interpretation changed")
    result = {"status": "PASS_ALL_SIX_CASES_FIXED_STATE_SOURCE_CLOCKS_EXACT_RULES_SIGNED_REQUESTS_AND_PAIRS",
        "protocol_sha256": sha(CONFIG), "counts": dict(counts), "frozen_bindings_verified": len(plan["bindings"]),
        "independent_event_count": 6, "new_market_paths": 0, "online_model_calls": 0,
        "actual_fills_computed": False, "historical_market_effect_identified": False,
        "auditor_sha256": sha(Path(__file__)), "goal_complete": False}
    with (output / "independent_verification.json").open("xb") as stream:
        stream.write(canonical(result) + b"\n")
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    audit(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
