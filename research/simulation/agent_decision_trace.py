"""Explain the existing portfolio rule without changing its decisions or state."""
from __future__ import annotations

import copy
import math
from dataclasses import asdict

from .agents import AgentParameters, finite_range
from .portfolio_auction import PortfolioAccount
from .portfolio_market import portfolio_decision

VERSION = "portfolio-agent-decision-explanation-v1"


def explain_decision(agent, profile, account, prices, observations, covariance, case,
                     memory, session, use_text, expected_decision=None, lot_size=100):
    """Return a source trace and the exact archived rule's result on copied memory.

    Belief terms are model assumptions, not inferred market returns. The caller
    supplies pre-decision state; neither that state nor the observations mutate.
    """
    if type(use_text) is not bool or type(session) is not int or session < 0:
        raise ValueError("explicit text flag and nonnegative session required")
    if type(lot_size) is not int or lot_size < 1:
        raise ValueError("positive integer lot size required")
    assets = sorted(prices)
    account.validate(assets)
    if set(observations) != set(assets) or set(covariance) != set(assets):
        raise ValueError("aligned observation and covariance assets required")
    if set(memory) != {"sign", "streak"} or type(memory["sign"]) is not int or memory["sign"] not in (-1, 0, 1):
        raise ValueError("explicit pre-decision sign/streak memory required")
    if type(memory["streak"]) is not int or memory["streak"] < 0:
        raise ValueError("nonnegative integer signal streak required")
    if any(type(v) is not int or v <= 0 for v in prices.values()):
        raise ValueError("positive integer prices required")
    if sum(account.wallets.values()) + sum(account.shares[a] * prices[a] for a in assets) <= 0:
        raise ValueError("positive portfolio wealth required")
    for a in assets:
        for name in ("market_signal", "text_signal"):
            finite_range(observations[a][name], -1, 1, name)
        finite_range(observations[a]["text_uncertainty"], 0, 1, "text uncertainty")
        finite_range(observations[a].get("scenario_shock", 0), -1, 1, "scenario shock")
        if not isinstance(observations[a].get("text_evidence"), str) or not observations[a]["text_evidence"].strip():
            raise ValueError("text evidence identifier required, including disabled/synthetic inputs")
        if set(covariance[a]) != set(assets) or any(
                not math.isfinite(v) for v in covariance[a].values()):
            raise ValueError("finite aligned covariance required")
    after = copy.deepcopy(memory)
    decision = portfolio_decision(agent, profile, account, prices, observations,
                                  covariance, case, after, session, use_text)
    if expected_decision is not None and decision != expected_decision:
        raise ValueError("stored decision differs from the unchanged portfolio rule")
    terms = {}
    for a in assets:
        obs = observations[a]
        raw_market = (obs["market_signal"] * profile["momentum_loading"]
                      + profile["market_bias"] + obs.get("scenario_shock", 0))
        bounded_market = max(-1.0, min(1.0, raw_market))
        market = agent.market_sensitivity * bounded_market
        text = agent.text_sensitivity * obs["text_signal"] if use_text else 0.0
        uncertainty = -agent.uncertainty_aversion * obs["text_uncertainty"] if use_text else 0.0
        terms[a] = {
            "raw_market_signal": raw_market, "bounded_market_signal": bounded_market,
            "market_contribution": market, "text_contribution": text,
            "uncertainty_contribution": uncertainty, "belief": market + text + uncertainty,
            "text_channel_enabled": use_text,
            "text_evidence_declared": obs["text_evidence"],
            "text_evidence_used": obs["text_evidence"] if use_text else None,
            "scenario_evidence": obs.get("scenario_evidence"),
            "market_history_sha256": obs.get("window_return_sha256"),
        }
    # Mirror only the arithmetic explanation; the result above remains authoritative.
    # Use the rule's exact grouping to avoid associativity differences in comparisons.
    beliefs = decision["beliefs"]
    score = sum(beliefs.values()) / len(assets)
    exponent = {a: math.exp(beliefs[a] - max(beliefs.values())) for a in assets}
    distribution = {a: exponent[a] / sum(exponent.values()) for a in assets}
    unconstrained_total = agent.base_weight + 0.4 * score
    bounded_total = min(decision["risk_weight_cap"], max(0.0, unconstrained_total))
    unconstrained_weights = {a: bounded_total * distribution[a] for a in assets}
    concentration_clipped = {a: min(decision["asset_weight_cap"], unconstrained_weights[a])
                             for a in assets}
    confirmation_due = bool(after["sign"] and after["streak"] < agent.confirmation_steps)
    rebalance_due = bool(session % agent.rebalance_interval)
    delta = decision["order_weight_changes"]
    requests = {}
    for a in assets:
        quantity = math.floor(abs(delta[a]) * decision["nav_minor"] / (prices[a] * lot_size)) * lot_size
        if delta[a] < 0:
            quantity = min(quantity, account.shares[a])
        requests[a] = {
            "weight_change": delta[a],
            "action": "buy" if quantity and delta[a] > 0 else "sell" if quantity else "hold",
            "whole_lot_quantity": quantity,
            "positive_weight_change_below_lot": bool(delta[a] > 0 and quantity == 0),
        }
    explanation = {
        "version": VERSION, "actor": agent.name, "role": agent.role,
        "session": session, "parameters": asdict(agent), "profile": dict(profile),
        "account_before": asdict(account), "prices_before_minor": dict(prices),
        "memory_before": copy.deepcopy(memory), "memory_after": after,
        "belief_terms": terms, "average_belief": score,
        "allocation_distribution": distribution,
        "unconstrained_total_weight": unconstrained_total,
        "bounded_total_weight": bounded_total,
        "weights_before_concentration_cap": unconstrained_weights,
        "weights_after_concentration_cap": concentration_clipped,
        "gates": {
            "confirmation_wait_condition": confirmation_due,
            "rebalance_wait_condition": rebalance_due,
            "waiting_applied": "confirmation_or_rebalance_wait" in decision["reasons"],
            "risk_liquidation_priority": decision["risk_liquidation"],
            "actual_portfolio_risk_cap": "actual_portfolio_risk_cap" in decision["reasons"],
            "minimum_trade_applied": "minimum_portfolio_trade" in decision["reasons"],
            "turnover_cap_applied": "portfolio_turnover_cap" in decision["reasons"],
        },
        "desired_weights": dict(decision["desired_weights"]),
        "requested_orders": requests,
        "decision_reasons": list(decision["reasons"]),
    }
    return {"decision": decision, "explanation": explanation}


