"""Fixed-state previews from the platform's real portfolio decision rule."""
from __future__ import annotations

import copy
import math

from research.simulation.agents import AgentParameters
from research.simulation.portfolio_auction import PortfolioAccount
from research.simulation.portfolio_market import VERSION, portfolio_decision
from .strategy_config import load_model, parameters_digest, validate_parameters

ASSETS = ("A", "B", "C")
HISTORY = {
    "first": {"session": 0, "sign": 0, "streak": 0},
    "positive": {"session": 3, "sign": 1, "streak": 2},
    "negative": {"session": 3, "sign": -1, "streak": 2},
}


def validate_scenario(value: object) -> dict:
    fields = {"signal", "uncertainty", "market", "volatility", "scope", "history"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("预览须包含完整的情景与观察状态")
    result = {}
    for name, low, high in (("signal", -1., 1.), ("uncertainty", 0., 1.),
                            ("market", -1., 1.), ("volatility", .001, .5)):
        number = value[name]
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or not low <= number <= high):
            raise ValueError(f"{name} 须为 {low}–{high} 的有限数值")
        result[name] = float(number)
    if not isinstance(value["scope"], str) or value["scope"] not in {"A", "public"}:
        raise ValueError("消息范围仅支持资产 A 或全部资产")
    if not isinstance(value["history"], str) or value["history"] not in HISTORY:
        raise ValueError("观察状态不受支持")
    result.update(scope=value["scope"], history=value["history"])
    return result


def preview_decisions(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"parameters", "scenario"}:
        raise ValueError("仅接受完整策略参数与预览情景")
    model, model_hash = load_model()
    parameters = validate_parameters(value["parameters"], model)
    scenario = validate_scenario(value["scenario"])
    # Every role and condition receive independent copies of exactly these
    # resources, prices, covariance and BEFORE-decision memory.
    account = PortfolioAccount({"shared": 85_000_000}, dict.fromkeys(ASSETS, 500),
                               dict.fromkeys(ASSETS, 500))
    prices = dict.fromkeys(ASSETS, 10_000)
    cov = {a: {b: scenario["volatility"] ** 2 if a == b else 0. for b in ASSETS} for a in ASSETS}
    observations = {a: {"market_signal": scenario["market"],
                        "text_signal": scenario["signal"] if scenario["scope"] == "public" or a == "A" else 0.,
                        "text_uncertainty": scenario["uncertainty"] if scenario["scope"] == "public" or a == "A" else 0.}
                    for a in ASSETS}
    history = HISTORY[scenario["history"]]
    memory = {"sign": history["sign"], "streak": history["streak"]}
    profile = {"momentum_loading": 1., "market_bias": 0.}
    roles = {}
    for spec in model["agent_parameters"]:
        role = spec["role"]
        agent = AgentParameters(**{**spec, **parameters[role]})
        pair = {}
        for name, use_text in (("baseline", False), ("with_message", True)):
            state = copy.deepcopy(memory)
            decision = portfolio_decision(agent, profile, copy.deepcopy(account), prices,
                                          observations, cov, model["portfolio_case"],
                                          state, history["session"], use_text)
            terms = {a: {"market": agent.market_sensitivity * observations[a]["market_signal"],
                         "text": agent.text_sensitivity * observations[a]["text_signal"] if use_text else 0.,
                         "uncertainty": -agent.uncertainty_aversion * observations[a]["text_uncertainty"] if use_text else 0.}
                     for a in ASSETS}
            pair[name] = {**decision, "terms": terms, "memory_after": state,
                          "total_target_weight": sum(decision["desired_weights"].values()),
                          "planned_turnover": sum(abs(d) for d in decision["order_weight_changes"].values())}
        roles[role] = pair
    return {"mode": "strategy_decision_preview", "schema_version": "fixed-state-decision-v1",
            "input": {"parameters": parameters, "scenario": scenario}, "roles": roles,
            "engine_version": VERSION, "mechanism_config_sha256": model_hash,
            "strategy_parameters_sha256": parameters_digest(parameters),
            "state_before": {"cash_minor": sum(account.wallets.values()), "nav_minor": 100_000_000,
                             "shares": account.shares, "prices_minor": prices, "memory": memory,
                             "decision_step": history["session"] + 1, "covariance": cov},
            "assumptions": {"synthetic_inputs": True, "identical_state_before": True,
                            "assets_correlated": False, "orders_submitted": False,
                            "fills_simulated": False, "llm_called": False}}
