"""Causal finite-participant market with resource-bounded inventory demand."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .agents import AgentState, Observation, decide, finite_range
from .call_auction import AuctionAccount, CallAuction, LimitOrder
from .feedback_auction import participants, arrival_order, price_observation, validate_feedback, validate_scenario
from .semantic_auction_experiment import _validate_venue
from .semantic_memory_sensitivity import canonical_hash
from .semantic_replay import _check_steps

VERSION = "finite-background-participant-market-v1"
BG_FIELDS = {"case_id", "mode", "participants", "initial_cash", "initial_shares", "target_range_lots",
             "max_order_lots", "urgency_bps", "seed"}


def validate_background(background: dict, lot: int) -> None:
    if not isinstance(background, dict) or set(background) != BG_FIELDS:
        raise ValueError("background case requires explicit resources and demand parameters")
    if not isinstance(background["case_id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", background["case_id"]):
        raise ValueError("invalid background case identifier")
    if background["mode"] not in {"none", "idle", "active"}:
        raise ValueError("unsupported background mode")
    for field, upper in (("participants", 100), ("initial_shares", 10**8), ("target_range_lots", 100),
                         ("max_order_lots", 100), ("urgency_bps", 1000), ("seed", 2**32 - 1)):
        if type(background[field]) is not int or not 0 <= background[field] <= upper:
            raise ValueError(f"background {field} must be a bounded nonnegative integer")
    finite_range(background["initial_cash"], 0.0, 1e9, "background initial cash")
    if Decimal(str(background["initial_cash"])) * 100 != (Decimal(str(background["initial_cash"])) * 100).to_integral_value():
        raise ValueError("background cash must have at most two decimal places")
    if background["mode"] == "none":
        if any(background[field] for field in BG_FIELDS - {"case_id", "mode"}):
            raise ValueError("no-background control must declare zero resources and parameters")
    elif (background["participants"] < 2 or background["initial_cash"] < 1 or background["initial_shares"] < lot
          or background["initial_shares"] % lot or background["max_order_lots"] < 1
          or background["target_range_lots"] < 1 or background["urgency_bps"] < 1):
        raise ValueError("background participants require finite cash, whole-lot shares and explicit demand")


def initial_accounts(role_agents, scenario: dict, background: dict, price: int):
    cohort = participants(role_agents, scenario)
    accounts, specs = {}, {}
    for agent, profile in cohort:
        cash = Decimal(str(agent.initial_cash)) * 100
        if cash != cash.to_integral_value():
            raise ValueError("strategy cash must have at most two decimal places")
        shares = scenario["inventory"][int(agent.name.rsplit("_", 1)[1])]
        accounts[agent.name] = AuctionAccount(int(cash), shares, shares)
        specs[agent.name] = {"kind": "strategy", "parameters": asdict(agent), "profile": profile}
    for index in range(background["participants"]):
        name = f"background_{index:03d}"
        shares = background["initial_shares"]
        accounts[name] = AuctionAccount(int(Decimal(str(background["initial_cash"])) * 100), shares, shares)
        specs[name] = {"kind": "background", "initial_cash": background["initial_cash"], "initial_shares": shares}
    wealth = {name: a.cash_minor + a.shares * price for name, a in accounts.items()}
    return cohort, accounts, specs, wealth


def background_demand(stock: str, session: int, name: str, account: AuctionAccount,
                      price: int, bounds: tuple[int, int], venue_settings: dict, settings: dict) -> dict:
    """Private inventory-target shocks; identical draws in text/no-text paths.

    No scenario ID, text flag, observed return or future data enters the draw.
    A common 64-bit uniform rank also couples different target-range settings.
    """
    if settings["mode"] != "active":
        return {"mode": "idle", "current_shares": account.shares, "target_shares": account.shares,
                "requested_quantity": 0, "side": "hold", "limit_price_minor": None, "shock_sha256": None}
    digest = hashlib.sha256(f"{settings['seed']}:{stock}:{session}:{name}".encode()).hexdigest()
    width = settings["target_range_lots"]
    bucket = int(digest[:16], 16) * (2 * width + 1) // 2**64 - width
    target = max(0, settings["initial_shares"] + bucket * venue_settings["lot_size"])
    delta = target - account.shares
    quantity = min(abs(delta), settings["max_order_lots"] * venue_settings["lot_size"])
    side = "buy" if delta > 0 else "sell" if delta < 0 else "hold"
    quote = None
    if quantity:
        sign = 1 if side == "buy" else -1
        reservation = Decimal(price) * (1 + Decimal(sign * settings["urgency_bps"]) / 10000)
        rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
        quote = int((reservation / venue_settings["tick_minor"]).to_integral_value(rounding=rounding)) * venue_settings["tick_minor"]
        quote = max(bounds[0], min(bounds[1], quote))
    return {"mode": "inventory_target", "current_shares": account.shares, "target_shares": target,
            "target_offset_lots": bucket, "requested_quantity": quantity, "side": side,
            "limit_price_minor": quote, "shock_sha256": digest}


def strategy_decision(agent, profile, state, step, feedback, session, venue, settings, use_text):
    account = venue.accounts[agent.name]
    state.cash, state.shares = account.cash_minor / 100, float(account.shares)
    text, uncertainty = (step["text_signal"], step["text_uncertainty"]) if use_text else (0.0, 0.0)
    signal = max(-1.0, min(1.0, feedback["market_signal"] * profile["momentum_loading"] + profile["market_bias"]))
    evidence = f"own-price-through:{step['signal_cutoff_date']}:{feedback['window_return_sha256']}"
    observation = Observation(session, venue.price_minor / 100, feedback["estimated_volatility"], signal, text,
                              uncertainty, evidence, step["text_evidence"] if use_text else "text:disabled")
    decision = decide(agent, state, observation, use_text=use_text)
    belief = agent.market_sensitivity * signal + agent.text_sensitivity * text - agent.uncertainty_aversion * uncertainty
    quantity = math.floor(abs(decision.requested_shares) / venue.lot_size) * venue.lot_size
    quote = None
    if quantity:
        side = "buy" if decision.requested_shares > 0 else "sell"
        sign = -1 if side == "buy" else 1
        reservation = Decimal(venue.price_minor) * (1 + Decimal(str(belief)) * settings["quote_response_bps"] / 10000
                      + Decimal(sign * settings["quote_spread_bps"]) / 20000)
        rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
        quote = int((reservation / venue.tick_minor).to_integral_value(rounding=rounding)) * venue.tick_minor
        bounds = venue.price_bounds()
        quote = max(bounds[0], min(bounds[1], quote))
    return asdict(observation), asdict(decision), quantity, quote, belief


def simulate_market(steps, role_agents, scenario, venue_settings, feedback_settings, background, stock, use_text):
    _check_steps(steps)
    _validate_venue(venue_settings)
    validate_feedback(feedback_settings)
    validate_scenario(scenario, venue_settings["lot_size"])
    validate_background(background, venue_settings["lot_size"])
    if scenario["feedback_mode"] != "endogenous" or type(use_text) is not bool or not re.fullmatch(r"[0-9]{6}", stock):
        raise ValueError("participant market requires own-price feedback, explicit ablation and stock identity")
    cohort, accounts, specs, initial_wealth = initial_accounts(role_agents, scenario, background, venue_settings["price_start_minor"])
    venue = CallAuction(accounts, venue_settings["price_start_minor"], venue_settings["lot_size"], venue_settings["tick_minor"],
                        venue_settings["fee_bps"], venue_settings["price_band_bps"])
    states = {agent.name: AgentState(agent.initial_cash) for agent, _ in cohort}
    totals = {name: dict(peak=value, max_drawdown=0.0, trades=0, fees_minor=0, risk_breach_sessions=0) for name, value in initial_wealth.items()}
    trace, history = [], []
    categories = {"strategy_strategy": 0, "strategy_background": 0, "background_background": 0}
    for session, step in enumerate(steps):
        if not step.get("execution_reference_date") or not step["signal_cutoff_date"] < step["execution_reference_date"] < step["trade_date"]:
            raise ValueError("market cutoff, reference and trade dates must be ordered")
        feedback = price_observation(history, step["signal_cutoff_date"], feedback_settings)
        observations, decisions, quotes, demands, orders = {}, {}, {}, {}, []
        order = arrival_order(list(accounts), scenario["order_mode"], scenario["seed"], session)
        sequence = {name: index for index, name in enumerate(order)}
        for agent, profile in cohort:
            observation, decision, quantity, quote, belief = strategy_decision(agent, profile, states[agent.name], step, feedback,
                                                                            session, venue, venue_settings, use_text)
            observations[agent.name], decisions[agent.name] = observation, decision
            if quantity:
                side = "buy" if decision["requested_shares"] > 0 else "sell"
                quotes[agent.name] = {"belief": belief, "limit_price_minor": quote}
                orders.append(LimitOrder(f"session-{session}:{agent.name}", agent.name, side, quantity, quote, sequence[agent.name]))
        for name, spec in specs.items():
            if spec["kind"] != "background":
                continue
            demand = background_demand(stock, session, name, venue.accounts[name], venue.price_minor, venue.price_bounds(), venue_settings, background)
            demands[name] = demand
            if demand["requested_quantity"]:
                orders.append(LimitOrder(f"session-{session}:{name}", name, demand["side"], demand["requested_quantity"], demand["limit_price_minor"], sequence[name]))
        cleared = venue.clear(session, orders, step.get("execution_available", True))
        for trade in cleared["trades"]:
            kinds = {specs[trade["buyer"]]["kind"], specs[trade["seller"]]["kind"]}
            category = "strategy_background" if len(kinds) == 2 else "strategy_strategy" if kinds == {"strategy"} else "background_background"
            categories[category] += trade["quantity"]
        for item in cleared["orders"]:
            totals[item["owner"]]["trades"] += int(item["filled_quantity"] > 0)
            totals[item["owner"]]["fees_minor"] += item["fee_minor"]
        for name, account in venue.accounts.items():
            value = account.cash_minor + account.shares * venue.price_minor
            totals[name]["peak"] = max(totals[name]["peak"], value)
            totals[name]["max_drawdown"] = max(totals[name]["max_drawdown"], 1 - value / totals[name]["peak"])
            if name in decisions:
                totals[name]["risk_breach_sessions"] += int(account.shares * venue.price_minor / value > decisions[name]["risk_weight_cap"] + 1e-9)
        history.append({"trade_date": step["trade_date"], "price_before_minor": cleared["price_before_minor"], "price_after_minor": cleared["price_after_minor"]})
        trace.append({"trade_date": step["trade_date"], "signal_cutoff_date": step["signal_cutoff_date"], "execution_reference_date": step["execution_reference_date"],
                      "feedback": feedback, "market_signal": feedback["market_signal"], "estimated_volatility": feedback["estimated_volatility"],
                      "text_signal_used": step["text_signal"] if use_text else 0.0, "text_uncertainty_used": step["text_uncertainty"] if use_text else 0.0,
                      "text_evidence": step["text_evidence"] if use_text else "text:disabled", "observations": observations, "decisions": decisions,
                      "quotes": quotes, "background_demands": demands, "arrival_order": order, "auction": cleared})
    saved_accounts = {name: {"kind": specs[name]["kind"], "role": specs[name].get("parameters", {}).get("role"),
                     "initial_wealth_minor": initial_wealth[name], **asdict(account),
                     "final_wealth_minor": account.cash_minor + account.shares * venue.price_minor,
                     "wealth_multiple": (account.cash_minor + account.shares * venue.price_minor) / initial_wealth[name],
                     **{field: totals[name][field] for field in ("trades", "fees_minor", "max_drawdown", "risk_breach_sessions")}}
                     for name, account in venue.accounts.items()}
    summary = {"sessions": len(steps), "participants": len(accounts), "background_participants": background["participants"],
               "use_text": use_text, "final_price_minor": venue.price_minor, "fee_pool_minor": venue.fee_pool_minor,
               "initial_cash_minor": venue.initial_cash_minor, "initial_shares": venue.initial_shares,
               "matched_volume": sum(categories.values()), "volume_by_counterparty": categories,
               "trade_sessions": sum(day["auction"]["matched_volume"] > 0 for day in trace),
               "price_change_sessions": sum(day["auction"]["price_before_minor"] != day["auction"]["price_after_minor"] for day in trace),
               "blocked_sessions": sum(not day["auction"]["execution_available"] for day in trace),
               "account_summary": saved_accounts, "trace_sha256": canonical_hash(trace)}
    for kind in ("strategy", "background"):
        items = [item for day in trace for item in day["auction"]["orders"] if specs[item["owner"]]["kind"] == kind]
        accepted, requested, filled = [sum(item[field] for item in items) for field in ("accepted_quantity", "quantity", "filled_quantity")]
        summary[f"{kind}_orders"] = {"requested": requested, "accepted": accepted, "filled": filled,
                                      "accepted_fill_fraction": filled / accepted if accepted else 0.0,
                                      "cash_clipped_orders": sum("cash_and_fee_reservation" in item["reasons"] for item in items),
                                      "inventory_clipped_orders": sum("sellable_inventory" in item["reasons"] for item in items)}
    summary["role_wealth_multiple"] = {role: sum(a["final_wealth_minor"] for a in saved_accounts.values() if a["role"] == role)
                                       / sum(a["initial_wealth_minor"] for a in saved_accounts.values() if a["role"] == role)
                                       for role in ("aggressive", "conservative", "institutional")}
    return {"summary": summary, "trace": trace, "participant_specs": specs}
