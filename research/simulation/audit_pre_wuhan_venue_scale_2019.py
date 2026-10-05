"""Decompose the 2019 toy venue's gap from observed whole-exchange volume.

The accepted two-sided quantity is only a mechanical ceiling for this venue.
It is not observed order flow, market depth, or a market-share estimate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..data_pipeline.provenance import file_sha256
from .audit_pre_wuhan_market_volume_2019 import (
    DOWNLOAD_MANIFEST, MARKET_MANIFEST, _observed_panel, compare_volume,
)


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "research_outputs/pre_wuhan_t_clock_market_2019_v1.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_venue_scale_audit_2019_v1.json"
FIELDS = ("requested_buy", "requested_sell", "accepted_buy", "accepted_sell",
          "filled_buy", "filled_sell", "matched_volume")


def compute() -> dict:
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    if (archive.get("pipeline_version") != "pre-wuhan-t-clock-market-development-v1"
            or archive.get("sample_role") != "seen_development_only"
            or archive.get("clock_contract", {}).get("auction_and_price_date") != "t"
            or archive.get("clock_contract", {}).get("observed_volume_date") != "t"):
        raise ValueError("same-auction-date development archive identity differs")
    rows = archive["corrected_daily_asset_rows"]
    keys = [(row["stock_code"], row["auction_date"]) for row in rows]
    codes = sorted({code for code, _ in keys})
    dates = sorted({day for _, day in keys})
    if (len(codes) != 123 or len(dates) != 43 or len(rows) != 5289
            or len(set(keys)) != len(keys)
            or set(keys) != {(code, day) for code in codes for day in dates}):
        raise ValueError("same-day venue trace is not the complete fixed panel")
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    download = json.loads(DOWNLOAD_MANIFEST.read_text(encoding="utf-8"))
    if market["market_dataset_id"] != archive["market_dataset_id"]:
        raise ValueError("market source differs from venue archive")
    observed = _observed_panel(codes, dates, download)
    paired = compare_volume(
        [{"stock_code": row["stock_code"],
          "execution_reference_date": row["auction_date"],
          "matched_volume": row["matched_volume"]} for row in rows], observed)
    if paired != archive["corrected_volume"]:
        raise ValueError("observed and simulated volume do not reconstruct archive")
    for row in rows:
        if (any(type(row[name]) is not int or row[name] < 0 for name in FIELDS)
                or row["accepted_buy"] > row["requested_buy"]
                or row["accepted_sell"] > row["requested_sell"]
                or row["filled_buy"] != row["matched_volume"]
                or row["filled_sell"] != row["matched_volume"]
                or row["matched_volume"] > min(row["accepted_buy"], row["accepted_sell"])
                or row["net_accepted"] != row["accepted_buy"] - row["accepted_sell"]
                or row["execution_available"] != (observed[row["stock_code"], row["auction_date"]] > 0)):
            raise ValueError("venue order, matching or execution status differs")
    totals = {name: sum(row[name] for row in rows) for name in FIELDS}
    capacity = sum(min(row["accepted_buy"], row["accepted_sell"]) for row in rows)
    observed_shares = paired["observed_traded_shares"]
    if not 0 < totals["matched_volume"] <= capacity < observed_shares:
        raise ValueError("venue/observed volume decomposition is outside expected bounds")
    return {
        "pipeline_version": "pre-wuhan-venue-scale-audit-v1",
        "sample_role": "seen_development_only",
        "company_days": len(rows), "companies": len(codes), "sessions": len(dates),
        "units": "shares", "venue_totals": totals,
        "per_company_day_accepted_two_sided_ceiling": capacity,
        "observed_whole_exchange_shares": observed_shares,
        "ratios": {
            "requested_buy_to_observed": totals["requested_buy"] / observed_shares,
            "requested_sell_to_observed": totals["requested_sell"] / observed_shares,
            "accepted_buy_to_observed": totals["accepted_buy"] / observed_shares,
            "accepted_sell_to_observed": totals["accepted_sell"] / observed_shares,
            "two_sided_ceiling_to_observed": capacity / observed_shares,
            "matched_to_two_sided_ceiling": totals["matched_volume"] / capacity,
            "matched_to_observed": totals["matched_volume"] / observed_shares,
        },
        "source_sha256": {
            "t_clock_archive": file_sha256(ARCHIVE),
            "provider_download_manifest": file_sha256(DOWNLOAD_MANIFEST),
            "market_manifest": file_sha256(MARKET_MANIFEST),
            "audit_code": file_sha256(Path(__file__)),
        },
        "interpretation": "The fixed venue has too little accepted two-sided share quantity to represent whole-exchange daily volume. The ceiling assumes all accepted orders at a company-day could cross regardless of price; it is not executable liquidity. Provider whole-day volume is used only after simulation. The ratio neither identifies the venue's real market share nor calibrates investor behavior or price impact.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("venue scale audit differs from source recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("venue scale audit output already exists")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                     allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"company_days": result["company_days"],
                      "venue_totals": result["venue_totals"],
                      "ratios": result["ratios"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
