"""Date-aligned order randomness for carried-state pre-evaluation simulation."""
from __future__ import annotations

from datetime import date


def require(value, message):
    if not value:
        raise ValueError(message)


def prepare_clock(calendar, evaluation_start_date, volatility_window, agents):
    if evaluation_start_date is None:
        return None
    require(type(evaluation_start_date) is str and date.fromisoformat(evaluation_start_date).isoformat() == evaluation_start_date,
            "evaluation start must be canonical ISO date")
    days = [d for d, _, _ in calendar]
    require(days == sorted(set(days)) and evaluation_start_date in days, "evaluation start outside complete calendar")
    offset = days.index(evaluation_start_date)
    cutoff = calendar[offset][1]
    require(type(volatility_window) is int and volatility_window >= 2 and offset >= volatility_window + 2
            and sum(d <= cutoff for d in days[:offset]) >= volatility_window, "insufficient formed own-price history before evaluation cutoff")
    require(all(type(a.rebalance_interval) is int and a.rebalance_interval > 0 and offset % a.rebalance_interval == 0 for a in agents),
            "warmup length would change evaluation rebalance schedule")
    return {"version": "date-aligned-carried-state-warmup-clock-v1", "evaluation_start_date": evaluation_start_date,
        "warmup_sessions": offset, "warmup_start_date": days[0], "warmup_end_date": days[offset - 1],
        "draw_rule": "model session minus warmup sessions; Q1 arrival and background hash keys match the original cold Q1 sessions"}


def draw_session(session, clock):
    require(type(session) is int and session >= 0, "model session must be a nonnegative integer")
    return session if clock is None else session - clock["warmup_sessions"]


def clock_metadata(session, clock):
    if clock is None:
        return None
    draw = draw_session(session, clock)
    return {"version": clock["version"], "evaluation_start_date": clock["evaluation_start_date"],
        "warmup_sessions": clock["warmup_sessions"], "model_session": session, "draw_session": draw,
        "phase": "warmup" if draw < 0 else "evaluation"}