def explain_path(result, initial_accounts, case, venue_settings, use_text):
    """Attach explanations and actual fills to every strategy decision in a path."""
    if any("issuer_information_receipts" in day for day in result["trace"]):
        raise ValueError("private issuer receipts require an actor-specific observation adapter")
    specs = result["participant_specs"]
    owners = sorted(n for n, spec in specs.items() if spec["kind"] == "strategy")
    if not owners or set(initial_accounts) != set(specs):
        raise ValueError("complete initial accounts and participant specifications required")
    before = {n: asdict(a) for n, a in initial_accounts.items()}
    memories = {n: {"sign": 0, "streak": 0} for n in owners}
    assets = sorted(result["summary"]["assets"])
    prices = dict.fromkeys(assets, venue_settings["price_start_minor"])
    rows = []
    for session, day in enumerate(result["trace"]):
        if not day["signal_cutoff_date"] < day["execution_reference_date"] < day["trade_date"]:
            raise ValueError("cutoff, reference and decision dates must be ordered")
        auction = day["portfolio_auction"]
        if auction["session"] != session or auction["prices_before_minor"] != prices:
            raise ValueError("trace session or pre-decision prices differ")
        if set(day["decisions"]) != set(owners):
            raise ValueError("all strategy decisions required")
        for owner in owners:
            account = PortfolioAccount(**copy.deepcopy(before[owner]))
            agent = AgentParameters(**specs[owner]["parameters"])
            explained = explain_decision(agent, specs[owner]["profile"], account, prices,
                day["observations"], day["covariance"], case, memories[owner], session, use_text,
                day["decisions"][owner], venue_settings["lot_size"])
            row = explained["explanation"]
            memories[owner] = copy.deepcopy(row["memory_after"])
            executions = {}
            for a in assets:
                orders = [o for o in auction["asset_calls"][a]["orders"] if o["owner"] == owner]
                requested = row["requested_orders"][a]
                if len(orders) > 1 or bool(orders) != bool(requested["whole_lot_quantity"]):
                    raise ValueError("whole-lot decision and submitted orders differ")
                if orders and (orders[0]["quantity"] != requested["whole_lot_quantity"]
                               or orders[0]["side"] != requested["action"]):
                    raise ValueError("submitted side or quantity differs from decision")
                order = orders[0] if orders else None
                executions[a] = {
                    "requested_quantity": order["quantity"] if order else 0,
                    "accepted_quantity": order["accepted_quantity"] if order else 0,
                    "filled_quantity": order["filled_quantity"] if order else 0,
                    "side": order["side"] if order else "hold",
                    "execution_reasons": order["reasons"] if order else [],
                    "closing_shares": auction["accounts"][owner]["shares"][a],
                }
            row.update(trade_date=day["trade_date"], signal_cutoff_date=day["signal_cutoff_date"],
                       execution_reference_date=day["execution_reference_date"],
                       execution=executions, account_after=copy.deepcopy(auction["accounts"][owner]))
            rows.append(row)
        before = copy.deepcopy(auction["accounts"])
        prices = {a: auction["asset_calls"][a]["price_after_minor"] for a in assets}
    return rows


