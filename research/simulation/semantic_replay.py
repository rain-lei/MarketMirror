"""Run an Agent path with a verified, as-of semantic signal stream.

This module is an isolated successor to the existing market-only replay.  It
accepts only joined steps carrying the passed semantic gate and keeps the
original replay outputs unchanged.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from .agents import AgentParameters, AgentState, Observation, decide, finite_range
from .semantic_signal_join import (VERSION as JOIN_VERSION, join_steps,
                                   load_verified_signal_stream)

VERSION = "semantic-agent-replay-v1"


def _check_steps(steps: list[dict[str, Any]]) -> None:
    if not steps:
        raise ValueError("semantic replay requires nonempty steps")
    required = {"trade_date", "signal_cutoff_date", "observed_return", "market_signal",
                "estimated_volatility", "text_signal", "text_uncertainty", "text_evidence"}
    previous: date | None = None
    for step in steps:
        if not isinstance(step, dict) or not required <= set(step):
            raise ValueError("semantic replay steps lack required signal fields")
        try:
            trade_date = date.fromisoformat(step["trade_date"])
            cutoff = date.fromisoformat(step["signal_cutoff_date"])
        except (TypeError, ValueError) as exc:
            raise ValueError("semantic replay dates must be ISO dates") from exc
        if cutoff >= trade_date or (previous is not None and trade_date <= previous):
            raise ValueError("semantic replay dates or information cutoff are invalid")
        previous = trade_date
        for name in ("market_signal", "text_signal"):
            finite_range(step[name], -1.0, 1.0, name)
        finite_range(step["text_uncertainty"], 0.0, 1.0, "text_uncertainty")
        finite_range(step["estimated_volatility"], 1e-6, 1.0, "estimated_volatility")
        if (type(step["observed_return"]) not in (int, float)
                or not math.isfinite(step["observed_return"]) or step["observed_return"] <= -1):
            raise ValueError("observed_return must be finite and greater than -1")
        if not isinstance(step["text_evidence"], str) or not step["text_evidence"].strip():
            raise ValueError("semantic replay requires text evidence identifiers")
        if "execution_available" in step and type(step["execution_available"]) is not bool:
            raise ValueError("execution_available must be a boolean")


def replay_semantic_path(steps: list[dict[str, Any]], agents: list[AgentParameters],
                         fee_rate: float, use_text: bool = True) -> dict[str, Any]:
    """Replay a lagged market path with or without the verified text channel."""
    _check_steps(steps)
    if not agents:
        raise ValueError("semantic replay requires agents")
    finite_range(fee_rate, 0.0, 0.1, "fee_rate")
    price = 100.0
    states = {agent.name: AgentState(float(agent.initial_cash)) for agent in agents}
    peaks = {agent.name: agent.initial_cash for agent in agents}
    drawdowns = {agent.name: 0.0 for agent in agents}
    risk_breach_days = {agent.name: 0 for agent in agents}
    external_cash, external_shares, fee_pool = 0.0, 0.0, 0.0
    initial_cash = sum(agent.initial_cash for agent in agents)
    trace = []
    for index, step in enumerate(steps):
        next_price = price * (1 + step["observed_return"])
        if not math.isfinite(next_price) or next_price <= 0:
            raise ValueError("observed return path produced an invalid price index")
        decisions = {}
        for agent in agents:
            observation = Observation(
                index, price, step["estimated_volatility"], step["market_signal"],
                step["text_signal"] if use_text else 0.0,
                step["text_uncertainty"] if use_text else 0.0,
                f"benchmark-through:{step['signal_cutoff_date']}", step["text_evidence"])
            decisions[agent.name] = decide(agent, states[agent.name], observation, use_text=use_text)
        agent_rows = {}
        for agent in agents:
            state, decision = states[agent.name], decisions[agent.name]
            requested = decision.requested_shares
            fill = (min(requested, state.cash / (price * (1 + fee_rate)))
                    if requested > 0 else max(requested, -state.shares))
            if not step.get("execution_available", True):
                fill = 0.0
            notional = fill * price
            fee = abs(notional) * fee_rate
            state.cash -= notional + fee
            state.shares += fill
            if state.cash < -1e-8 or state.shares < -1e-8:
                raise AssertionError("semantic replay violated cash or long-only position")
            state.cash, state.shares = max(0.0, state.cash), max(0.0, state.shares)
            state.trades += int(abs(fill) > 1e-12)
            state.cumulative_turnover += abs(notional)
            state.cumulative_fees += fee
            external_cash += notional
            external_shares -= fill
            fee_pool += fee
            wealth = state.wealth(next_price)
            peaks[agent.name] = max(peaks[agent.name], wealth)
            drawdowns[agent.name] = max(drawdowns[agent.name], 1 - wealth / peaks[agent.name])
            weight = state.shares * next_price / wealth
            risk_breach_days[agent.name] += int(weight > decision.risk_weight_cap + 1e-9)
            agent_rows[agent.name] = {"role": agent.role, "decision": asdict(decision),
                                      "filled_shares": fill, "fees_paid": fee,
                                      "execution_available": step.get("execution_available", True),
                                      "cash": state.cash, "shares": state.shares,
                                      "closing_wealth": wealth, "closing_weight": weight}
        if not math.isclose(sum(state.cash for state in states.values()) + external_cash + fee_pool,
                            initial_cash, rel_tol=1e-12, abs_tol=1e-6):
            raise AssertionError("semantic replay cash ledger did not conserve")
        if not math.isclose(sum(state.shares for state in states.values()) + external_shares,
                            0.0, rel_tol=1e-12, abs_tol=1e-8):
            raise AssertionError("semantic replay shares ledger did not conserve")
        trace.append({**step, "step": index, "price_index_before": price,
                      "price_index_after": next_price, "external_cash": external_cash,
                      "external_shares": external_shares, "fee_pool": fee_pool,
                      "agents": agent_rows})
        price = next_price
    buy_hold = {agent.name: agent.initial_cash * price / (100.0 * (1 + fee_rate))
                for agent in agents}
    return {
        "pipeline_version": VERSION, "signal_enabled": use_text,
        "price_index_start": 100.0, "price_index_end": price,
        "trace": trace,
        "summary": {
            agent.name: {"role": agent.role, "final_wealth": states[agent.name].wealth(price),
                         "return": states[agent.name].wealth(price) / agent.initial_cash - 1,
                         "cash_reference_wealth": agent.initial_cash,
                         "buy_hold_reference_wealth": buy_hold[agent.name],
                         "trades": states[agent.name].trades,
                         "cumulative_turnover": states[agent.name].cumulative_turnover,
                         "cumulative_fees": states[agent.name].cumulative_fees,
                         "max_drawdown": drawdowns[agent.name],
                         "risk_budget_breach_days": risk_breach_days[agent.name]}
            for agent in agents},
    }


def replay_joined_payload(payload: dict[str, Any], agents: list[AgentParameters],
                          fee_rate: float, use_text: bool = True) -> dict[str, Any]:
    """Reject an unverified join payload before it can reach Agent decisions."""
    if payload.get("pipeline_version") != JOIN_VERSION:
        raise ValueError("semantic replay requires a signal-join payload")
    gate = payload.get("eligible_gate", {})
    if (gate.get("passed") is not True or not isinstance(gate.get("checks"), dict)
            or not gate["checks"] or any(value is not True for value in gate["checks"].values())):
        raise ValueError("semantic replay requires a passed Agent signal gate")
    return replay_semantic_path(payload.get("steps", []), agents, fee_rate, use_text)


def replay_signal_directory(steps: list[dict[str, Any]], signal_dir: Path,
                            stock_code: str, agents: list[AgentParameters],
                            fee_rate: float, use_text: bool = True) -> dict[str, Any]:
    """Verify the adapter directory, join it as-of, and then run the path."""
    rows, _ = load_verified_signal_stream(signal_dir)
    joined = join_steps(steps, stock_code, rows)
    return replay_semantic_path(joined, agents, fee_rate, use_text)
