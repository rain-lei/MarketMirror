"""Run configured, traced event studies against a validated market import."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import tempfile
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .event_study import DailyObservation, event_study
from ..data_pipeline.build_dataset import normalize_timestamp, valid_stock_code
from ..data_pipeline.market_data import VERSION as MARKET_VERSION, numeric, read_rows, strict_date
from ..data_pipeline.provenance import file_sha256

VERSION = "event-experiment-v1"
WINDOW_DEFAULTS = {"estimation_window": 120, "pre_event_gap": 5, "window_before": 3, "window_after": 5}


def load_experiment(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "market_manifest", "data_kind", "stock_codes", "events"}
    if not isinstance(config, dict) or required - config.keys() or config.keys() - required - {"windows", "evidence_manifest"}:
        raise ValueError("experiment has missing or unknown fields")
    if not isinstance(config["run_id"], str) or not config["run_id"].strip():
        raise ValueError("run_id is required")
    if config["data_kind"] not in ("synthetic", "observed") or not isinstance(config["market_manifest"], str):
        raise ValueError("experiment data kind or market manifest is invalid")
    codes = config["stock_codes"]
    if not isinstance(codes, list) or not codes or any(not isinstance(c, str) or valid_stock_code(c) != c for c in codes):
        raise ValueError("stock_codes must be a nonempty list of six-digit codes")
    if len(set(codes)) != len(codes):
        raise ValueError("duplicate experiment stock codes")
    windows = config.get("windows", {})
    if not isinstance(windows, dict) or windows.keys() - WINDOW_DEFAULTS.keys():
        raise ValueError("unknown event window fields")
    windows = {**WINDOW_DEFAULTS, **windows}
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in windows.values()) or windows["estimation_window"] < 20:
        raise ValueError("windows require nonnegative integers and at least 20 estimation observations")
    config["windows"] = windows
    if "evidence_manifest" in config and (not isinstance(config["evidence_manifest"], str) or not config["evidence_manifest"].strip()):
        raise ValueError("evidence_manifest must be a nonempty path")
    if not isinstance(config["events"], list) or not config["events"]:
        raise ValueError("events must be a nonempty list")
    event_ids = set()
    for event in config["events"]:
        required_event = {"event_id", "event_date", "event_type", "evidence_source"}
        optional = {"visible_at", "visible_date", "description"}
        if not isinstance(event, dict) or required_event - event.keys() or event.keys() - required_event - optional:
            raise ValueError("event has missing or unknown fields")
        for key in required_event | (event.keys() & optional):
            if not isinstance(event[key], str) or not event[key].strip():
                raise ValueError(f"event {key} must be a nonempty string")
        if event["event_id"] in event_ids:
            raise ValueError("duplicate event_id")
        event_ids.add(event["event_id"])
        strict_date(event["event_date"])
        if ("visible_at" in event) == ("visible_date" in event):
            raise ValueError("provide exactly one of visible_at or visible_date")
        if "visible_at" in event:
            value = normalize_timestamp(event["visible_at"])
            parsed = datetime.fromisoformat(event["visible_at"])
            if value is None or parsed.tzinfo is None:
                raise ValueError("visible_at requires an explicit timestamp with timezone")
        else:
            strict_date(event["visible_date"])
    return config


def visibility_anchor(event: dict[str, str]):
    nominal = strict_date(event["event_date"])
    if "visible_at" in event:
        visible = datetime.fromisoformat(normalize_timestamp(event["visible_at"]))
        ready = visible.date() + timedelta(days=int(visible.time() >= time(15, 0)))
        rule = "timestamp_before_15:00_same_date_else_following_date_Asia_Shanghai"
    else:
        ready = strict_date(event["visible_date"]) + timedelta(days=1)
        rule = "date_only_visible_from_following_midnight_Asia_Shanghai"
    return max(nominal, ready), rule


def _load_market(path: Path, manifest: dict[str, Any]) -> dict[str, list[DailyObservation]]:
    groups = defaultdict(list)
    seen = set()
    market_by_day = {}
    calendar = [strict_date(d) for d in manifest["session_dates"]]
    if len(calendar) != len(set(calendar)) or calendar != sorted(calendar):
        raise ValueError("market manifest calendar must be unique and ordered")
    for _, row in read_rows(path, {"trade_date", "stock_code", "benchmark_id", "stock_return", "market_return"}):
        code, day = row["stock_code"], strict_date(row["trade_date"])
        if valid_stock_code(code) != code or row["benchmark_id"] != manifest["settings"]["benchmark_id"]:
            raise ValueError("prepared market identity differs from its manifest")
        if (code, day) in seen:
            raise ValueError("duplicate prepared market session")
        seen.add((code, day))
        sr, mr = numeric(row["stock_return"], "returns", "decimal"), numeric(row["market_return"], "returns", "decimal")
        if day in market_by_day and market_by_day[day] != mr:
            raise ValueError("benchmark return differs across stocks on one session")
        market_by_day[day] = mr
        groups[code].append(DailyObservation(day, sr, mr))
    if set(groups) != set(manifest["series"]):
        raise ValueError("prepared stock codes differ from manifest")
    for code, rows in groups.items():
        rows.sort(key=lambda r: r.trade_date)
        dates = [r.trade_date for r in rows]
        expected = [d for d in calendar if dates[0] <= d <= dates[-1]]
        info = manifest["series"][code]
        if dates != expected or len(rows) != info["return_rows"] or dates[0].isoformat() != info["first_return_date"] or dates[-1].isoformat() != info["last_return_date"]:
            raise ValueError("prepared market sessions differ from calendar or manifest")
    if sum(len(v) for v in groups.values()) != manifest["counts"]["market_rows"]:
        raise ValueError("prepared market counts differ from manifest")
    return groups


def run_experiments(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config_hash = file_sha256(config_path)
    config = load_experiment(config_path)
    market_manifest_path = (config_path.parent / config["market_manifest"]).resolve()
    market_manifest_hash = file_sha256(market_manifest_path)
    market_manifest = json.loads(market_manifest_path.read_text(encoding="utf-8"))
    if market_manifest["pipeline_version"] != MARKET_VERSION or market_manifest["data_kind"] != config["data_kind"]:
        raise ValueError("market version or synthetic/observed classification differs from experiment")
    market_path = market_manifest_path.parent / "market_daily.csv"
    market_hash = file_sha256(market_path)
    if market_hash != market_manifest["artifacts"]["market_daily.csv"]["sha256"]:
        raise ValueError("market CSV hash differs from import manifest")
    groups = _load_market(market_path, market_manifest)
    evidence_inputs = []
    if "evidence_manifest" in config:
        evidence_path = (config_path.parent / config["evidence_manifest"]).resolve()
        evidence_hash = file_sha256(evidence_path)
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(evidence.get("sources"), list) or not evidence["sources"]:
            raise ValueError("evidence manifest requires source records")
        urls = set()
        evidence_inputs.append({"path": str(evidence_path), "sha256": evidence_hash, "role": "evidence_manifest"})
        for item in evidence["sources"]:
            if not isinstance(item, dict) or not {"url", "path", "sha256"} <= item.keys():
                raise ValueError("evidence source requires url, path and sha256")
            if not all(isinstance(item[k], str) and item[k] for k in ("url", "path", "sha256")) or item["url"] in urls:
                raise ValueError("evidence source identity is invalid or duplicated")
            urls.add(item["url"])
            local_path = (evidence_path.parent / item["path"]).resolve()
            if file_sha256(local_path) != item["sha256"]:
                raise ValueError("evidence file hash differs from evidence manifest")
            evidence_inputs.append({"url": item["url"], "path": str(local_path), "sha256": item["sha256"], "role": "cached_source"})
        if any(event["evidence_source"] not in urls for event in config["events"]):
            raise ValueError("event evidence URL is absent from evidence manifest")
    if set(config["stock_codes"]) - set(groups):
        raise ValueError("experiment stock code is absent from prepared market data")
    names = ["event_results.json", "event_abnormal_returns.csv", "event_report.md", "experiment_manifest.json"]
    protected = {config_path, market_manifest_path, market_path}
    protected.update(Path(v["path"]).resolve() for v in market_manifest["inputs"].values())
    protected.add(Path(market_manifest["config_path"]).resolve())
    protected.update(Path(item["path"]) for item in evidence_inputs)
    if any(output_dir / name in protected for name in names):
        raise ValueError("experiment output must not overwrite input data or metadata")
    results, failures = [], []
    for event in config["events"]:
        target, rule = visibility_anchor(event)
        for code in config["stock_codes"]:
            try:
                result = event_study(groups[code], target, **config["windows"])
                json.dumps(result, allow_nan=False)
                result.update({"event_id": event["event_id"], "stock_code": code, "event_definition": event,
                               "visibility_anchor_date": target.isoformat(), "visibility_alignment_rule": rule})
                results.append(result)
            except (ValueError, OverflowError) as exc:
                failures.append({"event_id": event["event_id"], "stock_code": code, "reason": str(exc)})
    status = "complete" if not failures else "partial" if results else "failed"
    aggregates = []
    for event in config["events"]:
        subset = [r for r in results if r["event_id"] == event["event_id"]]
        complete = len(subset) == len(config["stock_codes"])
        aggregates.append({"event_id": event["event_id"], "requested_stock_codes": len(config["stock_codes"]),
                           "successful_stock_codes": len(subset), "complete_coverage": complete,
                           "mean_cumulative_abnormal_return": sum(r["cumulative_abnormal_return"] for r in subset) / len(subset) if complete else None})
    recheck = [(config_path, config_hash), (market_manifest_path, market_manifest_hash), (market_path, market_hash)]
    recheck += [(Path(item["path"]), item["sha256"]) for item in evidence_inputs]
    for path, expected in recheck:
        if file_sha256(path) != expected:
            raise RuntimeError("experiment input changed during execution")
    code_hashes = {
        "run_experiments.py": file_sha256(Path(__file__)),
        "event_study.py": file_sha256(Path(__file__).with_name("event_study.py")),
        "market_data.py": file_sha256(Path(__file__).resolve().parents[1] / "data_pipeline" / "market_data.py"),
        "build_dataset.py": file_sha256(Path(__file__).resolve().parents[1] / "data_pipeline" / "build_dataset.py"),
        "provenance.py": file_sha256(Path(__file__).resolve().parents[1] / "data_pipeline" / "provenance.py"),
    }
    run_payload = json.dumps([config, config_hash, market_manifest_hash, market_hash, code_hashes, evidence_inputs], sort_keys=True, ensure_ascii=False)
    report = {"pipeline_version": VERSION, "run_id": config["run_id"],
              "experiment_id": hashlib.sha256(run_payload.encode()).hexdigest(),
              "generated_at": datetime.now(timezone.utc).isoformat(), "data_kind": config["data_kind"], "status": status,
              "config": config, "results": results, "failures": failures, "event_aggregates": aggregates,
              "evidence_inputs": evidence_inputs}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        (staging / names[0]).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        with (staging / names[1]).open("w", encoding="utf-8-sig", newline="") as handle:
            fields = ["event_id", "stock_code", "trade_date", "relative_trade_day", "abnormal_return"]
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for result in results:
                for observation in result["abnormal_returns"]:
                    writer.writerow({"event_id": result["event_id"], "stock_code": result["stock_code"], **observation})
        lines = ["# Event study report", "", f"Data kind: **{config['data_kind']}**; run status: **{status}**.",
                 "Synthetic fixture only. These results do not establish historical fit, forecasting ability or Agent behavior." if config["data_kind"] == "synthetic" else
                 "Descriptive market-model abnormal returns; no causal, significance or forecasting claim.", "",
                 "| Event | Stock | Session used | Alpha | Beta | CAR (decimal) |", "|---|---|---|---:|---:|---:|"]
        for result in results:
            event_id = result["event_id"].replace("|", "/").replace("\n", " ")
            lines.append(f"| {event_id} | {result['stock_code']} | {result['event_date_used']} | {result['alpha']:.8f} | {result['beta']:.8f} | {result['cumulative_abnormal_return']:.8f} |")
        lines += ["", "## Interpretation and limits", "",
                  "- Window lengths count calendar-aligned trading sessions; estimation and event windows are disjoint.",
                  "- Date-only public visibility starts the next day; timestamps at/after 15:00 +08:00 start the next day.",
                  "- The later of nominal event date and visibility-ready date anchors the first available session.",
                  "- An intraday announcement uses the day's close-to-close return, which also includes pre-announcement movement.",
                  "- CAR sums abnormal simple returns; it is not a compounded portfolio return.",
                  "- No p-values or confidence intervals are reported; correlated events/stocks and model misspecification remain untested.",
                  "- Other shocks inside estimation windows can bias fitted alpha/beta; overlapping or contaminated windows are not automatically corrected.",
                  "- An event mean is unavailable when any preselected stock fails, avoiding a silently reduced sample.", "", "## Failures", ""]
        lines += [f"- {f['event_id']!r} / {f['stock_code']}: {f['reason']}" for f in failures] or ["None."]
        lines += ["", "## Event definitions and evidence", ""]
        for event in config["events"]:
            lines.append(f"- {event['event_id']}: {event.get('description', '')} Source: {event['evidence_source']}")
        (staging / names[2]).write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest = {
            "pipeline_version": VERSION, "experiment_id": report["experiment_id"], "data_kind": config["data_kind"],
            "status": status, "successful_runs": len(results), "failed_runs": len(failures),
            "inputs": {"config": {"path": str(config_path), "sha256": config_hash},
                       "market_manifest": {"path": str(market_manifest_path), "sha256": market_manifest_hash},
                       "market_csv": {"path": str(market_path), "sha256": market_hash}},
            "market_dataset_id": market_manifest["market_dataset_id"], "market_import_settings": market_manifest["settings"],
            "market_source_inputs": market_manifest["inputs"], "code_sha256": code_hashes, "python_version": platform.python_version(),
            "evidence_inputs": evidence_inputs,
            "session_close_assumption": "15:00 Asia/Shanghai; timestamps at or after close align from the following date.",
            "artifacts": {name: {"sha256": file_sha256(staging / name)} for name in names[:-1]},
        }
        (staging / names[3]).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for name in names:
            (staging / name).replace(output_dir / name)
    return report


def main():
    parser = argparse.ArgumentParser(description="Run traced market-model event experiments")
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_experiments(args.config, args.output_dir)
    print(json.dumps({"status": report["status"], "successful_runs": len(report["results"]), "failed_runs": len(report["failures"])}))
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
