"""Measure whether the simulator's configured daily price band constrains paths."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_common_shock_balanced_2019_v1.json"
BASE_CONFIG = ROOT / "research/configs/wuhan_pre_event_pit_portfolio_no_text_2020.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_price_band_diagnostic_2019_v1.json"
VERSION = "pre-wuhan-price-band-diagnostic-v1"
THRESHOLDS_BPS = (500, 900, 950, 1000, 1900)
EXPECTED_VARIANTS = {"no_shock", "common_only", "issuer_specific_only", "common_plus_issuer_specific"}


def _price_bounds(before: int, band_bps: int, tick: int) -> tuple[int, int]:
    lower = max(tick, ((before * (10000 - band_bps) + 10000 * tick - 1)
                       // (10000 * tick)) * tick)
    upper = before * (10000 + band_bps) // (10000 * tick) * tick
    return lower, upper


def price_band_metrics(rows: list[dict], band_bps: int, tick: int = 1) -> dict:
    """Compare simulated band use with observed return magnitudes, not limit-hit labels."""
    if (not isinstance(rows, list) or not rows or type(band_bps) is not int
            or not 0 <= band_bps < 10000 or type(tick) is not int or tick <= 0):
        raise ValueError("price-band metrics require rows and valid band/tick settings")
    keys = set()
    simulated, observed = [], []
    upper_touches = lower_touches = 0
    for row in rows:
        required = {"stock_code", "trade_date", "price_before_minor", "price_after_minor", "observed_return"}
        if not isinstance(row, dict) or not required <= set(row):
            raise ValueError("price-band row lacks required fields")
        key = (row["stock_code"], row["trade_date"])
        before, after, actual = row["price_before_minor"], row["price_after_minor"], row["observed_return"]
        if (key in keys or type(before) is not int or before <= 0 or type(after) is not int or after <= 0
                or after % tick or type(actual) not in (int, float) or not math.isfinite(actual)):
            raise ValueError("duplicate row or invalid price/return in price-band panel")
        keys.add(key)
        lower, upper = _price_bounds(before, band_bps, tick)
        if not lower <= after <= upper:
            raise ValueError("simulated clearing price violates its configured price band")
        lower_touches += int(after == lower)
        upper_touches += int(after == upper)
        simulated.append(after / before - 1)
        observed.append(float(actual))

    def magnitude_summary(values: list[float]) -> dict:
        return {
            "maximum_absolute_return_pct": max(abs(value) for value in values) * 100,
            "absolute_return_count_at_least_pct": {
                f"{threshold / 100:.1f}": sum(abs(value) * 100 >= threshold / 100 for value in values)
                for threshold in THRESHOLDS_BPS
            },
        }

    return {
        "company_days": len(rows),
        "configured_band_bps": band_bps,
        "configured_tick_minor": tick,
        "simulated_upper_band_touches": upper_touches,
        "simulated_lower_band_touches": lower_touches,
        "simulated_price_change_magnitude": magnitude_summary(simulated),
        "observed_adjusted_return_magnitude": magnitude_summary(observed),
        "observed_return_interpretation": "tail magnitudes only; adjusted pctChg does not establish historical price-limit hits",
    }


def compute(source_path: Path = SOURCE) -> dict:
    source_path = source_path.resolve()
    if source_path != SOURCE.resolve():
        raise ValueError("price-band diagnostic is bound to the frozen balanced 2019 shock archive")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    config = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    expected_config_hash = source.get("input_sha256", {}).get(str(BASE_CONFIG.resolve()))
    if (source.get("result", {}).get("pipeline_version") != "pre-wuhan-common-shock-balanced-sensitivity-v1"
            or source["result"].get("sample", {}).get("selected_companies") != 123
            or source["result"].get("sample", {}).get("sessions") != 43
            or expected_config_hash != file_sha256(BASE_CONFIG)
            or config.get("venue", {}).get("price_band_bps") != 1000
            or config.get("venue", {}).get("tick_minor") != 1):
        raise ValueError("balanced scenario archive or fixed venue price-band settings differ")
    variants = {row.get("name"): row for row in source["result"].get("variants", [])}
    if set(variants) != EXPECTED_VARIANTS:
        raise ValueError("balanced scenario archive does not contain the complete 2x2 grid")

    observed_panels = {}
    variant_results = {}
    for name, variant in variants.items():
        rows = variant.get("daily_asset_rows")
        if not isinstance(rows, list) or len(rows) != 5289:
            raise ValueError("balanced scenario variant lacks the full 123 by 43 company-day panel")
        panel = {(row.get("stock_code"), row.get("trade_date")): row.get("observed_return") for row in rows}
        if len(panel) != len(rows):
            raise ValueError("balanced scenario variant has duplicate stock-date rows")
        observed_panels[name] = panel
        variant_results[name] = price_band_metrics(rows, config["venue"]["price_band_bps"],
                                                   config["venue"]["tick_minor"])
    first_panel = next(iter(observed_panels.values()))
    if any(panel != first_panel for panel in observed_panels.values()):
        raise ValueError("paired scenario variants do not share the same observed return panel")

    return {
        "pipeline_version": VERSION,
        "purpose": "check whether the configured model price band binds and describe observed return-tail magnitudes",
        "source_archive": str(source_path),
        "source_archive_sha256": file_sha256(source_path),
        "base_config": str(BASE_CONFIG.resolve()),
        "base_config_sha256": file_sha256(BASE_CONFIG),
        "sample": {"companies": 123, "sessions": 43, "company_days": 5289,
                   "period": "2019-11-01 through 2019-12-31",
                   "interpretation": "2019 development panel; execution and observed-return clocks remain distinct"},
        "variants": variant_results,
        "method_limits": [
            "Model price-band touches are computed from simulated auction prices and the fixed 10% configured band.",
            "Observed tail counts use provider backward-adjusted daily pctChg; they are not historical limit-hit classifications.",
            "The archived source has no unadjusted OHLC bars or historical daily ST state, so exact exchange limit prices and limit queues cannot be reconstructed.",
            "A scenario's simulated auction uses its execution reference state while observed returns are the following replay-step outcome; the paired rows are not a same-exchange-session price fit.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    payload = {"result": result, "code_sha256": file_sha256(Path(__file__))}
    destination = args.output.resolve()
    if destination.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("price-band diagnostic output must be written under research_outputs")
    if args.audit_existing:
        if json.loads(destination.read_text(encoding="utf-8")) != payload:
            raise ValueError("archived price-band diagnostic differs from recomputation")
    else:
        if destination.exists():
            raise ValueError("choose a new price-band diagnostic output path")
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                               encoding="utf-8")
    print(json.dumps(result["variants"], ensure_ascii=False))


if __name__ == "__main__":
    main()