def audit_explanations(rows, result):
    """Check explanatory terms and fill bookkeeping directly from saved raw records.

    This does not re-run the producer or the portfolio rule. Full monetary
    settlement is checked separately by the existing independent auction audit.
    """
    owners = {n for n, s in result["participant_specs"].items() if s["kind"] == "strategy"}
    expected = {(session, owner) for session in range(len(result["trace"])) for owner in owners}
    seen = set()
    memories = {n: {"sign": 0, "streak": 0} for n in owners}
    for row in rows:
        key = row["session"], row["actor"]
        if key not in expected or key in seen:
            raise ValueError("missing, duplicate or unexpected explanation")
        seen.add(key)
        day = result["trace"][row["session"]]
        spec = result["participant_specs"][row["actor"]]
        params, profile = spec["parameters"], spec["profile"]
        actual = day["decisions"][row["actor"]]
        if row["parameters"] != params or row["profile"] != profile or row["role"] != params["role"]:
            raise ValueError("role explanation parameters differ")
        if (row["version"] != VERSION
                or any(row[name] != day[name] for name in
                       ("trade_date", "signal_cutoff_date", "execution_reference_date"))
                or row["prices_before_minor"] != day["portfolio_auction"]["prices_before_minor"]
                or set(row["belief_terms"]) != set(day["observations"])):
            raise ValueError("explanation version, clock, prices or asset coverage differs")
        score = sum(actual["beliefs"].values()) / len(actual["beliefs"])
        sign = (score > 1e-12) - (score < -1e-12)
        previous = memories[row["actor"]]
        streak = previous["streak"] + 1 if sign and sign == previous["sign"] else (1 if sign else 0)
        after = {"sign": sign, "streak": streak}
        if row["memory_before"] != previous or row["memory_after"] != after:
            raise ValueError("explanation confirmation memory differs")
        memories[row["actor"]] = after
        expected_gates = {
            "confirmation_wait_condition": bool(sign and streak < params["confirmation_steps"]),
            "rebalance_wait_condition": bool(row["session"] % params["rebalance_interval"]),
            "waiting_applied": "confirmation_or_rebalance_wait" in actual["reasons"],
            "risk_liquidation_priority": actual["risk_liquidation"],
            "actual_portfolio_risk_cap": "actual_portfolio_risk_cap" in actual["reasons"],
            "minimum_trade_applied": "minimum_portfolio_trade" in actual["reasons"],
            "turnover_cap_applied": "portfolio_turnover_cap" in actual["reasons"],
        }
        if row["gates"] != expected_gates or row["decision_reasons"] != actual["reasons"]:
            raise ValueError("explanation waiting or risk gates differ")
        if row["desired_weights"] != actual["desired_weights"]:
            raise ValueError("explanation desired weights differ")
        exp_values = {a: math.exp(v - max(actual["beliefs"].values())) for a, v in actual["beliefs"].items()}
        distribution = {a: v / sum(exp_values.values()) for a, v in exp_values.items()}
        unconstrained = params["base_weight"] + 0.4 * score
        bounded = min(actual["risk_weight_cap"], max(0, unconstrained))
        for name, value in (("average_belief", score), ("unconstrained_total_weight", unconstrained),
                            ("bounded_total_weight", bounded)):
            if not math.isclose(row[name], value, abs_tol=1e-12):
                raise ValueError("explanation aggregate belief or target differs")
        for a, weight in distribution.items():
            for name, value in (("allocation_distribution", weight),
                    ("weights_before_concentration_cap", bounded * weight),
                    ("weights_after_concentration_cap", min(actual["asset_weight_cap"], bounded * weight))):
                if not math.isclose(row[name][a], value, abs_tol=1e-12):
                    raise ValueError("explanation allocation or concentration cap differs")
        for a, obs in day["observations"].items():
            term = row["belief_terms"][a]
            enabled = term["text_channel_enabled"]
            if type(enabled) is not bool or enabled != result["summary"]["use_text"]:
                raise ValueError("explanation text mode differs")
            raw = obs["market_signal"] * profile["momentum_loading"] + profile["market_bias"] + obs.get("scenario_shock", 0)
            clamp = min(1.0, max(-1.0, raw))
            expected_values = {
                "raw_market_signal": raw, "bounded_market_signal": clamp,
                "market_contribution": params["market_sensitivity"] * clamp,
                "text_contribution": params["text_sensitivity"] * obs["text_signal"] if enabled else 0.0,
                "uncertainty_contribution": -params["uncertainty_aversion"] * obs["text_uncertainty"] if enabled else 0.0,
            }
            for name, value in expected_values.items():
                if not math.isclose(term[name], value, rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError("explanation belief contribution differs")
            if (term["text_evidence_declared"] != obs["text_evidence"]
                    or term["text_evidence_used"] != (obs["text_evidence"] if enabled else None)
                    or term["scenario_evidence"] != obs.get("scenario_evidence")
                    or term["market_history_sha256"] != obs.get("window_return_sha256")):
                raise ValueError("explanation evidence differs")
            direct = sum(expected_values[k] for k in ("market_contribution", "text_contribution", "uncertainty_contribution"))
            if not math.isclose(term["belief"], direct, abs_tol=1e-12) or not math.isclose(direct, actual["beliefs"][a], abs_tol=1e-12):
                raise ValueError("explanation belief sum differs")
            execution = row["execution"][a]
            orders = [o for o in day["portfolio_auction"]["asset_calls"][a]["orders"] if o["owner"] == row["actor"]]
            requested = row["requested_orders"][a]
            if (requested["weight_change"] != actual["order_weight_changes"][a]
                    or requested["whole_lot_quantity"] != sum(o["quantity"] for o in orders)
                    or requested["action"] != (orders[0]["side"] if orders else "hold")
                    or requested["positive_weight_change_below_lot"] !=
                       bool(actual["order_weight_changes"][a] > 0 and not orders)):
                raise ValueError("explanation intended and submitted request differs")
            if execution["filled_quantity"] != sum(o["filled_quantity"] for o in orders):
                raise ValueError("explanation actual fill differs")
            if execution["accepted_quantity"] != sum(o["accepted_quantity"] for o in orders) or execution["requested_quantity"] != sum(o["quantity"] for o in orders):
                raise ValueError("explanation request/acceptance differs")
            if (execution["side"] != (orders[0]["side"] if orders else "hold")
                    or execution["execution_reasons"] != (orders[0]["reasons"] if orders else [])):
                raise ValueError("explanation execution side or reasons differ")
            if execution["closing_shares"] != day["portfolio_auction"]["accounts"][row["actor"]]["shares"][a]:
                raise ValueError("explanation closing shares differ")
        if row["account_after"] != day["portfolio_auction"]["accounts"][row["actor"]]:
            raise ValueError("explanation closing account differs")
    if seen != expected:
        raise ValueError("missing decision explanations")
    return {"status": "PASS_ALL_EXPLANATORY_BELIEF_TERMS_EVIDENCE_AND_ACTUAL_FILL_REFERENCES",
            "decisions": len(seen), "asset_explanations": sum(len(row["belief_terms"]) for row in rows)}
