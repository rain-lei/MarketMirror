"""Archive bounded EastMoney responses without asserting adjusted-return eligibility."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .market_data import strict_date
from .provenance import file_sha256

VERSION = "eastmoney-raw-acquisition-with-explicit-qc-v1"
ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
FIELDS = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

def request_url(secid: str, fqt: int, start: str, end: str) -> str:
    if (not isinstance(secid, str) or len(secid) != 8 or secid[:2] not in {"0.", "1."}
        or not secid[2:].isdigit() or type(fqt) is not int or fqt not in {0, 1}
        or strict_date(start) > strict_date(end)):
        raise ValueError("bounded raw source request identity or dates invalid")
    return ENDPOINT + "?" + urlencode({"secid": secid, "klt": 101, "fqt": fqt,
        "beg": start.replace("-", ""), "end": end.replace("-", ""),
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": FIELDS})

def inspect_response(raw: bytes, secid: str, start: str, end: str, expected_dates: list[str]) -> dict:
    """Verify request identity and coverage only; never produce model features."""
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or type(payload.get("rc")) is not int or payload["rc"] != 0
        or not isinstance(payload.get("data"), dict)):
        raise ValueError("provider response has no successful selected series")
    data = payload["data"]
    if (data.get("code") != secid[2:] or data.get("market") != int(secid[0])
        or not isinstance(data.get("klines"), list) or not data["klines"]):
        raise ValueError("provider returned another code or no K-lines")
    dates, blank_numeric_fields, zero_volume_dates = [], [], []
    for raw_row in data["klines"]:
        if not isinstance(raw_row, str):
            raise ValueError("provider K-line must be a CSV string")
        columns = raw_row.split(",")
        if len(columns) != 11:
            raise ValueError("provider K-line width differs from fixed fields2")
        day = strict_date(columns[0]).isoformat()
        if not start <= day <= end:
            raise ValueError("provider K-line date escapes request")
        dates.append(day)
        for position, value in enumerate(columns[1:], 1):
            if value in {"", "-"}:
                blank_numeric_fields.append({"trade_date": day, "field": FIELDS.split(",")[position]})
                continue
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("provider numeric field is not finite")
            if position in {5, 6} and number < 0:
                raise ValueError("provider volume/amount is negative")
        if columns[5] not in {"", "-"} and float(columns[5]) == 0:
            zero_volume_dates.append(day)
    if dates != sorted(set(dates)):
        raise ValueError("provider K-line dates are duplicated or unordered")
    if expected_dates != sorted(set(expected_dates)) or any(not start <= d <= end for d in expected_dates):
        raise ValueError("independent expected calendar invalid")
    missing, extra = sorted(set(expected_dates) - set(dates)), sorted(set(dates) - set(expected_dates))
    return {"rows": len(dates), "first_date": dates[0], "last_date": dates[-1],
        "missing_expected_sessions": missing, "unexpected_sessions": extra,
        "blank_numeric_fields": blank_numeric_fields, "zero_volume_dates_without_explicit_trade_status": zero_volume_dates,
        "calendar_coverage_complete": not missing and not extra,
        "provider_code": data["code"], "provider_name": data.get("name"),
        "basis_verified_for_model_use": False, "model_eligible": False,
        "price_and_volume_units_verified": False, "explicit_trade_status_supplied": False}

def fetch_raw(config: dict, protocol_path: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("raw acquisition requires a new directory; preserve prior responses")
    output.mkdir(parents=True)
    if config["outcome_analysis_enabled"] is not False or config["max_workers"] != 2 or config["max_attempts"] != 2:
        raise ValueError("raw acquisition scope or concurrency changed")
    start, end = config["download_period"]
    requests = config["requests"]
    keys = [r["secid"] + "_fqt" + str(r["fqt"]) for r in requests]
    if len(requests) != 247 or len(set(keys)) != len(keys):
        raise ValueError("raw acquisition declared request grid differs")
    def acquire(spec):
        key = spec["secid"] + "_fqt" + str(spec["fqt"])
        url = request_url(spec["secid"], spec["fqt"], start, end)
        entry = {**spec, "url": url, "attempts": [], "model_eligible": False}
        for attempt in range(1, config["max_attempts"] + 1):
            detail = {"attempt": attempt, "started_at_utc": datetime.now(timezone.utc).isoformat()}
            entry["attempts"].append(detail)
            try:
                request = Request(url, headers={"User-Agent": "MarketMirrorResearch/1.0", "Referer": "https://quote.eastmoney.com/"})
                with urlopen(request, timeout=15) as stream:
                    raw = stream.read(2_000_001)
                    detail["http_status"] = stream.status
                if len(raw) > 2_000_000:
                    raise ValueError("provider response exceeds frozen byte bound")
                target = output / f"{key}_attempt{attempt}.json"
                target.write_bytes(raw)
                detail.update(raw_path=target.name, raw_sha256=file_sha256(target), response_bytes=len(raw))
                inspection = inspect_response(raw, spec["secid"], start, end, config["expected_session_dates"])
                detail.update(status="VALID_RAW_RESPONSE", finished_at_utc=datetime.now(timezone.utc).isoformat())
                entry.update(status="RAW_ACQUIRED_MODEL_QC_PENDING", inspection=inspection)
                break
            except Exception as error:
                detail.update(status="FAILED_RAW_SOURCE_ATTEMPT", error_type=type(error).__name__, error=str(error),
                    finished_at_utc=datetime.now(timezone.utc).isoformat())
        if "inspection" not in entry:
            entry["status"] = "FAILED_RAW_SOURCE_REQUEST"
        target = output / f"{key}_receipt.json"
        target.write_text(json.dumps(entry, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return key, entry
    results = {}
    with ThreadPoolExecutor(max_workers=config["max_workers"]) as executor:
        futures = [executor.submit(acquire, spec) for spec in requests]
        for future in as_completed(futures):
            key, entry = future.result()
            results[key] = entry
            if len(results) % 40 == 0:
                print(f"Public raw source requests finished {len(results)}/247.", flush=True)
    failed = [k for k, r in results.items() if r["status"] == "FAILED_RAW_SOURCE_REQUEST"]
    gaps = [k for k, r in results.items() if r.get("inspection", {}).get("calendar_coverage_complete") is False]
    manifest = {"pipeline_version": VERSION, "status": "COMPLETE_RAW_REQUESTS_WITH_SOURCE_FAILURES" if failed else "COMPLETE_RAW_REQUESTS_MODEL_QC_PENDING",
        "protocol_sha256": file_sha256(protocol_path), "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "request_count": len(results), "failed_requests": failed, "calendar_gap_requests": gaps,
        "model_eligible_requests": 0, "outcome_analysis_enabled": False, "new_period_model_effects_evaluated": False,
        "all_source_failures_preserved": True, "no_missing_values_filled": True, "no_companies_deleted_or_replaced": True,
        "results": dict(sorted(results.items())),
        "artifacts": {p.name: file_sha256(p) for p in sorted(output.iterdir()) if p.is_file()},
        "limitations": config["limitations"]}
    (output / "raw_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return manifest
