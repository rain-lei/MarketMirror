"""Compose the frozen Wuhan fresh-cohort market sources without viewing QA text."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .provenance import file_sha256
from .sina_supplement import parse_sina_daily_response

VERSION = "wuhan-fresh-market-composite-v1"
FRESH_RULE = "sha256_rank_after_excluding_prior_development_and_v3_validation_cohorts"
MISSING_CODE = "200468"
SINA_PROVIDER = "Sina public historical K-line endpoint"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _symbol(code: str) -> str:
    return ("sh." if code.startswith(("600", "601", "603", "605", "688", "689")) else "sz.") + code


def _read_stock_returns(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = ["trade_date", "stock_code", "stock_return_pct"]
        if reader.fieldnames != expected:
            raise ValueError("BaoStock stock return headers differ from the frozen pipeline")
        rows = list(reader)
    if not rows or any(any(value is None for value in row.values()) for row in rows):
        raise ValueError("BaoStock stock return file is empty or has malformed rows")
    return rows


def _write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def compose_market(baostock_dir: Path, sina_capture_dir: Path, universe_path: Path,
                   output_dir: Path) -> dict[str, Any]:
    """Bind 125 BaoStock series and one exact-calendar Sina supplement to one input panel."""
    baostock_dir, sina_capture_dir, universe_path = (
        baostock_dir.resolve(), sina_capture_dir.resolve(), universe_path.resolve())
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("composite download requires a fresh empty output directory")

    universe = _read_json(universe_path)
    audit = universe.get("selection_audit")
    codes = universe.get("stock_codes")
    if (not isinstance(audit, dict) or audit.get("selection_rule") != FRESH_RULE
            or audit.get("sample_size") != 126 or audit.get("status") != "membership_frozen_text_unreviewed_model_unrun"
            or not isinstance(codes, list) or len(codes) != 126 or len(set(codes)) != 126
            or MISSING_CODE not in codes):
        raise ValueError("universe is not the frozen 126-company fresh holdout")
    symbols = [_symbol(code) for code in codes]

    base_path = baostock_dir / "download_manifest.json"
    base = _read_json(base_path)
    expected_primary = set(symbols) - {"sz." + MISSING_CODE}
    if (base.get("provider") != "BaoStock" or base.get("data_kind") != "observed"
            or set(base.get("source_symbols", [])) != expected_primary
            or base.get("benchmark_symbol") != "sh.000300"
            or base.get("start_date") != "2019-06-01" or base.get("end_date") != "2020-03-31"):
        raise ValueError("BaoStock source does not match the frozen 125-symbol market request")
    for name, artifact in base.get("artifacts", {}).items():
        source = baostock_dir / name
        if not source.is_file() or file_sha256(source) != artifact.get("sha256"):
            raise ValueError(f"BaoStock source artifact differs from its manifest: {name}")
    calendar_path = baostock_dir / "calendar.csv"
    with calendar_path.open(encoding="utf-8-sig", newline="") as handle:
        sessions = [row["trade_date"] for row in csv.DictReader(handle)]
    if not sessions or sessions != sorted(set(sessions)):
        raise ValueError("BaoStock market calendar must be sorted and unique")

    capture_manifest_path = sina_capture_dir / "capture_manifest.json"
    capture = _read_json(capture_manifest_path)
    raw_path = sina_capture_dir / "sina_response.json"
    raw = raw_path.read_bytes()
    parameters = capture.get("request_parameters", {})
    if (capture.get("provider") != SINA_PROVIDER or capture.get("http_status") != 200
            or parameters != {"symbol": "sz200468", "scale": "240", "ma": "no", "datalen": "3000"}
            or capture.get("response_sha256") != file_sha256(raw_path)
            or capture.get("response_bytes") != len(raw)
            or capture.get("target_window") != [base["start_date"], base["end_date"]]
            or capture.get("exact_calendar_match") is not True):
        raise ValueError("Sina capture identity or source hash differs from the fixed supplement")
    sina_rows, quote_check = parse_sina_daily_response(
        raw, MISSING_CODE, base["start_date"], base["end_date"], sessions)
    if (capture.get("target_rows") != len(sina_rows)
            or len(sina_rows) != len(sessions)):
        raise ValueError("Sina supplement rows differ from the provider calendar")

    primary_rows = _read_stock_returns(baostock_dir / "stock_returns.csv")
    primary_codes = {row["stock_code"] for row in primary_rows}
    expected_primary_codes = {symbol.split(".", 1)[1] for symbol in expected_primary}
    if primary_codes != expected_primary_codes:
        raise ValueError("BaoStock stock return codes differ from its manifest")
    combined = primary_rows + sina_rows
    identities = [(row["stock_code"], row["trade_date"]) for row in combined]
    if len(identities) != len(set(identities)):
        raise ValueError("composite stock input has duplicate stock/date rows")
    combined.sort(key=lambda row: (row["stock_code"], row["trade_date"]))

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        artifact_name_map = {}
        for name in base["artifacts"]:
            if name == "stock_returns.csv":
                target_name = "baostock_stock_returns.csv"
            elif name == "market_import.json":
                target_name = "baostock_market_import.json"
            else:
                target_name = name
            shutil.copyfile(baostock_dir / name, staging / target_name)
            artifact_name_map[name] = target_name
        shutil.copyfile(base_path, staging / "baostock_download_manifest.json")
        shutil.copyfile(raw_path, staging / "sina_200468_response.json")
        shutil.copyfile(capture_manifest_path, staging / "sina_200468_capture_manifest.json")
        _write_rows(staging / "stock_returns.csv", ["trade_date", "stock_code", "stock_return_pct"], combined)

        base_settings = json.loads((baostock_dir / "market_import.json").read_text(encoding="utf-8"))
        settings = {
            **base_settings,
            "stock_input": "stock_returns.csv",
            "benchmark_input": "benchmark_prices.csv",
            "calendar_input": "calendar.csv",
            "stock_return_basis": "mixed_adjusted_and_unadjusted_price_return",
            "stock_data_source": (
                "Mixed observed sources: BaoStock SDK 0.9.4 adjusted pctChg for 125 symbols; "
                "Sina daily close-to-close price return for sz.200468, with no adjustment parameter; "
                "see composite download_manifest.json and source response hashes."),
        }
        (staging / "market_import.json").write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        supplemental = {
            "provider": "Sina",
            "stock_code": MISSING_CODE,
            "symbol": "sz." + MISSING_CODE,
            "response_file": "sina_200468_response.json",
            "response_sha256": file_sha256(staging / "sina_200468_response.json"),
            "capture_manifest_file": "sina_200468_capture_manifest.json",
            "capture_manifest_sha256": file_sha256(staging / "sina_200468_capture_manifest.json"),
            "adjustment_basis": "unadjusted_close_to_close_price_return",
            "return_method": "close_t / close_previous_session - 1",
            "rows": len(sina_rows),
            "calendar_match": True,
        }
        quote_checks = [row for row in base["quote_checks"]
                        if row["symbol"] != base["benchmark_symbol"]] + [quote_check]
        quote_checks.sort(key=lambda row: row["symbol"])
        query = {
            "provider": "Sina", "function": "CN_MarketData.getKLineData",
            "symbol": "sz." + MISSING_CODE, "start_date": base["start_date"],
            "end_date": base["end_date"], "frequency": "d", "adjustment": "none parameter supplied",
            "target_rows": len(sina_rows), "response_rows": capture["total_rows"],
            "raw_file": "sina_200468_response.json",
        }
        source_hashes = {
            "baostock_download_manifest.json": file_sha256(staging / "baostock_download_manifest.json"),
            "sina_capture_manifest.json": file_sha256(staging / "sina_200468_capture_manifest.json"),
        }
        code_hashes = {
            "compose_wuhan_fresh_market.py": file_sha256(Path(__file__)),
            "sina_supplement.py": file_sha256(Path(__file__).with_name("sina_supplement.py")),
        }
        manifest = {
            "pipeline_version": VERSION,
            "provider": "BaoStock",
            "provider_scope": "primary source; supplemental_sources records the single Sina exception",
            "sdk_version": base["sdk_version"],
            "data_kind": "observed",
            "documentation": base["documentation"],
            "code_sha256": code_hashes,
            "source_manifests": source_hashes,
            "source_config_sha256": file_sha256(universe_path),
            "source_symbols": sorted(symbols),
            "benchmark_symbol": base["benchmark_symbol"],
            "start_date": base["start_date"],
            "end_date": base["end_date"],
            "queries": base["queries"] + [query],
            "quote_checks": quote_checks,
            "supplemental_sources": [supplemental],
            "baostock_artifact_name_map": artifact_name_map,
            "assumptions": base.get("assumptions", []) + [
                "The frozen 126-company roster is unchanged; 125 return series use BaoStock adjusted pctChg.",
                "sz.200468 uses Sina adjacent unadjusted close returns because BaoStock returned no history for this B-share.",
                "The combined stock panel therefore mixes adjusted and unadjusted price returns; this source-basis sensitivity is explicit.",
                "Sina daily dates must exactly match the BaoStock calendar; no missing-session fill or bridging is performed.",
            ],
        }
        manifest["artifacts"] = {
            path.name: {"sha256": file_sha256(path), "bytes": path.stat().st_size}
            for path in staging.iterdir() if path.is_file()
        }
        (staging / "download_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baostock-dir", type=Path, required=True)
    parser.add_argument("--sina-capture-dir", type=Path, required=True)
    parser.add_argument("--universe-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = compose_market(args.baostock_dir, args.sina_capture_dir, args.universe_config,
                              args.output_dir)
    print(json.dumps({"provider": manifest["provider"], "primary_symbols": 125,
                      "supplemental_symbols": 1, "stock_symbols": len(manifest["source_symbols"]),
                      "data_kind": manifest["data_kind"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
