"""Parse an archived Sina daily K-line response as an explicit market supplement."""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

from .market_data import strict_date


def _positive_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Sina {field} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"Sina {field} must be finite and positive")
    return number


def parse_sina_daily_response(raw: bytes, stock_code: str, start_date: str, end_date: str,
                              expected_sessions: list[str]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Convert raw daily close levels to simple returns without filling sessions.

    Sina's endpoint response has no adjustment parameter. The returned series is
    therefore explicitly treated as unadjusted close-to-close price return.
    """
    start, end = strict_date(start_date), strict_date(end_date)
    if stock_code != "200468":
        raise ValueError("this verified supplement parser is frozen to B-share 200468")
    if start > end or not expected_sessions or expected_sessions != sorted(set(expected_sessions)):
        raise ValueError("expected sessions must be a nonempty, sorted unique calendar")
    sessions = [strict_date(value) for value in expected_sessions]
    if sessions[0] < start or sessions[-1] > end:
        raise ValueError("expected sessions escape the declared source window")
    try:
        decoded = raw.decode("gbk")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Sina response is not valid GBK JSON") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("Sina response must contain daily rows")

    ordered: list[tuple[date, float]] = []
    seen: set[date] = set()
    required = {"day", "open", "high", "low", "close", "volume"}
    for row in payload:
        if not isinstance(row, dict) or required - row.keys():
            raise ValueError("Sina daily row is missing required fields")
        day = strict_date(row["day"])
        if day in seen:
            raise ValueError("Sina daily rows contain duplicate dates")
        seen.add(day)
        close = _positive_number(row["close"], "close")
        for field in ("open", "high", "low"):
            _positive_number(row[field], field)
        try:
            volume = float(row["volume"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Sina volume must be numeric") from exc
        if not math.isfinite(volume) or volume < 0:
            raise ValueError("Sina volume must be finite and nonnegative")
        ordered.append((day, close))
    if ordered != sorted(ordered):
        raise ValueError("Sina daily dates must be strictly increasing")

    selected = [(day, close) for day, close in ordered if start <= day <= end]
    selected_dates = [day for day, _ in selected]
    if selected_dates != sessions:
        missing = len(set(sessions) - set(selected_dates))
        extra = len(set(selected_dates) - set(sessions))
        raise ValueError(f"Sina dates do not exactly cover the market calendar: {missing} missing, {extra} extra")
    first_index = next(index for index, (day, _) in enumerate(ordered) if day == selected[0][0])
    if first_index == 0 or ordered[first_index - 1][0] >= start:
        raise ValueError("Sina response lacks a prior close for the first requested session")

    rows = []
    for day, close in selected:
        index = next(i for i, (candidate, _) in enumerate(ordered) if candidate == day)
        previous_close = ordered[index - 1][1]
        result_pct = (close / previous_close - 1.0) * 100.0
        if not math.isfinite(result_pct) or result_pct < -100:
            raise ValueError("Sina adjacent-close return is invalid")
        rows.append({"trade_date": day.isoformat(), "stock_code": stock_code,
                     "stock_return_pct": format(result_pct, ".15g")})
    return rows, {
        "symbol": "sz." + stock_code,
        "rows": len(rows),
        "suspended_sessions": [],
        "same_day_return_gate": "computed_from_adjacent_unadjusted_closes",
        "adjustment_basis": "unadjusted_close_to_close_price_return",
        "output_method": "sina_adjacent_close_simple_return",
        "first_date": rows[0]["trade_date"],
        "last_date": rows[-1]["trade_date"],
    }
