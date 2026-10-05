"""Synthetic issuer valuation messages with lagged risk and private receipt masks.

These are declared mechanism hypotheses, never measured company news or target
returns. Only a recipient's current message is added to its decision input.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .agents import finite_range


PARAMETERS = {"history_window", "risk_multiplier", "belief_scale_bps", "max_shift_bps",
              "information_probability_bps", "innovation_seed", "information_seed"}
RISK_FIELDS = {"trade_date", "signal_cutoff_date", "history", "history_sha256", "lagged_stock_volatility"}
MESSAGE_FIELDS = RISK_FIELDS | {"innovation_sha256", "standardized_innovation", "raw_shift_bps",
                               "valuation_shift_bps", "was_capped"}


def history_hash(history: list[dict]) -> str:
    data = json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def validate_parameters(parameters: dict) -> None:
    if not isinstance(parameters, dict) or set(parameters) != PARAMETERS:
        raise ValueError("issuer valuation requires explicit parameters")
    for key, lower, upper in (("history_window", 2, 252), ("belief_scale_bps", 1000, 10000),
                              ("max_shift_bps", 1, 500), ("information_probability_bps", 0, 10000)):
        if type(parameters[key]) is not int or not lower <= parameters[key] <= upper:
            raise ValueError(f"invalid issuer valuation {key}")
    finite_range(parameters["risk_multiplier"], 0.0, 2.0, "issuer risk multiplier")
    if any(not isinstance(parameters[key], str) or not parameters[key] for key in ("innovation_seed", "information_seed")):
        raise ValueError("issuer innovation and information seeds must be explicit")


def validate_risk_row(row: dict, day: str, cutoff: str, window: int) -> None:
    history = row["history"]
    if row["trade_date"] != day or row["signal_cutoff_date"] != cutoff or not cutoff < day:
        raise ValueError("issuer risk row differs from the decision clock")
    if not isinstance(history, list) or len(history) != window:
        raise ValueError("issuer valuation requires complete lagged history")
    dates, returns = [], []
    for item in history:
        if not isinstance(item, dict) or set(item) != {"trade_date", "stock_return"}:
            raise ValueError("issuer history permits dates and lagged returns only")
        value = item["trade_date"]
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value or value > cutoff:
            raise ValueError("issuer history contains a future or invalid date")
        finite_range(item["stock_return"], -1.0, 10.0, "lagged stock return")
        dates.append(value)
        returns.append(item["stock_return"])
    if dates != sorted(set(dates)) or dates[-1] != cutoff:
        raise ValueError("issuer history must end exactly at the information cutoff")
    if row["history_sha256"] != history_hash(history):
        raise ValueError("issuer history hash differs")
    sigma = statistics.stdev(returns)
    if not math.isfinite(row["lagged_stock_volatility"]) or row["lagged_stock_volatility"] != sigma:
        raise ValueError("issuer lagged risk differs from its complete history")


def build_lagged_risk(groups: dict, joined: dict, window: int = 20) -> dict:
    """Whitelist history through each supplied cutoff; never copy target fields."""
    if type(window) is not int or not 2 <= window <= 252 or not joined:
        raise ValueError("issuer risk panel requires stocks and a valid history window")
    result = {}
    for stock, steps in sorted(joined.items()):
        series = sorted(groups[stock], key=lambda row: row.trade_date)
        index = {row.trade_date.isoformat(): i for i, row in enumerate(series)}
        if len(index) != len(series):
            raise ValueError("issuer risk source contains duplicate market dates")
        rows = []
        for step in steps:
            cutoff = step["signal_cutoff_date"]
            end = index.get(cutoff)
            if end is None or end + 1 < window:
                raise ValueError("issuer risk source lacks the lagged window")
            history = [{"trade_date": row.trade_date.isoformat(), "stock_return": row.stock_return}
                       for row in series[end + 1 - window:end + 1]]
            risk = {"trade_date": step["trade_date"], "signal_cutoff_date": cutoff, "history": history,
                    "history_sha256": history_hash(history), "lagged_stock_volatility": statistics.stdev(row["stock_return"] for row in history)}
            validate_risk_row(risk, step["trade_date"], cutoff, window)
            rows.append(risk)
        result[stock] = rows
    return result


def build_issuer_valuation(risk: dict, scenario_id: str, parameters: dict) -> dict:
    validate_parameters(parameters)
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("issuer valuation scenario id must be explicit")
    paths = {}
    for stock, rows in sorted(risk.items()):
        messages = []
        for row in rows:
            if set(row) != RISK_FIELDS:
                raise ValueError("issuer risk input contains undeclared or target fields")
            validate_risk_row(row, row["trade_date"], row["signal_cutoff_date"], parameters["history_window"])
            digest = hashlib.sha256(f"issuer-innovation-v1:{parameters['innovation_seed']}:{stock}:{row['trade_date']}".encode()).hexdigest()
            uniform = (int(digest[:16], 16) + 0.5) / 2**64
            innovation = math.sqrt(3) * (2 * uniform - 1)
            raw = innovation * parameters["risk_multiplier"] * row["lagged_stock_volatility"] * 10000
            cap = parameters["max_shift_bps"]
            shift = max(-cap, min(cap, raw))
            messages.append({**row, "innovation_sha256": digest, "standardized_innovation": innovation,
                             "raw_shift_bps": raw, "valuation_shift_bps": shift, "was_capped": raw != shift})
        paths[stock] = messages
    return {"scenario_id": scenario_id, "parameters": dict(parameters), "by_stock": paths}


def validate_issuer_valuation(assets: list[str], calendar: list[tuple], path: dict) -> dict:
    if (not isinstance(path, dict) or set(path) != {"scenario_id", "parameters", "by_stock"}
            or not isinstance(path["scenario_id"], str) or not path["scenario_id"]
            or not isinstance(path["by_stock"], dict) or set(path["by_stock"]) != set(assets)):
        raise ValueError("issuer valuation path coverage or contract differs")
    validate_parameters(path["parameters"])
    risk = {}
    for stock in assets:
        rows = path["by_stock"][stock]
        if not isinstance(rows, list) or len(rows) != len(calendar):
            raise ValueError("issuer valuation session coverage differs")
        risk[stock] = []
        for row, (day, cutoff, _) in zip(rows, calendar, strict=True):
            if not isinstance(row, dict) or set(row) != MESSAGE_FIELDS:
                raise ValueError("issuer valuation message contains undeclared fields")
            validate_risk_row(row, day, cutoff, path["parameters"]["history_window"])
            if type(row["was_capped"]) is not bool:
                raise ValueError("issuer cap flag must be a boolean")
            risk[stock].append({key: row[key] for key in RISK_FIELDS})
    rebuilt = build_issuer_valuation(risk, path["scenario_id"], path["parameters"])
    if path != rebuilt:
        raise ValueError("issuer message differs from its declared innovation and lagged risk")
    return path


def subset_issuer_valuation(path: dict, assets: list[str]) -> dict:
    return {"scenario_id": path["scenario_id"], "parameters": dict(path["parameters"]),
            "by_stock": {stock: path["by_stock"][stock] for stock in assets}}


def message_receipt(row: dict, parameters: dict, stock: str, owner: str) -> dict:
    digest = hashlib.sha256(f"issuer-information-v1:{parameters['information_seed']}:{stock}:{row['trade_date']}:{owner}".encode()).hexdigest()
    probability = parameters["information_probability_bps"]
    received = int(digest[:16], 16) * 10000 // 2**64 < probability
    shift = row["valuation_shift_bps"] if received else 0.0
    return {"received": received, "receipt_sha256": digest, "information_probability_bps": probability,
            "valuation_shift_bps": shift if received else None,
            "applied_valuation_shift_bps": shift, "applied_belief_signal": shift / parameters["belief_scale_bps"]}


def strategy_quote_terms(agent, profile: dict, observation: dict, receipt: dict, belief: float, use_text: bool) -> dict:
    shared = observation["market_signal"] * profile["momentum_loading"] + profile["market_bias"] + observation.get("scenario_shock", 0.0)
    baseline = agent.market_sensitivity * max(-1.0, min(1.0, shared))
    if use_text:
        baseline += agent.text_sensitivity * observation["text_signal"] - agent.uncertainty_aversion * observation["text_uncertainty"]
    return {"base_belief": baseline, "belief_contribution": belief - baseline,
            "quote_shift_bps": agent.market_sensitivity * receipt["applied_valuation_shift_bps"]}


def respond_issuer_background(demand: dict, receipt: dict, price: int, bounds: tuple[int, int],
                              venue: dict, background: dict, shared_shock: float, response: dict | None) -> dict:
    # Recompute from the unrounded reservation, not an already rounded quote.
    # Participation, target inventory, side and order size are inherited intact.
    result = {**demand, "issuer_valuation_response": {"receipt": dict(receipt),
                                                     "base_limit_price_minor": demand["limit_price_minor"]}}
    if not demand["requested_quantity"] or not receipt["received"]:
        return result
    shift = Decimal(str(shared_shock)) * response["valuation_response_bps"] if response else Decimal(0)
    shift += Decimal(str(receipt["applied_valuation_shift_bps"]))
    side = demand["side"]
    urgency = background["urgency_bps"] if side == "buy" else -background["urgency_bps"]
    units = Decimal(price) * (1 + (shift + urgency) / 10000) / venue["tick_minor"]
    rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
    quote = int(units.to_integral_value(rounding=rounding)) * venue["tick_minor"]
    result["limit_price_minor"] = max(bounds[0], min(bounds[1], quote))
    return result
