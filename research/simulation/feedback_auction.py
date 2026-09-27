"""Finite auction paths with causal own-price feedback and explicit cohort beliefs.

This is a versioned successor: the already archived conditioned auction remains
unchanged. All heterogeneity parameters are model assumptions, not estimates.
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
from dataclasses import asdict, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .agents import AgentParameters, AgentState, Observation, decide, finite_range
from .call_auction import AuctionAccount, CallAuction, LimitOrder
from .semantic_auction_experiment import _validate_venue
from .semantic_memory_sensitivity import canonical_hash
from .semantic_replay import _check_steps

VERSION = "own-price-feedback-auction-v1"
ROLES = ("aggressive", "conservative", "institutional")
NEUTRAL = dict(momentum_loading=1.0, market_bias=0.0, text_multiplier=1.0,
               weight_offset=0.0, risk_multiplier=1.0)
PROFILES = [
    dict(momentum_loading=-1.0, market_bias=-0.15, text_multiplier=0.6, weight_offset=-0.04, risk_multiplier=0.85),
    dict(momentum_loading=0.5, market_bias=-0.05, text_multiplier=0.9, weight_offset=-0.01, risk_multiplier=0.95),
    dict(momentum_loading=1.0, market_bias=0.05, text_multiplier=1.1, weight_offset=0.01, risk_multiplier=1.05),
    dict(momentum_loading=1.5, market_bias=0.15, text_multiplier=1.4, weight_offset=0.04, risk_multiplier=1.15),
]


def validate_feedback(settings: dict) -> None:
    if not isinstance(settings, dict) or set(settings) != {"momentum_sessions", "volatility_sessions", "volatility_floor"}:
        raise ValueError("feedback windows and volatility floor must be explicit")
    for key in ("momentum_sessions", "volatility_sessions"):
        if type(settings[key]) is not int or not 2 <= settings[key] <= 120:
            raise ValueError("feedback windows must be integer sessions between 2 and 120")
    if settings["momentum_sessions"] > settings["volatility_sessions"]:
        raise ValueError("feedback momentum window exceeds volatility window")
    finite_range(settings["volatility_floor"], 1e-6, 1.0, "feedback volatility floor")


def validate_scenario(scenario: dict, lot: int) -> None:
    fields = {"scenario_id", "feedback_mode", "profile_mode", "order_mode", "seed", "inventory"}
    if not isinstance(scenario, dict) or set(scenario) != fields:
        raise ValueError("feedback scenario requires all explicit fields")
    if not isinstance(scenario["scenario_id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scenario["scenario_id"]):
        raise ValueError("invalid feedback scenario identifier")
    if (scenario["feedback_mode"] not in {"conditioned", "endogenous"}
            or scenario["profile_mode"] not in {"homogeneous", "fixed_cohort_v1"}
            or scenario["order_mode"] not in {"forward", "reverse", "hashed"}):
        raise ValueError("unsupported feedback, profile or order mode")
    if type(scenario["seed"]) is not int or not 0 <= scenario["seed"] <= 2**32 - 1:
        raise ValueError("arrival seed must be a bounded nonnegative integer")
    if scenario["order_mode"] != "hashed" and scenario["seed"] != 0:
        raise ValueError("a fixed order must declare seed zero")
    inventory = scenario["inventory"]
    if (not isinstance(inventory, list) or len(inventory) != 4
            or any(type(q) is not int or q < 0 or q % lot for q in inventory) or not any(inventory)):
        raise ValueError("scenario inventory requires four nonnegative whole-lot holdings")


def participants(role_agents: list[AgentParameters], scenario: dict) -> list[tuple[AgentParameters, dict]]:
    if len(role_agents) != 3 or {agent.role for agent in role_agents} != set(ROLES):
        raise ValueError("feedback market requires one parameter set per role")
    output = []
    for agent in role_agents:
        for index in range(4):
            profile = dict(NEUTRAL if scenario["profile_mode"] == "homogeneous" else PROFILES[index])
            output.append((replace(agent, name=f"{agent.role}_{index:02d}",
                                   base_weight=min(agent.max_weight, max(0.0, agent.base_weight + profile["weight_offset"])),
                                   text_sensitivity=agent.text_sensitivity * profile["text_multiplier"],
                                   risk_budget=agent.risk_budget * profile["risk_multiplier"]), profile))
    return output


def arrival_order(names: list[str], mode: str, seed: int, session: int) -> list[str]:
    if mode == "forward":
        return list(names)
    if mode == "reverse":
        return list(reversed(names))
    if mode != "hashed":
        raise ValueError("unsupported arrival order")
    return sorted(names, key=lambda name: (hashlib.sha256(f"{seed}:{session}:{name}".encode()).hexdigest(), name))


def price_observation(history: list[dict], cutoff: str, settings: dict) -> dict:
    """Use only this path's closing returns dated at or before the text cutoff.

    Missing warmup returns are zero-padded to the declared fixed windows. A
    declared volatility floor avoids division by zero in a flat young market.
    """
    visible = [row for row in history if row["trade_date"] <= cutoff]
    returns = [row["price_after_minor"] / row["price_before_minor"] - 1 for row in visible]
    window = settings["volatility_sessions"]
    padded = ([0.0] * window + returns)[-window:]
    volatility = max(settings["volatility_floor"], min(1.0, statistics.stdev(padded)))
    momentum = sum(padded[-settings["momentum_sessions"]:])
    return {"market_signal": math.tanh(momentum / (volatility * math.sqrt(settings["momentum_sessions"]))),
            "estimated_volatility": volatility,
            "visible_closes": len(visible), "latest_close_date": visible[-1]["trade_date"] if visible else None,
            "window_return_sha256": canonical_hash(padded), "warmup_missing": max(0, window - len(visible))}


def simulate_feedback(steps: list[dict], role_agents: list[AgentParameters], scenario: dict,
                      venue_settings: dict, feedback_settings: dict, use_text: bool) -> dict:
    _check_steps(steps)
    _validate_venue(venue_settings)
    validate_feedback(feedback_settings)
    validate_scenario(scenario, venue_settings["lot_size"])
    if type(use_text) is not bool:
        raise ValueError("text ablation must be boolean")
    cohort = participants(role_agents, scenario)
    accounts, initial_wealth, states = {}, {}, {}
    for agent, _ in cohort:
        cash = Decimal(str(agent.initial_cash)) * 100
        if cash != cash.to_integral_value():
            raise ValueError("initial cash must have at most two decimal places")
        shares = scenario["inventory"][int(agent.name.rsplit("_", 1)[1])]
        accounts[agent.name] = AuctionAccount(int(cash), shares, shares)
        initial_wealth[agent.name] = int(cash) + shares * venue_settings["price_start_minor"]
        states[agent.name] = AgentState(agent.initial_cash, float(shares))
    venue = CallAuction(accounts, venue_settings["price_start_minor"], venue_settings["lot_size"],
                        venue_settings["tick_minor"], venue_settings["fee_bps"], venue_settings["price_band_bps"])
    peaks = dict(initial_wealth)
    drawdowns = {name: 0.0 for name in accounts}
    breaches, trade_counts, fees = [{name: 0 for name in accounts} for _ in range(3)]
    trace, history = [], []
    for index, step in enumerate(steps):
        if not step.get("execution_reference_date") or not step["signal_cutoff_date"] < step["execution_reference_date"] < step["trade_date"]:
            raise ValueError("feedback input cutoff, reference and trade dates must be ordered")
        feedback = (price_observation(history, step["signal_cutoff_date"], feedback_settings)
                    if scenario["feedback_mode"] == "endogenous" else
                    {"market_signal": step["market_signal"], "estimated_volatility": step["estimated_volatility"]})
        observations, decisions, orders, quotes = {}, {}, [], {}
        lower, upper = venue.price_bounds()
        order = arrival_order(list(accounts), scenario["order_mode"], scenario["seed"], index)
        sequence = {name: position for position, name in enumerate(order)}
        for agent, profile in cohort:
            account, state = venue.accounts[agent.name], states[agent.name]
            state.cash, state.shares = account.cash_minor / 100, float(account.shares)
            text, uncertainty = (step["text_signal"], step["text_uncertainty"]) if use_text else (0.0, 0.0)
            signal = max(-1.0, min(1.0, feedback["market_signal"] * profile["momentum_loading"] + profile["market_bias"]))
            market_evidence = (f"benchmark-through:{step['signal_cutoff_date']}" if scenario["feedback_mode"] == "conditioned"
                               else f"own-price-through:{step['signal_cutoff_date']}:{feedback['window_return_sha256']}")
            observation = Observation(index, venue.price_minor / 100, feedback["estimated_volatility"],
                                      signal, text, uncertainty, market_evidence, step["text_evidence"] if use_text else "text:disabled")
            observations[agent.name] = asdict(observation)
            decision = decide(agent, state, observation, use_text=use_text)
            decisions[agent.name] = asdict(decision)
            belief = agent.market_sensitivity * signal + agent.text_sensitivity * text - agent.uncertainty_aversion * uncertainty
            quantity = math.floor(abs(decision.requested_shares) / venue.lot_size) * venue.lot_size
            if not quantity:
                continue
            side = "buy" if decision.requested_shares > 0 else "sell"
            spread_sign = -1 if side == "buy" else 1
            reservation = Decimal(venue.price_minor) * (1 + Decimal(str(belief)) * venue_settings["quote_response_bps"] / 10000
                          + Decimal(spread_sign * venue_settings["quote_spread_bps"]) / 20000)
            rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
            quote = int((reservation / venue.tick_minor).to_integral_value(rounding=rounding)) * venue.tick_minor
            quote = max(lower, min(upper, quote))
            quotes[agent.name] = {"belief": belief, "limit_price_minor": quote}
            orders.append(LimitOrder(f"session-{index}:{agent.name}", agent.name, side, quantity, quote, sequence[agent.name]))
        cleared = venue.clear(index, orders, step.get("execution_available", True))
        for item in cleared["orders"]:
            trade_counts[item["owner"]] += int(item["filled_quantity"] > 0)
            fees[item["owner"]] += item["fee_minor"]
        for agent, _ in cohort:
            account = venue.accounts[agent.name]
            wealth = account.cash_minor + account.shares * venue.price_minor
            peaks[agent.name] = max(peaks[agent.name], wealth)
            drawdowns[agent.name] = max(drawdowns[agent.name], 1 - wealth / peaks[agent.name])
            breaches[agent.name] += int(account.shares * venue.price_minor / wealth > decisions[agent.name]["risk_weight_cap"] + 1e-9)
        history.append({"trade_date": step["trade_date"], "price_before_minor": cleared["price_before_minor"],
                        "price_after_minor": cleared["price_after_minor"]})
        trace.append({"trade_date": step["trade_date"], "signal_cutoff_date": step["signal_cutoff_date"],
                      "execution_reference_date": step["execution_reference_date"],
                      "market_signal": feedback["market_signal"], "estimated_volatility": feedback["estimated_volatility"],
                      "text_signal_used": step["text_signal"] if use_text else 0.0,
                      "text_uncertainty_used": step["text_uncertainty"] if use_text else 0.0,
                      "text_evidence": step["text_evidence"] if use_text else "text:disabled",
                      "feedback": feedback, "observations": observations, "arrival_order": order,
                      "decisions": decisions, "quotes": quotes, "auction": cleared})
    accepted = sum(row["accepted_quantity"] for day in trace for row in day["auction"]["orders"])
    requested = sum(row["quantity"] for day in trace for row in day["auction"]["orders"])
    matched = sum(day["auction"]["matched_volume"] for day in trace)
    agents = {agent.name: {"role": agent.role, "initial_wealth_minor": initial_wealth[agent.name],
              **asdict(venue.accounts[agent.name]),
              "final_wealth_minor": venue.accounts[agent.name].cash_minor + venue.accounts[agent.name].shares * venue.price_minor,
              "wealth_multiple": (venue.accounts[agent.name].cash_minor + venue.accounts[agent.name].shares * venue.price_minor) / initial_wealth[agent.name],
              "trades": trade_counts[agent.name], "fees_minor": fees[agent.name],
              "max_drawdown": drawdowns[agent.name], "risk_breach_sessions": breaches[agent.name]} for agent, _ in cohort}
    summary = {"sessions": len(steps), "agents": len(cohort), "use_text": use_text,
               "quote_response_bps": venue_settings["quote_response_bps"], "final_price_minor": venue.price_minor,
               "initial_cash_minor": venue.initial_cash_minor, "initial_shares": venue.initial_shares,
               "final_cash_minor": sum(account.cash_minor for account in venue.accounts.values()), "fee_pool_minor": venue.fee_pool_minor,
               "matched_volume": matched, "trade_sessions": sum(day["auction"]["matched_volume"] > 0 for day in trace),
               "price_change_sessions": sum(day["auction"]["price_before_minor"] != day["auction"]["price_after_minor"] for day in trace),
               "blocked_sessions": sum(not day["auction"]["execution_available"] for day in trace),
               "accepted_quantity": accepted, "requested_quantity": requested,
               "accepted_fill_fraction": 2 * matched / accepted if accepted else 0.0,
               "request_fill_fraction": 2 * matched / requested if requested else 0.0,
               "unfilled_quantity": accepted - 2 * matched, "trace_sha256": canonical_hash(trace), "agent_summary": agents,
               "role_wealth_multiple": {role: sum(a["final_wealth_minor"] for a in agents.values() if a["role"] == role)
                                       / sum(a["initial_wealth_minor"] for a in agents.values() if a["role"] == role) for role in ROLES}}
    return {"summary": summary, "trace": trace,
            "participants": {agent.name: {"parameters": asdict(agent), "profile": profile} for agent, profile in cohort}}
