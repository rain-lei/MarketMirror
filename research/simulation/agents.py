"""Common long-only decision rule with explicit role parameters and state."""

from __future__ import annotations

import math
from dataclasses import dataclass


def finite_range(value: float, lower: float, upper: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} must be finite and between {lower} and {upper}")


@dataclass(frozen=True)
class AgentParameters:
    name: str
    role: str
    initial_cash: float
    base_weight: float
    max_weight: float
    max_turnover: float
    risk_budget: float
    market_sensitivity: float
    text_sensitivity: float
    uncertainty_aversion: float
    confirmation_steps: int
    rebalance_interval: int
    min_trade_weight: float

    def __post_init__(self) -> None:
        if not self.name or self.role not in {"aggressive", "conservative", "institutional"}:
            raise ValueError("agent needs name and supported role")
        for field in ("initial_cash", "base_weight", "max_weight", "max_turnover", "risk_budget",
                      "market_sensitivity", "text_sensitivity", "uncertainty_aversion", "min_trade_weight"):
            value = getattr(self, field)
            if field == "initial_cash":
                finite_range(value, 1.0, 1e15, field)
            elif field in {"risk_budget", "market_sensitivity", "text_sensitivity", "uncertainty_aversion"}:
                finite_range(value, 0.0, 10.0, field)
            else:
                finite_range(value, 0.0, 1.0, field)
        if self.base_weight > self.max_weight or self.min_trade_weight > self.max_turnover:
            raise ValueError("base/minimum trade exceeds weight/turnover limits")
        for field in ("confirmation_steps", "rebalance_interval"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")


@dataclass
class AgentState:
    cash: float
    shares: float = 0.0
    target_weight: float = 0.0
    latest_action: str = "hold"
    signal_sign: int = 0
    signal_streak: int = 0
    trades: int = 0
    cumulative_turnover: float = 0.0
    cumulative_fees: float = 0.0

    def wealth(self, price: float) -> float:
        if price <= 0 or not math.isfinite(price):
            raise ValueError("price must be finite and positive")
        value = self.cash + self.shares * price
        if not math.isfinite(value) or value <= 0:
            raise ValueError("agent wealth must stay finite and positive")
        return value


@dataclass(frozen=True)
class Observation:
    step: int
    price: float
    estimated_volatility: float
    market_signal: float
    text_signal: float
    uncertainty: float
    market_evidence: str
    text_evidence: str

    def __post_init__(self) -> None:
        if isinstance(self.step, bool) or not isinstance(self.step, int) or self.step < 0:
            raise ValueError("step must be nonnegative")
        finite_range(self.price, 1e-8, 1e15, "price")
        finite_range(self.estimated_volatility, 1e-6, 1.0, "estimated_volatility")
        for name in ("market_signal", "text_signal"):
            finite_range(getattr(self, name), -1.0, 1.0, name)
        finite_range(self.uncertainty, 0.0, 1.0, "uncertainty")
        if not self.market_evidence or not self.text_evidence:
            raise ValueError("signal provenance identifiers are required")


@dataclass(frozen=True)
class Decision:
    action: str
    target_weight: float
    requested_shares: float
    confidence: float
    reason_codes: tuple[str, ...]
    evidence: tuple[str, ...]
    current_weight: float
    risk_weight_cap: float


def decide(parameters: AgentParameters, state: AgentState, observation: Observation,
           use_text: bool = True) -> Decision:
    """No future prices, targets or event outcomes enter this rule."""
    wealth = state.wealth(observation.price)
    current_weight = state.shares * observation.price / wealth
    signal = (parameters.market_sensitivity * observation.market_signal
              + (parameters.text_sensitivity * observation.text_signal if use_text else 0.0)
              - parameters.uncertainty_aversion * observation.uncertainty)
    sign = (signal > 1e-12) - (signal < -1e-12)
    state.signal_streak = state.signal_streak + 1 if sign != 0 and sign == state.signal_sign else (1 if sign else 0)
    state.signal_sign = sign
    risk_cap = min(parameters.max_weight, parameters.risk_budget / observation.estimated_volatility)
    target = min(risk_cap, max(0.0, parameters.base_weight + 0.4 * signal))
    reasons = ["market_signal"]
    evidence = [observation.market_evidence]
    if use_text and parameters.text_sensitivity and observation.text_signal:
        reasons.append("text_signal")
        evidence.append(observation.text_evidence)
    if observation.uncertainty:
        reasons.append("uncertainty_stress")
    if target >= risk_cap - 1e-12:
        reasons.append("risk_budget_cap")
    risk_liquidation = current_weight > risk_cap + 1e-12
    if risk_liquidation:
        reasons.append("risk_liquidation_priority")
    if state.signal_streak < parameters.confirmation_steps and sign and not risk_liquidation:
        reasons.append("confirmation_wait")
        target = current_weight
    if observation.step % parameters.rebalance_interval and not risk_liquidation:
        reasons.append("rebalance_wait")
        target = current_weight
    delta = target - current_weight
    if abs(delta) < parameters.min_trade_weight and not risk_liquidation:
        reasons.append("minimum_trade")
        target = current_weight
        delta = 0.0
    if risk_liquidation and -delta > parameters.max_turnover:
        reasons.append("risk_liquidation_overrides_turnover")
    clipped = min(parameters.max_turnover, delta) if delta > 0 else (delta if risk_liquidation else max(-parameters.max_turnover, delta))
    requested = clipped * wealth / observation.price
    requested = max(-state.shares, requested)
    action = "buy" if requested > 1e-12 else "sell" if requested < -1e-12 else "hold"
    state.target_weight = target
    state.latest_action = action
    return Decision(action, target, requested, min(1.0, abs(signal)), tuple(reasons), tuple(evidence),
                    current_weight, risk_cap)
