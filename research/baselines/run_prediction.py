"""Frozen chronological holdout: market ridge versus market plus visible QA text."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import random
import statistics
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .prediction_panel import MARKET_FEATURES, TEXT_FEATURES, load_panel_inputs, load_prediction_config
from ..data_pipeline.market_data import strict_date
from ..data_pipeline.provenance import file_sha256

VERSION = "text-prediction-v1"


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Partial-pivot elimination of the small, positive-definite ridge system."""
    n = len(vector)
    augmented = [list(row) + [v] for row, v in zip(matrix, vector)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        diagonal = augmented[col][col]
        if abs(diagonal) < 1e-14:
            raise ValueError("numerically singular ridge system")
        for row in range(col + 1, n):
            ratio = augmented[row][col] / diagonal
            for j in range(col, n + 1):
                augmented[row][j] -= ratio * augmented[col][j]
    coefficients = [0.0] * n
    for row in range(n - 1, -1, -1):
        coefficients[row] = (augmented[row][n] - sum(augmented[row][j] * coefficients[j] for j in range(row + 1, n))) / augmented[row][row]
    return coefficients


def fit_ridge(rows: list[dict[str, Any]], columns: list[str], penalty: float) -> dict[str, Any]:
    if len(rows) < 20 or not columns or penalty <= 0 or not math.isfinite(penalty):
        raise ValueError("ridge requires at least 20 training rows, features and positive finite penalty")
    x = [[float(row[c]) for c in columns] for row in rows]
    y = [float(row["target_return"]) for row in rows]
    if any(not math.isfinite(v) for values in x + [y] for v in values):
        raise ValueError("nonfinite model input")
    means = [statistics.fmean(values) for values in zip(*x)]
    scales = [statistics.pstdev(values) or 1.0 for values in zip(*x)]
    z = [[(v - m) / s for v, m, s in zip(values, means, scales)] for values in x]
    intercept = statistics.fmean(y)
    p, n = len(columns), len(rows)
    gram = [[sum(values[i] * values[j] for values in z) / n + (penalty if i == j else 0.0)
             for j in range(p)] for i in range(p)]
    rhs = [sum(values[j] * (target - intercept) for values, target in zip(z, y)) / n for j in range(p)]
    coefficients = solve(gram, rhs)
    if any(not math.isfinite(v) for v in coefficients):
        raise ValueError("nonfinite fitted coefficient")
    return {"columns": columns, "means": means, "scales": scales, "coefficients": coefficients,
            "intercept": intercept, "lambda": penalty, "training_rows": n,
            "last_training_target_available_at": max(row["target_available_at"] for row in rows),
            "objective": "mean squared training error + lambda * squared standardized coefficients; intercept unpenalized"}


def predict(model: dict[str, Any], row: dict[str, Any]) -> float:
    result = model["intercept"] + sum(coefficient * (float(row[col]) - mean) / scale for col, mean, scale, coefficient in
                                      zip(model["columns"], model["means"], model["scales"], model["coefficients"]))
    if not math.isfinite(result):
        raise ValueError("nonfinite prediction")
    return result


def metrics(targets: list[float], predictions: list[float]) -> dict[str, Any]:
    if not targets or len(targets) != len(predictions) or any(not math.isfinite(v) for v in targets + predictions):
        raise ValueError("metrics need aligned finite predictions and targets")
    errors = [p - y for p, y in zip(predictions, targets)]
    sign = lambda value: (value > 0) - (value < 0)
    return {"rows": len(targets), "mae": statistics.fmean(abs(v) for v in errors),
            "rmse": math.sqrt(statistics.fmean(v * v for v in errors)),
            "directional_accuracy": (statistics.fmean(sign(y) == sign(p) for y, p in zip(targets, predictions))
                                     if any(p != 0 for p in predictions) else None)}


def chronological_split(rows: list[dict[str, Any]], config: dict[str, Any]):
    groups, audit = {}, {}
    assigned = set()
    for name in ("train", "validation", "test"):
        limits = config["splits"][name]
        start, end = strict_date(limits["start_date"]), strict_date(limits["end_date"])
        selected = [r for r in rows if start <= strict_date(r["trade_date"]) <= end]
        if not selected:
            raise ValueError(f"empty {name} split")
        keys = {(r["stock_code"], r["as_of"]) for r in selected}
        if len(keys) != len(selected) or assigned & keys:
            raise ValueError("duplicate or overlapping split rows")
        assigned.update(keys)
        by_day = defaultdict(set)
        for row in selected:
            by_day[row["as_of"]].add(row["stock_code"])
            if row["target_available_at"] <= row["as_of"]:
                raise ValueError("prediction target must become available strictly after features")
        if any(codes != set(config["stock_codes"]) for codes in by_day.values()):
            raise ValueError("split does not contain every selected stock on every session")
        groups[name] = selected
    if len(assigned) != len(rows):
        raise ValueError("every panel row must belong to one configured split")
    for name, next_name in (("train", "validation"), ("validation", "test")):
        boundary = min(r["as_of"] for r in groups[next_name])
        before = groups[name]
        groups[name] = [r for r in before if r["target_available_at"] < boundary]
        audit[name] = {"rows_before_purge": len(before), "purged_rows": len(before) - len(groups[name]),
                       "next_split_first_as_of": boundary}
        if not groups[name] or max(r["as_of"] for r in groups[name]) >= boundary:
            raise ValueError("invalid chronological partition")
    for name, selected in groups.items():
        audit.setdefault(name, {"rows_before_purge": len(selected), "purged_rows": 0})
        audit[name].update({"rows": len(selected), "sessions": len({r["as_of"] for r in selected}),
                            "first_as_of": min(r["as_of"] for r in selected), "last_as_of": max(r["as_of"] for r in selected),
                            "last_target_available_at": max(r["target_available_at"] for r in selected)})
    return groups, audit


def evaluate_panel(rows: list[dict[str, Any]], config: dict[str, Any]):
    groups, audit = chronological_split(rows, config)
    train, validation, test = (groups[name] for name in ("train", "validation", "test"))
    identity_columns = [f"stock_is_{code}" for code in config["stock_codes"][1:]]
    feature_sets = {"market_only": MARKET_FEATURES + identity_columns,
                    "market_plus_text": MARKET_FEATURES + identity_columns + TEXT_FEATURES}
    model_records, grid, predictions = {}, {}, []
    for name, columns in feature_sets.items():
        scores = []
        for penalty in sorted(config["ridge_lambdas"]):
            model = fit_ridge(train, columns, penalty)
            score = metrics([r["target_return"] for r in validation], [predict(model, r) for r in validation])
            scores.append({"lambda": penalty, **score})
        # Fixed validation RMSE criterion; exact ties favor stronger regularization.
        chosen = min(scores, key=lambda score: (score["rmse"], -score["lambda"]))
        frozen = fit_ridge(train + validation, columns, chosen["lambda"])
        first_test = min(r["as_of"] for r in test)
        if frozen["last_training_target_available_at"] >= first_test:
            raise ValueError("model fitted using labels not available before first test prediction")
        model_records[name] = frozen
        grid[name] = {"selected_lambda": chosen["lambda"], "criterion": "minimum validation RMSE", "scores": scores}
    historical_means = {code: statistics.fmean(r["target_return"] for r in train + validation if r["stock_code"] == code)
                        for code in config["stock_codes"]}
    for row in test:
        prediction = {k: row[k] for k in ("stock_code", "as_of", "target_date", "target_available_at", "target_return")}
        prediction.update({name: predict(model, row) for name, model in model_records.items()})
        prediction.update({"zero_return": 0.0, "historical_mean": historical_means[row["stock_code"]]})
        predictions.append(prediction)
    results = {}
    for name in ("zero_return", "historical_mean", *feature_sets):
        results[name] = {"pooled": metrics([p["target_return"] for p in predictions], [p[name] for p in predictions]),
                         "by_stock": {code: metrics([p["target_return"] for p in predictions if p["stock_code"] == code],
                                                    [p[name] for p in predictions if p["stock_code"] == code])
                                      for code in config["stock_codes"]}}
    daily_loss = defaultdict(list)
    for p in predictions:
        daily_loss[p["as_of"]].append(abs(p["market_plus_text"] - p["target_return"]) - abs(p["market_only"] - p["target_return"]))
    paired = block_bootstrap([statistics.fmean(daily_loss[day]) for day in sorted(daily_loss)], config["bootstrap"])
    return {"split_audit": audit, "validation_grid": grid, "models": model_records,
            "historical_means": historical_means, "test_metrics": results, "paired_mae_difference": paired}, predictions


def block_bootstrap(values: list[float], settings: dict[str, int]) -> dict[str, Any]:
    """Circular moving blocks over dates; stock losses were averaged within each date."""
    n, block = len(values), settings["block_sessions"]
    if n < 2 * block or any(not math.isfinite(v) for v in values):
        raise ValueError("test dates need at least two finite bootstrap blocks")
    rng = random.Random(settings["seed"])
    bootstrap = []
    for _ in range(settings["replicates"]):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(values[(start + j) % n] for j in range(block))
        bootstrap.append(statistics.fmean(sample[:n]))
    bootstrap.sort()
    def percentile(q):
        index = q * (len(bootstrap) - 1)
        low = math.floor(index)
        return bootstrap[low] + (bootstrap[math.ceil(index)] - bootstrap[low]) * (index - low)
    return {"mean": statistics.fmean(values), "interval_95": [percentile(0.025), percentile(0.975)], "test_sessions": n,
            **settings, "method": "circular moving-block percentile bootstrap of daily equal-stock mean paired absolute loss",
            "sign": "text MAE minus market MAE; positive means text is worse",
            "limitations": "Conditional on this fixed sample, vintage, model selection and period; approximate temporal dependence only; no causal or generalization claim."}


def render_report(result: dict[str, Any]) -> str:
    evaluation = result["evaluation"]
    lines = ["# 问答文本增量预测试验", "", f"运行：{result['run_id']}；数据：{result['data_kind']}。", "",
             "目标是收盘后预测下一交易日股票简单收益。只用当前及历史行情、截止时刻可见的提问和回复。",
             "股票按配置固定；公司指标变量用于两个模型。超参数只按验证集 RMSE 选择；训练和验证数据合并重拟合后，测试期间参数固定。", "",
             "| 切分 | 保留行数 | 交易日数 | 剔除跨界行数 | 特征日期起止 |", "|---|---:|---:|---:|---|"]
    for name, audit in evaluation["split_audit"].items():
        lines.append(f"| {name} | {audit['rows']} | {audit['sessions']} | {audit['purged_rows']} | {audit['first_as_of'][:10]} 至 {audit['last_as_of'][:10]} |")
    lines += ["", "MAE 和 RMSE 以下用收益率百分点表示。方向准确率不代表可交易收益，零收益预测没有方向信号。", "",
              "| 模型 | 测试 MAE | 测试 RMSE | 方向准确率 |", "|---|---:|---:|---:|"]
    names = {"zero_return": "零收益", "historical_mean": "各股票历史均值", "market_only": "行情 Ridge", "market_plus_text": "行情＋文本 Ridge"}
    for name, score in evaluation["test_metrics"].items():
        m = score["pooled"]
        direction = f"{100 * m['directional_accuracy']:.2f}%" if m["directional_accuracy"] is not None else "无方向信号"
        lines.append(f"| {names[name]} | {100 * m['mae']:.4f} | {100 * m['rmse']:.4f} | {direction} |")
    lines += ["", "| 股票代码 | 行情 MAE | 行情＋文本 MAE | 文本－行情 |", "|---|---:|---:|---:|"]
    market, text = (evaluation["test_metrics"][k]["by_stock"] for k in ("market_only", "market_plus_text"))
    for code in result["config"]["stock_codes"]:
        lines.append(f"| {code} | {100 * market[code]['mae']:.4f} | {100 * text[code]['mae']:.4f} | {100 * (text[code]['mae'] - market[code]['mae']):+.4f} |")
    paired = evaluation["paired_mae_difference"]
    low, high = paired["interval_95"]
    lines += ["", f"按交易日先平均各股票配对绝对误差，文本减行情为 {paired['mean'] * 100:+.4f} 个百分点；",
              f"{paired['block_sessions']} 日循环移动区块、{paired['replicates']} 次重抽样的近似 95% 区间为 [{low * 100:+.4f}, {high * 100:+.4f}] 个百分点。",
              "正值表示文本模型误差更大。该区间只描述本试验条件下的测试误差，不包含模型选择和样本选择的不确定性。", "",
              "## 口径与限制", "",
              "- 日期切分及参数网格在首次运行前写入本地配置。三只股票在查看来源覆盖后探索性选取，虽要求训练开始前已有问答，仍未外部预注册；一年行情不能代表整个市场。",
              "- 问答时间作为公开可见时间代理；日期精度回复从次日零点起使用。原始公开时刻、修订历史和日志完整性未核实。",
              "- 只读取指定 2020 来源；声明覆盖期以外禁止构造文本零值。覆盖期内无记录按零计数，但仍取决于来源完整性。",
              "- 文本为数量、长度和字面关键词比例，不是 LLM、语义情绪或 Agent 决策。没有使用财务值、违规标签、用户字段、季度统计或未来回复。",
              "- 行情为当前下载版本；日收益核对通过，但不是历史时点归档版，也未经独立行情源复核。",
              "- 使用北京时间 15:00 为收盘信息代理。未模拟行情入库延迟、成交、交易成本或仓位；不是收益策略评估。",
              "- 配对区块区间为固定小样本的近似诊断，没有证明因果、稳定预测能力或未来市场表现。", ""]
    return "\n".join(lines)


def run_prediction(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path, output_dir = config_path.resolve(), output_dir.resolve()
    config_hash = file_sha256(config_path)
    config = load_prediction_config(config_path)
    panel, inputs, provenance, protected = load_panel_inputs(config_path, config)
    if inputs[config_path] != config_hash:
        raise RuntimeError("prediction config changed during loading")
    if any(path == output_dir or output_dir in path.parents for path in set(inputs) | protected):
        raise ValueError("output directory must not contain input files")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty output directory to retain experiment vintages")
    evaluation, predictions = evaluate_panel(panel, config)
    code_dir = Path(__file__).parent
    code_paths = [code_dir / name for name in ("run_prediction.py", "prediction_panel.py", "run_experiments.py", "event_study.py")]
    code_paths += [code_dir.parent / "data_pipeline" / name for name in
                   ("asof_features.py", "aggregate_qa_features.py", "build_dataset.py", "market_data.py", "provenance.py")]
    code_hashes = {str(path.relative_to(code_dir.parent)): file_sha256(path) for path in code_paths}
    result = {"pipeline_version": VERSION, "run_id": config["run_id"], "data_kind": config["data_kind"],
              "config": config, "panel_rows": len(panel), "provenance": provenance, "evaluation": evaluation}
    for path, digest in inputs.items():
        if file_sha256(path) != digest:
            raise RuntimeError(f"input changed during prediction: {path.name}")
    identity = {"config_sha256": config_hash, "inputs": {str(p): h for p, h in inputs.items()}, "code_sha256": code_hashes}
    result["experiment_id"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        for filename, records in (("prediction_panel.csv", panel), ("test_predictions.csv", predictions)):
            with (staging / filename).open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(records[0]))
                writer.writeheader()
                writer.writerows(records)
        (staging / "prediction_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "prediction_report.md").write_text(render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "experiment_id": result["experiment_id"],
                    "generated_at": datetime.now(timezone.utc).isoformat(), "python_version": platform.python_version(),
                    **identity, "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()},
                    "assumptions": result["config"]["qa_coverage"], "close_time_proxy": "15:00 Asia/Shanghai",
                    "information_rule": "training targets strictly before next split's first feature cutoff; test model frozen",
                    "note": "No raw texts or user fields in output panel; source occurrences are retained."}
        (staging / "prediction_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_prediction(args.config, args.output_dir)
    print(json.dumps({"run_id": result["run_id"], "panel_rows": result["panel_rows"],
                      "test_metrics": result["evaluation"]["test_metrics"],
                      "paired_mae_difference": result["evaluation"]["paired_mae_difference"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
