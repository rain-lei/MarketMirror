"""Describe observed daily activity around already aligned event-study windows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..data_pipeline.market_activity import OUTPUT_FIELDS, VERSION as ACTIVITY_VERSION
from ..data_pipeline.market_data import read_rows, strict_date
from ..data_pipeline.provenance import file_sha256
from .run_experiments import VERSION as EVENT_VERSION

VERSION = "event-activity-v1"


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "data_kind", "activity_manifest", "event_results", "minimum_estimation_sessions"}
    if not isinstance(config, dict) or set(config) != required or config["data_kind"] != "observed":
        raise ValueError("event-activity config must declare observed inputs and exact fields")
    for name in ("run_id", "activity_manifest", "event_results"):
        if not isinstance(config[name], str) or not config[name].strip():
            raise ValueError(f"{name} must be nonempty")
    minimum = config["minimum_estimation_sessions"]
    if type(minimum) is not int or minimum < 20:
        raise ValueError("minimum_estimation_sessions must be an integer >=20")
    return config


def parse_activity(path: Path, manifest: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    calendar = manifest["session_dates"]
    if calendar != sorted(set(calendar)):
        raise ValueError("activity calendar must be sorted and unique")
    for _, row in read_rows(path, set(OUTPUT_FIELDS)):
        day, code = row["trade_date"], row["stock_code"]
        strict_date(day)
        if code not in {s.split(".")[1] for s in manifest["per_stock"]}:
            raise ValueError("activity row contains unlisted stock code")
        volume = int(row["volume_shares"])
        amount = Decimal(row["amount_cny"])
        if volume < 0 or not amount.is_finite() or amount < 0 or row["trading_status"] not in {"trading", "suspended"}:
            raise ValueError("invalid activity value")
        if (row["trading_status"] == "trading") != (volume > 0 and amount > 0):
            raise ValueError("activity status differs from volume/amount")
        expected_symbol = next(s for s in manifest["per_stock"] if s.split(".")[1] == code)
        if row["provider_symbol"] != expected_symbol or day in grouped.setdefault(code, {}):
            raise ValueError("activity symbol/date differs or duplicates")
        grouped[code][day] = {"volume_shares": volume, "amount_cny": amount, "trading_status": row["trading_status"]}
    if set(grouped) != {s.split(".")[1] for s in manifest["per_stock"]}:
        raise ValueError("activity stock set differs from manifest")
    for code, observations in grouped.items():
        if sorted(observations) != calendar:
            raise ValueError(f"activity calendar coverage differs for {code}")
    if sum(len(v) for v in grouped.values()) != manifest["counts"]["rows"]:
        raise ValueError("activity row count differs from manifest")
    return grouped


def summarize_run(run: dict[str, Any], activity: dict[str, dict[str, dict[str, Any]]],
                  minimum_sessions: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    code = run["stock_code"]
    if code not in activity:
        raise ValueError("event stock absent from activity data")
    series = activity[code]
    estimation_start, estimation_end = run["estimation_start"], run["estimation_end"]
    strict_date(estimation_start)
    strict_date(estimation_end)
    estimation_dates = [day for day in sorted(series) if estimation_start <= day <= estimation_end]
    if len(estimation_dates) != run["estimation_observations"] or len(estimation_dates) < minimum_sessions:
        raise ValueError("activity estimation dates differ from event study or are too few")
    event_rows = run["abnormal_returns"]
    expected_window = run["window_before"] + run["window_after"] + 1
    if len(event_rows) != expected_window:
        raise ValueError("event activity window differs from event study")
    window_dates = [entry["trade_date"] for entry in event_rows]
    if len(set(window_dates)) != len(window_dates) or window_dates != sorted(window_dates):
        raise ValueError("event activity window dates are repeated or unordered")
    if estimation_end >= window_dates[0] or run["event_date_used"] not in window_dates:
        raise ValueError("activity baseline overlaps event window or event date is absent")
    if [entry["relative_trade_day"] for entry in event_rows] != list(range(-run["window_before"], run["window_after"] + 1)):
        raise ValueError("relative session indices differ from event study")
    if next(entry["trade_date"] for entry in event_rows if entry["relative_trade_day"] == 0) != run["event_date_used"]:
        raise ValueError("event session does not match aligned event date")
    estimation = [series[day] for day in estimation_dates]
    base_volume = statistics.median(row["volume_shares"] for row in estimation)
    base_amount = statistics.median(row["amount_cny"] for row in estimation)
    if base_volume <= 0 or base_amount <= 0:
        raise ValueError("activity estimation median must be positive")
    normalized = []
    for entry in event_rows:
        day = entry["trade_date"]
        if day not in series:
            raise ValueError("event window lacks observed activity session")
        observation = series[day]
        normalized.append({"event_id": run["event_id"], "stock_code": code, "trade_date": day,
                           "relative_trade_day": entry["relative_trade_day"],
                           "abnormal_return": entry["abnormal_return"],
                           "volume_shares": observation["volume_shares"],
                           "amount_cny": str(observation["amount_cny"]),
                           "volume_to_estimation_median": float(Decimal(observation["volume_shares"]) / Decimal(str(base_volume))),
                           "amount_to_estimation_median": float(observation["amount_cny"] / base_amount),
                           "trading_status": observation["trading_status"]})
    event_day = next(row for row in normalized if row["relative_trade_day"] == 0)
    summary = {"event_id": run["event_id"], "stock_code": code, "event_date_used": run["event_date_used"],
               "event_date_requested": run["event_date_requested"], "visibility_alignment_rule": run["visibility_alignment_rule"],
               "estimation_start": estimation_start, "estimation_end": estimation_end,
               "estimation_sessions": len(estimation), "estimation_median_volume_shares": base_volume,
               "estimation_median_amount_cny": str(base_amount), "event_day_volume_fold": event_day["volume_to_estimation_median"],
               "event_day_amount_fold": event_day["amount_to_estimation_median"],
               "window_max_volume_fold": max(row["volume_to_estimation_median"] for row in normalized),
               "window_max_amount_fold": max(row["amount_to_estimation_median"] for row in normalized),
               "window_mean_amount_fold": statistics.fmean(row["amount_to_estimation_median"] for row in normalized),
               "cumulative_abnormal_return": run["cumulative_abnormal_return"],
               "event_type": run["event_definition"]["event_type"]}
    if any(not math.isfinite(v) for v in (summary["event_day_volume_fold"], summary["event_day_amount_fold"],
                                          summary["window_mean_amount_fold"])):
        raise ValueError("nonfinite normalized activity")
    return summary, normalized


def render_report(report: dict[str, Any]) -> str:
    lines = ["# 历史事件窗口成交活动初测", "", "样本是已对齐的历史事件研究股票；同一武汉通告的两个公开时刻口径是敏感性分析，不能当作两个独立事件。", "",
             "成交量单位为股，成交额为人民币元，来自 BaoStock 原始逐日响应并经来源文档核对。表中倍数以该股票、该事件窗口之前的估计期日中位数为基期。", "",
             "| 对齐口径 | 股票 | 实际事件日 | 估计期交易日 | 当日成交量倍数 | 当日成交额倍数 | 窗口最高成交额倍数 |", "|---|---|---|---:|---:|---:|---:|"]
    for row in report["runs"]:
        lines.append(f"| {row['event_id']} | {row['stock_code']} | {row['event_date_used']} | {row['estimation_sessions']} | {row['event_day_volume_fold']:.3f} | {row['event_day_amount_fold']:.3f} | {row['window_max_amount_fold']:.3f} |")
    lines += ["", "两种对齐中的 2020-02-03 是同一个交易日：日期保守口径的相对日 0，就是生效时间代理口径的相对日 +1。不能把这次峰值算作两个独立观测。", "",
              "成交额和成交量是**双向总成交**，无法区分买入/卖出压力，也不等于盘口可执行深度。本结果未校准价格冲击参数，没有做显著性或因果判断。当前提供方版本并非历史时点归档，事件可见时刻仍按原事件报告的两个假设处理。", "",
              "逐日相对交易日、异常收益和成交倍数见 `event_activity.csv`；输入来源、版本和文件哈希见 `activity_event_manifest.json`。", ""]
    return "\n".join(lines)


def run_activity_event(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config = load_config(config_path)
    activity_manifest_path = (config_path.parent / config["activity_manifest"]).resolve()
    event_results_path = (config_path.parent / config["event_results"]).resolve()
    event_manifest_path = event_results_path.parent / "experiment_manifest.json"
    activity_path = activity_manifest_path.parent / "market_activity.csv"
    activity_manifest = json.loads(activity_manifest_path.read_text(encoding="utf-8"))
    event_manifest = json.loads(event_manifest_path.read_text(encoding="utf-8"))
    events = json.loads(event_results_path.read_text(encoding="utf-8"))
    inputs = {p: file_sha256(p) for p in (config_path, activity_manifest_path, activity_path, event_results_path, event_manifest_path)}
    if (activity_manifest["pipeline_version"] != ACTIVITY_VERSION or activity_manifest["data_kind"] != "observed"
            or inputs[activity_path] != activity_manifest["artifacts"]["market_activity.csv"]["sha256"]):
        raise ValueError("activity version, basis or CSV hash differs from manifest")
    if (event_manifest["pipeline_version"] != EVENT_VERSION or events["pipeline_version"] != EVENT_VERSION
            or event_manifest["data_kind"] != "observed" or events["data_kind"] != "observed"
            or events["status"] != "complete" or event_manifest["status"] != "complete"
            or inputs[event_results_path] != event_manifest["artifacts"]["event_results.json"]["sha256"]):
        raise ValueError("event result version/status or hash differs from manifest")
    if activity_manifest["market_dataset_id"] != event_manifest["market_dataset_id"]:
        raise ValueError("event and activity inputs do not share the same prepared market vintage")
    if not events["results"] or len(events["results"]) != event_manifest["successful_runs"]:
        raise ValueError("event result count differs from complete experiment")
    activity = parse_activity(activity_path, activity_manifest)
    summaries, daily = [], []
    for run in events["results"]:
        summary, observations = summarize_run(run, activity, config["minimum_estimation_sessions"])
        summaries.append(summary)
        daily.extend(observations)
    if any(file_sha256(p) != h for p, h in inputs.items()):
        raise RuntimeError("event/activity input changed during analysis")
    if any(output_dir == p or output_dir in p.parents for p in inputs):
        raise ValueError("output directory must not contain event/activity inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty event-activity output directory")
    code_hash = {name: file_sha256(path) for name, path in (
        ("activity_event_study.py", Path(__file__)),
        ("market_activity.py", Path(__file__).parents[1] / "data_pipeline" / "market_activity.py"),
        ("provenance.py", Path(__file__).parents[1] / "data_pipeline" / "provenance.py"))}
    identity = {"input_sha256": {str(p): h for p, h in inputs.items()}, "code_sha256": code_hash}
    report = {"pipeline_version": VERSION, "run_id": config["run_id"], "data_kind": "observed",
              "analysis_id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest(),
              "event_experiment_id": events["experiment_id"], "activity_id": activity_manifest["activity_id"],
              "runs": summaries, "daily_rows": len(daily),
              "interpretation": "Descriptive gross trading activity relative to a prior estimation median; not directional order flow, liquidity depth, statistical significance or causal response."}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        with (staging / "event_activity.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(daily[0]))
            writer.writeheader()
            writer.writerows(daily)
        (staging / "event_activity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "event_activity_report.md").write_text(render_report(report), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "analysis_id": report["analysis_id"],
                    "generated_at": datetime.now(timezone.utc).isoformat(), **identity,
                    "activity_id": report["activity_id"], "event_experiment_id": report["event_experiment_id"],
                    "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "activity_event_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_activity_event(args.config, args.output_dir)
    print(json.dumps({"analysis_id": report["analysis_id"], "runs": len(report["runs"]),
                      "daily_rows": report["daily_rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
