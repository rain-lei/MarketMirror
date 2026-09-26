from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class DailyObservation:
    trade_date: date
    stock_return: float
    market_return: float


def _to_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def fit_market_model(stock_returns: Sequence[float], market_returns: Sequence[float]) -> tuple[float, float]:
    """Fit stock_return = alpha + beta * market_return with OLS."""
    if len(stock_returns) != len(market_returns) or len(stock_returns) < 2:
        raise ValueError("market model requires at least two aligned observations")
    if not all(math.isfinite(x) for x in (*stock_returns, *market_returns)):
        raise ValueError("market model requires finite returns")
    mean_stock = sum(stock_returns) / len(stock_returns)
    mean_market = sum(market_returns) / len(market_returns)
    covariance = sum((x - mean_market) * (y - mean_stock) for x, y in zip(market_returns, stock_returns))
    variance = sum((x - mean_market) ** 2 for x in market_returns)
    if variance == 0:
        raise ValueError("market return variance is zero")
    beta = covariance / variance
    alpha = mean_stock - beta * mean_market
    return alpha, beta


def event_study(
    observations: Iterable[DailyObservation],
    event_date: date | datetime | str,
    estimation_window: int = 120,
    pre_event_gap: int = 5,
    window_before: int = 3,
    window_after: int = 5,
) -> dict[str, object]:
    """Estimate abnormal returns with disjoint estimation and event windows.

    Window lengths count trading observations. pre_event_gap counts excluded
    observations before the event window starts. Returns are decimal fractions.
    """
    for name, value in (("estimation_window", estimation_window), ("pre_event_gap", pre_event_gap),
                        ("window_before", window_before), ("window_after", window_after)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if estimation_window < 20:
        raise ValueError("at least 20 estimation observations are required")
    ordered = sorted(observations, key=lambda item: item.trade_date)
    if not ordered:
        raise ValueError("event study requires observations")
    if len({item.trade_date for item in ordered}) != len(ordered):
        raise ValueError("duplicate trade dates must be resolved before event study")
    if not all(math.isfinite(value) for item in ordered for value in (item.stock_return, item.market_return)):
        raise ValueError("event study requires finite returns")
    target = _to_date(event_date)
    event_index = next((i for i, item in enumerate(ordered) if item.trade_date >= target), None)
    if event_index is None:
        raise ValueError("event date is after the available market data")
    window_start = event_index - window_before
    window_end = event_index + window_after + 1
    if window_start < 0 or window_end > len(ordered):
        raise ValueError("complete event window is not available")
    estimation_end = window_start - pre_event_gap
    estimation_start = estimation_end - estimation_window
    if estimation_start < 0:
        raise ValueError("complete estimation window is not available")
    estimation = ordered[estimation_start:estimation_end]
    if len(estimation) < 20:
        raise ValueError("at least 20 estimation observations are required")
    alpha, beta = fit_market_model(
        [item.stock_return for item in estimation],
        [item.market_return for item in estimation],
    )
    abnormal = []
    for i, item in enumerate(ordered[window_start:window_end], start=window_start):
        value = item.stock_return - (alpha + beta * item.market_return)
        abnormal.append({
            "trade_date": item.trade_date.isoformat(),
            "relative_trade_day": i - event_index,
            "abnormal_return": value,
        })
    return {
        "event_date_requested": target.isoformat(),
        "event_date_used": ordered[event_index].trade_date.isoformat(),
        "estimation_start": estimation[0].trade_date.isoformat(),
        "estimation_end": estimation[-1].trade_date.isoformat(),
        "estimation_observations": len(estimation),
        "pre_event_gap": pre_event_gap,
        "alpha": alpha,
        "beta": beta,
        "window_before": window_before,
        "window_after": window_after,
        "abnormal_returns": abnormal,
        "cumulative_abnormal_return": sum(item["abnormal_return"] for item in abnormal),
    }


def read_market_csv(path: Path, stock_code: str | None = None) -> list[DailyObservation]:
    """Read a narrow market CSV with trade_date, stock_return, market_return columns."""
    observations: list[DailyObservation] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"trade_date", "stock_return", "market_return"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"market CSV is missing columns: {sorted(missing)}")
        has_codes = "stock_code" in (reader.fieldnames or [])
        if stock_code is not None and not has_codes:
            raise ValueError("stock_code column is required when filtering a security")
        seen_codes = set()
        for row in reader:
            if has_codes and not row["stock_code"]:
                raise ValueError("stock_code value is missing")
            if stock_code is not None and row["stock_code"] != stock_code:
                continue
            if has_codes:
                seen_codes.add(row["stock_code"])
                if len(seen_codes) > 1:
                    raise ValueError("multiple securities require an explicit stock_code filter")
            observations.append(
                DailyObservation(
                    trade_date=_to_date(row["trade_date"]),
                    stock_return=float(row["stock_return"]),
                    market_return=float(row["market_return"]),
                )
            )
    if not observations:
        raise ValueError("no market observations matched the requested security")
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a market-model event study")
    parser.add_argument("market_csv", type=Path)
    parser.add_argument("event_date")
    parser.add_argument("--stock-code", default=None)
    parser.add_argument("--estimation-window", type=int, default=120)
    parser.add_argument("--pre-event-gap", type=int, default=5)
    parser.add_argument("--window-before", type=int, default=3)
    parser.add_argument("--window-after", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = event_study(read_market_csv(args.market_csv, args.stock_code), args.event_date,
                         args.estimation_window, args.pre_event_gap, args.window_before, args.window_after)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
