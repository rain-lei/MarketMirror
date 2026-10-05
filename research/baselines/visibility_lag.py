"""Sensitivity of the fixed 2020 text baseline to unobserved publication delay."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .prediction_panel import Question, TEXT_FEATURES, load_panel_inputs, load_prediction_config, make_panel
from .run_experiments import _load_market
from .run_prediction import evaluate_panel
from ..data_pipeline.build_dataset import SOURCE_TIMEZONE
from ..data_pipeline.market_data import strict_date
from ..data_pipeline.provenance import file_sha256
from ..registry.verify_catalog import audit_run, load_catalog

VERSION = "text-visibility-lag-v1"
ROOT = Path(__file__).resolve().parents[2]
BASELINE_CATALOG = ROOT / "research/configs/integrity_catalog_2020.json"
CONFIG = ROOT / "research/configs/visibility_lag_2020.json"


def shift_timestamp(value: str | None, days: int) -> str | None:
    if value is None:
        return None
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() != SOURCE_TIMEZONE.utcoffset(None):
        raise ValueError("QA timestamp must use the source timezone")
    return (moment + timedelta(days=days)).isoformat(timespec="microseconds")


def delayed_questions(records: dict[str, list[Question]], days: int) -> dict[str, list[Question]]:
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError("publication delay must be nonnegative whole calendar days")
    return {code: sorted((replace(q, question_available_at=shift_timestamp(q.question_available_at, days),
                                  reply_available_at=shift_timestamp(q.reply_available_at, days))
                          for q in rows), key=lambda q: q.question_available_at)
            for code, rows in records.items()}


def read_source_questions(config_path: Path, baseline: dict[str, Any],
                          expected_counts: dict[str, int]) -> dict[str, list[Question]]:
    """Repeat the baseline's source/coverage SQL on the already-audited database."""
    database = (config_path.parent / baseline["qa_database"]).resolve()
    coverage = baseline["qa_coverage"]
    start = datetime.combine(strict_date(coverage["start_date"]), datetime.min.time(),
                             tzinfo=SOURCE_TIMEZONE).isoformat(timespec="microseconds")
    end = datetime.combine(strict_date(coverage["end_date"]) + timedelta(days=1),
                           datetime.min.time(), tzinfo=SOURCE_TIMEZONE).isoformat(timespec="microseconds")
    codes = baseline["stock_codes"]
    questions = {code: [] for code in codes}
    query = ("SELECT stock_code, question_available_at, question_text, reply_available_at, "
             "reply_text, reply_eligible FROM qa_record WHERE question_eligible=1 "
             "AND source_file_hash=? AND stock_code IN (" + ",".join("?" for _ in codes) + ") "
             "AND question_available_at>=? AND question_available_at<? "
             "ORDER BY stock_code, question_available_at, qa_id")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        for code, qt, text, rt, reply, eligible in conn.execute(
                query, [baseline["qa_source_sha256"], *codes, start, end]):
            questions[code].append(Question(qt, text, rt, reply, bool(eligible)))
    if {code: len(rows) for code, rows in questions.items()} != expected_counts:
        raise ValueError("sensitivity question selection differs from baseline")
    return questions


def load_sensitivity_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(config, dict)
            or set(config) != {"run_id", "source_run_id", "lag_days", "lag_unit", "interpretation"}
            or config["run_id"] != "text_publication_delay_sensitivity_2020"
            or config["source_run_id"] != "text_prediction"
            or config["lag_unit"] != "calendar_days"
            or not isinstance(config["interpretation"], str)
            or not config["interpretation"].strip()
            or not isinstance(config["lag_days"], list)
            or config["lag_days"] != [0, 1, 3, 7]):
        raise ValueError("visibility sensitivity requires the fixed 0/1/3/7-day grid")
    return config


