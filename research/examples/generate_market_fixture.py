"""Create deterministic synthetic prices, a synthetic calendar and experiment configs."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path


def generate_fixture(output_dir: Path) -> dict[str, Path]:
    output_dir = output_dir.resolve()
    names = ["stock_prices.csv", "benchmark_prices.csv", "calendar.csv", "market_import.json", "experiment.json", "fixture_expectations.json"]
    if any((output_dir / name).exists() for name in names):
        raise ValueError("fixture files already exist; choose a fresh output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    sessions = []
    day = date(2020, 1, 1)
    while len(sessions) < 100:
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    market_price, prices = 3000.0, {"000001": 100.0, "000002": 80.0}
    parameters = {"000001": (0.0003, 1.2, 0.04), "000002": (-0.0001, 0.8, -0.02)}
    stocks, benchmark = [], []
    for i, day in enumerate(sessions):
        if i:
            market_return = ((i % 7) - 3) * 0.001 + ((i % 11) - 5) * 0.0001
            market_price *= 1 + market_return
            for code, (alpha, beta, shock) in parameters.items():
                stock_return = alpha + beta * market_return + (shock if i == 60 else 0)
                prices[code] *= 1 + stock_return
        benchmark.append([day.isoformat(), "SYNTHETIC_INDEX", market_price])
        for code in sorted(prices):
            stocks.append([day.isoformat(), code, prices[code]])
    for name, header, rows in (
        (names[0], ["trade_date", "stock_code", "adjusted_close"], stocks),
        (names[1], ["trade_date", "benchmark_id", "close"], benchmark),
        (names[2], ["trade_date"], [[d.isoformat()] for d in sessions]),
    ):
        with (output_dir / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
    settings = {
        "stock_input": names[0], "benchmark_input": names[1], "calendar_input": names[2],
        "benchmark_id": "SYNTHETIC_INDEX", "stock_format": "prices", "benchmark_format": "prices",
        "stock_value_column": "adjusted_close", "benchmark_value_column": "close",
        "stock_price_basis": "split_dividend_adjusted", "benchmark_price_basis": "price_index",
        "stock_data_source": "Generated deterministic market-model fixture, not actual securities.",
        "benchmark_data_source": "Generated deterministic index fixture, not a real index.",
        "calendar_data_source": "Generated weekdays only; does not represent historical exchange holidays.",
        "data_kind": "synthetic", "return_type": "simple",
    }
    experiment = {
        "run_id": "synthetic_pipeline_validation", "market_manifest": "prepared/market_manifest.json", "data_kind": "synthetic",
        "stock_codes": ["000001", "000002"],
        "windows": {"estimation_window": 40, "pre_event_gap": 5, "window_before": 3, "window_after": 5},
        "events": [
            {"event_id": "known_shock", "event_date": sessions[60].isoformat(),
             "visible_at": sessions[60].isoformat() + "T08:00:00+08:00", "event_type": "synthetic_shock",
             "evidence_source": "Generated fixture: abnormal returns +0.04 and -0.02 on session 60."},
            {"event_id": "date_only_notice", "event_date": sessions[50].isoformat(),
             "visible_date": sessions[50].isoformat(), "event_type": "synthetic_no_shock",
             "evidence_source": "Generated fixture: date-only notice with no added shock."},
        ],
    }
    expectations = {
        "data_kind": "synthetic", "warning": "Not real prices, trading calendar, historical events or simulated Agent behavior.",
        "known_shock_car": {code: values[2] for code, values in parameters.items()},
        "date_only_notice_car": {code: 0.0 for code in parameters},
        "known_shock_session": sessions[60].isoformat(), "date_only_notice_session": sessions[51].isoformat(),
    }
    for name, value in ((names[3], settings), (names[4], experiment), (names[5], expectations)):
        (output_dir / name).write_text(json.dumps(value, indent=2), encoding="utf-8")
    return {"market_config": output_dir / names[3], "experiment_config": output_dir / names[4]}


def main():
    parser = argparse.ArgumentParser(description="Generate explicitly synthetic event-study validation files")
    parser.add_argument("--output-dir", type=Path, default=Path("research_outputs/market_demo"))
    args = parser.parse_args()
    print({k: str(v) for k, v in generate_fixture(args.output_dir).items()})


if __name__ == "__main__":
    main()
