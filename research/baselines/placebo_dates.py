"""Enumerate other eligible event dates without treating ranks as statistical p-values."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .event_study import event_study
from .run_experiments import VERSION as EVENT_VERSION, _load_market
from ..data_pipeline.market_data import VERSION as MARKET_VERSION, strict_date
from ..data_pipeline.provenance import file_sha256

VERSION = "event-date-diagnostic-v1"
WINDOW_NAMES = ("estimation_window", "pre_event_gap", "window_before", "window_after")


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"run_id", "data_kind", "market_manifest", "event_results", "candidate_start_date",
                "candidate_end_date", "blackout_padding_sessions"}
    if not isinstance(config, dict) or set(config) != required or config["data_kind"] != "observed":
        raise ValueError("placebo configuration must declare exact observed inputs")
    for name in ("run_id", "market_manifest", "event_results"):
        if not isinstance(config[name], str) or not config[name].strip():
            raise ValueError(f"{name} must be nonempty")
    if strict_date(config["candidate_start_date"]) > strict_date(config["candidate_end_date"]):
        raise ValueError("candidate date range is reversed")
    padding = config["blackout_padding_sessions"]
    if type(padding) is not int or not 0 <= padding <= 30:
        raise ValueError("blackout_padding_sessions must be an integer from 0 to 30")
    return config


def blackout_bounds(actual_runs: list[dict[str, Any]], sessions: list[str], padding: int) -> tuple[str, str]:
    actual_days = {row["trade_date"] for run in actual_runs for row in run["abnormal_returns"]}
    if not actual_days or not actual_days <= set(sessions):
        raise ValueError("actual event windows must fall within the market session calendar")
    lower, upper = min(actual_days), max(actual_days)
    return sessions[max(0, sessions.index(lower) - padding)], sessions[min(len(sessions) - 1, sessions.index(upper) + padding)]


def enumerate_dates(groups, codes: list[str], actual_runs: list[dict[str, Any]],
                    settings: dict[str, Any], start: str, end: str, padding: int):
    """Require both estimation and event windows entirely outside the actual-event blackout."""
    if not codes or any(code not in groups for code in codes):
        raise ValueError("selected stock missing from prepared market")
    sessions = [row.trade_date.isoformat() for row in groups[codes[0]]]
    if any([row.trade_date.isoformat() for row in groups[code]] != sessions for code in codes):
        raise ValueError("placebo stocks need the same complete session calendar")
    if not all(type(settings.get(name)) is int and settings[name] >= 0 for name in WINDOW_NAMES):
        raise ValueError("event window settings are invalid")
    if settings["estimation_window"] < 20:
        raise ValueError("estimation window needs at least 20 sessions")
    blackout_start, blackout_end = blackout_bounds(actual_runs, sessions, padding)
    audit = Counter()
    candidates, daily = [], []
    for day in sessions:
        if not start <= day <= end:
            continue
        computed = {}
        try:
            for code in codes:
                computed[code] = event_study(groups[code], day, **{name: settings[name] for name in WINDOW_NAMES})
        except ValueError:
            audit["incomplete_windows_or_model_fit"] += 1
            continue
        first = min(r["estimation_start"] for r in computed.values())
        last = max(r["abnormal_returns"][-1]["trade_date"] for r in computed.values())
        if not (last < blackout_start or first > blackout_end):
            audit["overlaps_actual_event_estimation_or_window"] += 1
            continue
        regime = "before_actual_event" if last < blackout_start else "after_actual_event"
        cars = {code: computed[code]["cumulative_abnormal_return"] for code in codes}
        candidates.append({"candidate_date": day, "regime": regime, "mean_car": statistics.fmean(cars.values()),
                           "stock_cars": cars})
        for code in codes:
            result = computed[code]
            daily.append({"candidate_date": day, "regime": regime, "stock_code": code,
                          "car": result["cumulative_abnormal_return"], "alpha": result["alpha"], "beta": result["beta"],
                          "estimation_start": result["estimation_start"], "estimation_end": result["estimation_end"],
                          "window_start": result["abnormal_returns"][0]["trade_date"],
                          "window_end": result["abnormal_returns"][-1]["trade_date"]})
        audit[regime] += 1
    if not candidates or len(daily) != len(candidates) * len(codes):
        raise ValueError("no complete candidate dates outside actual-event blackout")
    return {"candidate_start_date": start, "candidate_end_date": end,
            "blackout_padding_sessions": padding,
            "blackout_start": blackout_start, "blackout_end": blackout_end,
            "excluded_or_retained_dates": dict(audit), "candidate_dates": candidates}, daily


def comparison(actual_runs: list[dict[str, Any]], candidates: list[dict[str, Any]], codes: list[str]) -> list[dict[str, Any]]:
    by_event = {}
    for run in actual_runs:
        event = by_event.setdefault(run["event_id"], {})
        if run["stock_code"] in event:
            raise ValueError("duplicate actual event and stock combination")
        event[run["stock_code"]] = run
    rows = []
    for event_id, stock_runs in by_event.items():
        if set(stock_runs) != set(codes):
            raise ValueError("actual event lacks a complete preselected stock panel")
        actual = {code: stock_runs[code]["cumulative_abnormal_return"] for code in codes}
        actual["equal_stock_mean"] = statistics.fmean(actual.values())
        for regime in ("before_actual_event", "after_actual_event"):
            reference = [r for r in candidates if r["regime"] == regime]
            for code, value in actual.items():
                values = [r["mean_car"] if code == "equal_stock_mean" else r["stock_cars"][code] for r in reference]
                rows.append({"event_id": event_id, "event_date_used": next(iter(stock_runs.values()))["event_date_used"],
                             "stock_code_or_mean": code, "regime": regime, "actual_car": value,
                             "candidate_dates": len(values),
                             "reference_abs_car_median": statistics.median(abs(v) for v in values) if values else None,
                             "absolute_rank_fraction": sum(abs(v) <= abs(value) for v in values) / len(values) if values else None})
    return rows


def render_report(result: dict[str, Any]) -> str:
    counts = result["diagnostic"]["excluded_or_retained_dates"]
    candidates = result["diagnostic"]["candidate_dates"]
    spans = {}
    for regime in ("before_actual_event", "after_actual_event"):
        dates = [row["candidate_date"] for row in candidates if row["regime"] == regime]
        spans[regime] = f"{min(dates)} 至 {max(dates)}" if dates else "无样本"
    lines = ["# 事件日期安慰剂诊断", "", "同一股票、相同市场模型与窗口参数下，逐个计算其他可用交易日的 CAR。候选日期的估计期与事件窗口都不得与真实事件窗口交叉。", "",
             f"候选日期范围：{result['diagnostic']['candidate_start_date']} 至 {result['diagnostic']['candidate_end_date']}；真实事件窗口两侧额外隔离 {result['diagnostic']['blackout_padding_sessions']} 个交易日。",
             f"真实事件隔离区间：{result['diagnostic']['blackout_start']} 至 {result['diagnostic']['blackout_end']}。",
             f"此前候选日期：{counts.get('before_actual_event', 0)}（{spans['before_actual_event']}）；此后候选日期：{counts.get('after_actual_event', 0)}（{spans['after_actual_event']}）。",
             f"窗口或模型不完整：{counts.get('incomplete_windows_or_model_fit', 0)} 日；与真实事件隔离区间相交：{counts.get('overlaps_actual_event_estimation_or_window', 0)} 日。", "",
             "| 实际对齐口径 | 股票/等权均值 | 实际 CAR | 前段绝对 CAR 排名比例 | 后段绝对 CAR 排名比例 |", "|---|---|---:|---:|---:|"]
    by_pair = {(r["event_id"], r["stock_code_or_mean"], r["regime"]): r for r in result["comparisons"]}
    events = list(dict.fromkeys(r["event_id"] for r in result["comparisons"]))
    codes = list(dict.fromkeys(r["stock_code_or_mean"] for r in result["comparisons"]))
    for event_id in events:
        for code in codes:
            before, after = (by_pair[(event_id, code, regime)] for regime in ("before_actual_event", "after_actual_event"))
            fmt = lambda v: f"{v * 100:.1f}%" if v is not None else "无样本"
            lines.append(f"| {event_id} | {code} | {before['actual_car']:.4f} | {fmt(before['absolute_rank_fraction'])} ({before['candidate_dates']}日) | {fmt(after['absolute_rank_fraction'])} ({after['candidate_dates']}日) |")
    lines += ["", "排名比例表示候选日期中绝对 CAR 不大于实际值的比例；不是 p 值或显著性检验。前后样本处于不同市场阶段，候选窗口在时间上重叠、股票收益相关，且原事件与样本均非随机抽取。没有可交换性或有效独立样本假设，不能由这些比例判断因果或预测能力。", "",
              "两个实际对齐口径指向同一武汉通告，不可将其作为两个独立成功案例。全部候选日期、股票 CAR 和估计窗口保存在 `placebo_candidates.csv`，输入哈希在 `placebo_manifest.json`。", ""]
    return "\n".join(lines)


def run_placebo(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config = load_config(config_path)
    market_manifest_path = (config_path.parent / config["market_manifest"]).resolve()
    results_path = (config_path.parent / config["event_results"]).resolve()
    experiment_manifest_path = results_path.parent / "experiment_manifest.json"
    market_csv = market_manifest_path.parent / "market_daily.csv"
    market_manifest = json.loads(market_manifest_path.read_text(encoding="utf-8"))
    experiment_manifest = json.loads(experiment_manifest_path.read_text(encoding="utf-8"))
    actual = json.loads(results_path.read_text(encoding="utf-8"))
    inputs = {p: file_sha256(p) for p in (config_path, market_manifest_path, market_csv,
                                          results_path, experiment_manifest_path)}
    if (market_manifest["pipeline_version"] != MARKET_VERSION or market_manifest["data_kind"] != "observed"
            or inputs[market_csv] != market_manifest["artifacts"]["market_daily.csv"]["sha256"]):
        raise ValueError("market version, kind or CSV hash differs from manifest")
    if (experiment_manifest["pipeline_version"] != EVENT_VERSION or actual["pipeline_version"] != EVENT_VERSION
            or experiment_manifest["status"] != "complete" or actual["status"] != "complete"
            or actual["data_kind"] != "observed" or inputs[results_path] != experiment_manifest["artifacts"]["event_results.json"]["sha256"]):
        raise ValueError("actual event results differ from complete observed experiment")
    if (experiment_manifest["market_dataset_id"] != market_manifest["market_dataset_id"]
            or experiment_manifest["inputs"]["market_manifest"]["sha256"] != inputs[market_manifest_path]
            or experiment_manifest["inputs"]["market_csv"]["sha256"] != inputs[market_csv]):
        raise ValueError("actual event and placebo market vintages differ")
    codes = actual["config"]["stock_codes"]
    expected = len(codes) * len(actual["config"]["events"])
    if len(actual["results"]) != expected or actual["failures"]:
        raise ValueError("actual event results lack the complete configured panel")
    groups = _load_market(market_csv, market_manifest)
    if set(codes) != set(groups):
        raise ValueError("placebo requires exactly the actual experiment's stock panel")
    diagnostic, detail = enumerate_dates(groups, codes, actual["results"], actual["config"]["windows"],
                                         config["candidate_start_date"], config["candidate_end_date"],
                                         config["blackout_padding_sessions"])
    comparisons = comparison(actual["results"], diagnostic["candidate_dates"], codes)
    if any(file_sha256(p) != h for p, h in inputs.items()):
        raise RuntimeError("placebo input changed during calculation")
    if any(output_dir == p or output_dir in p.parents for p in inputs):
        raise ValueError("placebo output must not contain input files")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty placebo output directory")
    code_hashes = {name: file_sha256(Path(__file__).with_name(name)) for name in
                   ("placebo_dates.py", "event_study.py", "run_experiments.py")}
    identity = {"inputs": {str(p): h for p, h in inputs.items()}, "code_sha256": code_hashes}
    result = {"pipeline_version": VERSION, "run_id": config["run_id"], "data_kind": "observed",
              "diagnostic_id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest(),
              "actual_experiment_id": actual["experiment_id"], "diagnostic": diagnostic,
              "comparisons": comparisons,
              "interpretation": "Descriptive absolute-CAR rank among other dates with uncontaminated windows; not a p-value, valid randomization inference, causal or forecasting claim."}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        with (staging / "placebo_candidates.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(detail[0]))
            writer.writeheader()
            writer.writerows(detail)
        (staging / "placebo_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "placebo_report.md").write_text(render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "diagnostic_id": result["diagnostic_id"],
                    "generated_at": datetime.now(timezone.utc).isoformat(), **identity,
                    "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "placebo_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_placebo(args.config, args.output_dir)
    print(json.dumps({"diagnostic_id": result["diagnostic_id"],
                      "candidate_counts": result["diagnostic"]["excluded_or_retained_dates"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
