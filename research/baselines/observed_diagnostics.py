"""Reuse audited numerical diagnostics with event-neutral historical reports.

The existing v1 runners have 2020 Wuhan-specific report prose. Their numerical
algorithms and pinned outputs remain unchanged; this adapter discards that prose
and records its own report-generation code alongside their input/code hashes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .activity_event_study import run_activity_event
from .placebo_dates import run_placebo
from ..data_pipeline.provenance import file_sha256

VERSION = "observed-diagnostic-report-v1"


def render_activity_report(result: dict[str, Any]) -> str:
    lines = ["# 历史事件窗口成交活动", "",
             "下表沿用已经核验的事件窗口和提供方逐日成交记录，以事件前估计期的日中位数作基期。事件日是按已声明的信息可见规则对齐的交易日。", "",
             "| 事件口径 | 股票 | 对齐交易日 | 估计期日数 | 当日成交量倍数 | 当日成交额倍数 | 窗口最高成交额倍数 |",
             "|---|---|---|---:|---:|---:|---:|"]
    for row in result["runs"]:
        lines.append(f"| {row['event_id']} | {row['stock_code']} | {row['event_date_used']} | "
                     f"{row['estimation_sessions']} | {row['event_day_volume_fold']:.3f} | "
                     f"{row['event_day_amount_fold']:.3f} | {row['window_max_amount_fold']:.3f} |")
    lines += ["", "成交量单位是股，成交额单位是人民币元；倍数反映双向总成交，无法区分买卖方向、投资者类别或盘口可执行深度。股票与事件是研究者选取的小样本，成交变化不能归因于单一政策，也不能用来校准价格冲击。", "",
              "逐日数值见 `event_activity.csv`，来源及代码哈希见 `diagnostic_manifest.json`。", ""]
    return "\n".join(lines)


def render_placebo_report(result: dict[str, Any]) -> str:
    diagnostic = result["diagnostic"]
    counts = diagnostic["excluded_or_retained_dates"]
    lines = ["# 历史事件日期对照", "",
             "在同一股票、市场模型和事件窗口规则下，逐日尝试其他事件日期。候选日期的估计期与事件窗口都不得触及实际事件窗口。", "",
             f"候选范围：{diagnostic['candidate_start_date']} 至 {diagnostic['candidate_end_date']}；"
             f"真实事件隔离区间：{diagnostic['blackout_start']} 至 {diagnostic['blackout_end']}。",
             f"事前候选日 {counts.get('before_actual_event', 0)} 个，"
             f"事后候选日 {counts.get('after_actual_event', 0)} 个；"
             f"窗口或拟合不完整 {counts.get('incomplete_windows_or_model_fit', 0)} 个，"
             f"隔离区间相交 {counts.get('overlaps_actual_event_estimation_or_window', 0)} 个。", "",
             "| 实际事件 | 股票/等权均值 | 实际 CAR | 事前绝对 CAR 排名比例 | 事后绝对 CAR 排名比例 |",
             "|---|---|---:|---:|---:|"]
    pairs = {(row["event_id"], row["stock_code_or_mean"], row["regime"]): row
             for row in result["comparisons"]}
    for event_id, code in dict.fromkeys((row["event_id"], row["stock_code_or_mean"])
                                        for row in result["comparisons"]):
        before = pairs[(event_id, code, "before_actual_event")]
        after = pairs[(event_id, code, "after_actual_event")]
        def rank(row: dict[str, Any]) -> str:
            value = row["absolute_rank_fraction"]
            return f"{value:.1%}（{row['candidate_dates']} 日）" if value is not None else "无可比日期"
        lines.append(f"| {event_id} | {code} | {before['actual_car']:.4f} | {rank(before)} | {rank(after)} |")
    lines += ["", "排名比例只是其他候选日期中绝对 CAR 不大于实际值的比例，不是 p 值。候选窗口互相重叠、股票收益相关，事前事后环境也不同；实际事件和股票非随机选择。因此不能把排名解读为显著性、因果效应或预测准确率。", "",
              "完整候选日期和各股结果见 `placebo_candidates.csv`；输入与代码哈希见 `diagnostic_manifest.json`。", ""]
    return "\n".join(lines)


def run_diagnostic(kind: str, config_path: Path, output_dir: Path) -> dict[str, Any]:
    if kind not in {"activity", "placebo"}:
        raise ValueError("kind must be activity or placebo")
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty diagnostic output directory")
    with tempfile.TemporaryDirectory() as tmp:
        temporary = Path(tmp)
        if kind == "activity":
            result = run_activity_event(config_path, temporary)
            source_manifest_path = temporary / "activity_event_manifest.json"
            artifacts = ("event_activity.json", "event_activity.csv")
            report_name = "event_activity_report.md"
            report_text = render_activity_report(result)
            source_id = result["analysis_id"]
        else:
            result = run_placebo(config_path, temporary)
            source_manifest_path = temporary / "placebo_manifest.json"
            artifacts = ("placebo_results.json", "placebo_candidates.csv")
            report_name = "placebo_report.md"
            report_text = render_placebo_report(result)
            source_id = result["diagnostic_id"]
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        for name, info in source_manifest["artifacts"].items():
            if file_sha256(temporary / name) != info["sha256"]:
                raise ValueError(f"numerical runner artifact changed: {name}")
        input_hashes = source_manifest.get("input_sha256", source_manifest.get("inputs"))
        if not isinstance(input_hashes, dict) or str(config_path) not in input_hashes:
            raise ValueError("numerical runner omitted required input hashes")
        if (file_sha256(config_path) != input_hashes[str(config_path)]
                or any(file_sha256(Path(path)) != digest for path, digest in input_hashes.items())):
            raise RuntimeError("diagnostic input changed while rendering report")
        if any(output_dir == Path(path) or output_dir in Path(path).parents or Path(path) in output_dir.parents
               for path in input_hashes):
            raise ValueError("diagnostic output must be separate from all inputs")
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_dir) as stage:
            staging = Path(stage)
            for name in artifacts:
                shutil.copyfile(temporary / name, staging / name)
            (staging / report_name).write_text(report_text, encoding="utf-8")
            manifest = {"pipeline_version": VERSION, "kind": kind,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "numerical_pipeline_version": source_manifest["pipeline_version"],
                        "numerical_run_id": source_id,
                        "input_sha256": input_hashes,
                        "code_sha256": {**source_manifest["code_sha256"],
                                        "observed_diagnostics.py": file_sha256(Path(__file__))},
                        "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()},
                        "interpretation": "Same audited numerical diagnostic with event-neutral reporting; descriptive, not causal or predictive."}
            (staging / "diagnostic_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            for path in staging.iterdir():
                path.replace(output_dir / path.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("activity", "placebo"))
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_diagnostic(args.kind, args.config, args.output_dir)
    print(json.dumps({"kind": result["kind"], "numerical_run_id": result["numerical_run_id"],
                      "artifacts": list(result["artifacts"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