def summarize(evaluation: dict[str, Any], lag_days: int, changed_rows: int,
              question_count_sum: int, reply_count_sum: int) -> dict[str, Any]:
    market = evaluation["test_metrics"]["market_only"]["pooled"]
    text = evaluation["test_metrics"]["market_plus_text"]["pooled"]
    paired = evaluation["paired_mae_difference"]
    return {"lag_days": lag_days, "changed_text_feature_rows": changed_rows,
            "panel_question_count_sum": question_count_sum,
            "panel_known_reply_count_sum": reply_count_sum,
            "market_test_mae": market["mae"], "text_test_mae": text["mae"],
            "text_test_rmse": text["rmse"], "test_rows": text["rows"],
            "paired_mae_difference": paired["mean"],
            "paired_interval_95": paired["interval_95"],
            "selected_text_lambda": evaluation["validation_grid"]["market_plus_text"]["selected_lambda"]}


def render_report(result: dict[str, Any]) -> str:
    lines = ["# 问答可见时间延迟敏感性", "",
             "只对同一 2020 年三股文本预测试验施加额外 0、1、3、7 个**自然日**的假设公开延迟；提问和回复各自的来源可用时间同步后移。",
             "这不是观测到的真实发布时间或延迟分布，也不是在测试期选出的最优参数。0 日重算必须逐项复现原基线，纯行情预测在所有情景中必须相同。", "",
             f"样本面板 {result['panel_rows']} 行；固定测试 {result['test_rows']} 行。日收益目标、股票、切分、训练与验证规则保持一致；每种延迟均只在验证集选择 Ridge 惩罚项。", "",
             "问题与回复计数是逐行 30 日滚动窗口的总和；同一问答会在多行重复计入，不是唯一问答数。", "",
             "| 额外延迟 | 文本特征改变的面板行 | 滚动问题计数总和 | 滚动已知回复计数总和 | 行情 MAE | 行情＋文本 MAE | 文本－行情 MAE 差（百分点） | 近似 95% 区间（百分点） | 文本 λ |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["scenarios"]:
        lo, hi = row["paired_interval_95"]
        lines.append(f"| {row['lag_days']} 日 | {row['changed_text_feature_rows']} | "
                     f"{row['panel_question_count_sum']} | {row['panel_known_reply_count_sum']} | "
                     f"{100 * row['market_test_mae']:.4f} | {100 * row['text_test_mae']:.4f} | "
                     f"{100 * row['paired_mae_difference']:+.4f} | "
                     f"[{100 * lo:+.4f}, {100 * hi:+.4f}] | {row['selected_text_lambda']:g} |")
    lines += ["", "误差以收益率百分点表示；正的文本－行情差表示加入文本更差。区间是固定测试期的 5 交易日区块重抽样近似诊断，不包含数据源修订、样本选择或真实公开时刻的不确定性。", "",
              "当前只有来源问答时间，缺少首次公开日志。若某个延迟结果看似改善，也不能据此反推实际发布时间或证明预测能力；该试验没有 LLM 信号、Agent 校准或真实市场冲击识别。", ""]
    return "\n".join(lines)


def run_sensitivity(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config = load_sensitivity_config(config_path)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty sensitivity output directory")
    catalog = load_catalog(BASELINE_CATALOG)
    matches = [run for run in catalog if run["run_id"] == config["source_run_id"]]
    if len(matches) != 1 or audit_run(matches[0], BASELINE_CATALOG.parent, ROOT / "research",
                                       ROOT / "research_outputs", {})["status"] != "passed":
        raise ValueError("pinned baseline prediction failed source integrity audit")
    baseline_manifest_path = (BASELINE_CATALOG.parent / matches[0]["manifest"]).resolve()
    baseline_result_path = baseline_manifest_path.parent / "prediction_results.json"
    baseline_result = json.loads(baseline_result_path.read_text(encoding="utf-8"))
    source_config_path = ROOT / "research/configs/text_pilot_2020.json"
    baseline_config = load_prediction_config(source_config_path)
    base_panel, source_inputs, provenance, protected = load_panel_inputs(source_config_path, baseline_config)
    if (baseline_result["config"] != baseline_config
            or baseline_result["panel_rows"] != len(base_panel)
            or baseline_result["provenance"] != provenance):
        raise ValueError("baseline result differs from current audited source inputs")
    if any(path == output_dir or output_dir in path.parents for path in set(source_inputs) | protected):
        raise ValueError("sensitivity output directory must not contain source inputs")
    questions = read_source_questions(source_config_path, baseline_config,
                                      provenance["qa_questions_by_stock"])
    market_manifest_path = (source_config_path.parent / baseline_config["market_manifest"]).resolve()
    market_manifest = json.loads(market_manifest_path.read_text(encoding="utf-8"))
    market = _load_market(market_manifest_path.parent / "market_daily.csv", market_manifest)
    scenarios = []
    original_market_predictions = None
    original_market_model = None
    test_rows = None
    for days in config["lag_days"]:
        panel = make_panel(market, delayed_questions(questions, days), baseline_config)
        if len(panel) != len(base_panel) or any(
                (row["stock_code"], row["as_of"], row["target_return"])
                != (original["stock_code"], original["as_of"], original["target_return"])
                for row, original in zip(panel, base_panel)):
            raise ValueError("sensitivity changed sample identity or target")
        if days == 0 and panel != base_panel:
            raise ValueError("zero-delay panel differs from pinned baseline construction")
        changed = sum(any(row[name] != original[name] for name in TEXT_FEATURES)
                      for row, original in zip(panel, base_panel))
        evaluation, predictions = evaluate_panel(panel, baseline_config)
        if days == 0 and evaluation != baseline_result["evaluation"]:
            raise ValueError("zero-delay evaluation does not reproduce pinned baseline")
        current_market_predictions = [(p["stock_code"], p["as_of"], p["market_only"])
                                      for p in predictions]
        if original_market_predictions is None:
            original_market_predictions = current_market_predictions
            original_market_model = evaluation["models"]["market_only"]
            test_rows = len(predictions)
        elif (current_market_predictions != original_market_predictions
              or evaluation["models"]["market_only"] != original_market_model):
            raise ValueError("publication delay changed the market-only control")
        scenarios.append(summarize(evaluation, days, changed,
                                   sum(row["question_count"] for row in panel),
                                   sum(row["known_reply_count"] for row in panel)))
    result = {"pipeline_version": VERSION, "run_id": config["run_id"],
              "source_run_id": config["source_run_id"], "data_kind": baseline_config["data_kind"],
              "delay_unit": config["lag_unit"], "panel_rows": len(base_panel), "test_rows": test_rows,
              "baseline_reproduced": True, "market_only_invariant": True,
              "scenarios": scenarios}
    input_paths = set(source_inputs) | {config_path, BASELINE_CATALOG, baseline_manifest_path,
                                        baseline_result_path}
    inputs = {str(path): file_sha256(path) for path in sorted(input_paths)}
    code_paths = [Path(__file__), Path(__file__).with_name("prediction_panel.py"),
                  Path(__file__).with_name("run_prediction.py")]
    code_hashes = {path.name: file_sha256(path) for path in code_paths}
    if any(file_sha256(path) != digest for path, digest in source_inputs.items()):
        raise RuntimeError("source changed during visibility sensitivity run")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        (staging / "visibility_lag_results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "visibility_lag_report.md").write_text(render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "config_sha256": file_sha256(config_path),
                    "source_run_id": config["source_run_id"], "inputs": inputs,
                    "code_sha256": code_hashes,
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()},
                    "assumptions": [config["interpretation"],
                                    "No original publication logs; source timestamp remains a proxy.",
                                    "No test labels used to select delays or the model penalty."]}
        (staging / "visibility_lag_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_sensitivity(args.config, args.output_dir)
    print(json.dumps({"run_id": result["run_id"], "scenarios": len(result["scenarios"]),
                      "baseline_reproduced": result["baseline_reproduced"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
