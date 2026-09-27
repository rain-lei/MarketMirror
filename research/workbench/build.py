"""Build a local dashboard from audited, explicitly whitelisted research summaries."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from ..registry.verify_catalog import audit_run, load_catalog
from ..semantic.audit_model_run import audit_model_run

VERSION = "research-workbench-v4"
ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/integrity_catalog_2020.json"
OUTPUTS = ROOT / "research_outputs"
ASSETS = Path(__file__).resolve().parent / "assets"
PRIVATE_FIELDS = {"question_text", "reply_text", "user_name", "source_path", "source_file_hash",
                  "raw_response", "segments", "annotation_items", "evidence_spans"}


def validate_public_payload(value: Any) -> None:
    """Fail closed if a future summary edit accidentally embeds source text or host paths."""
    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key in PRIVATE_FIELDS:
                    raise ValueError(f"private field cannot enter workbench: {key}")
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, str):
            if re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]|wxid_|[/\\]Users[/\\]", node, re.IGNORECASE):
                raise ValueError("local private path or identifier cannot enter workbench")
    visit(value)


def checked_report(directory: Path, manifest_name: str, result_name: str,
                   code_path: Path) -> dict[str, Any]:
    manifest = json.loads((directory / manifest_name).read_text(encoding="utf-8"))
    if manifest["auditor_code_sha256"] != file_sha256(code_path):
        raise ValueError(f"auditor code changed since {manifest_name}")
    if result_name not in manifest["artifacts"]:
        raise ValueError(f"report is absent from audited artifacts: {result_name}")
    for name, info in manifest["artifacts"].items():
        if file_sha256(directory / name) != info["sha256"]:
            raise ValueError(f"audited report artifact changed: {name}")
    return json.loads((directory / result_name).read_text(encoding="utf-8"))


def checked_semantic_review() -> dict[str, Any]:
    directory = OUTPUTS / "semantic_review_comparison_pilot_2020"
    manifest = json.loads((directory / "comparison_manifest.json").read_text(encoding="utf-8"))
    if manifest["code_sha256"] != file_sha256(ROOT / "research/semantic/review_workflow.py"):
        raise ValueError("semantic review code changed since comparison")
    if "comparison_results.json" not in manifest["artifacts"]:
        raise ValueError("semantic review result is absent from audited artifacts")
    for path, digest in manifest["inputs"].items():
        if file_sha256(Path(path)) != digest:
            raise ValueError("semantic review input changed")
    for name, info in manifest["artifacts"].items():
        if file_sha256(directory / name) != info["sha256"]:
            raise ValueError("semantic review report changed")
    return json.loads((directory / "comparison_results.json").read_text(encoding="utf-8"))


def checked_financial_dictionary() -> dict[str, Any]:
    """Load the public financial dictionary only after checking its provenance."""
    directory = OUTPUTS / "financial_2020"
    report_path = directory / "financial_quality_report.json"
    dictionary_path = directory / "field_dictionary.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    dictionary = json.loads(dictionary_path.read_text(encoding="utf-8"))
    generated = dictionary.get("generated_from", {})
    scope = dictionary.get("scope", {})
    if generated.get("report_sha256") != file_sha256(report_path):
        raise ValueError("financial dictionary does not match quality report")
    if generated.get("source_file_sha256") != report.get("source_file_hash"):
        raise ValueError("financial dictionary source hash does not match quality report")
    if generated.get("catalog_sha256") != file_sha256(ROOT / "research/data_contracts/financial_fields.json"):
        raise ValueError("financial dictionary does not match field catalog")
    if scope.get("all_values_status") != "unverified" or any(
            item.get("verification_status") != "unverified" for item in dictionary.get("fields", [])):
        raise ValueError("workbench refuses to display a financial dictionary with promoted values")
    validate_public_payload(dictionary)
    return dictionary


def checked_model_run() -> dict[str, Any]:
    """Expose only metadata from an optional DeepSeek semantic run."""
    pack_dir = OUTPUTS / "semantic_annotation_pilot_2020"
    raw_dir = OUTPUTS / "semantic_model_deepseek_v1"
    pack_manifest = json.loads((pack_dir / "annotation_manifest.json").read_text(encoding="utf-8"))
    pack_items = int(pack_manifest.get("counts", {}).get("items", 0))
    if not raw_dir.exists():
        return {"status": "not_run", "model_id": None,
                "scope": {"pack_items": pack_items, "requested_rows": 0, "full_pack_requested": False},
                "raw": {"rows": 0, "remaining_rows": pack_items, "request_failures": 0, "status": "not_run"},
                "normalized": {"status": "not_provided"},
                "scoring": {"status": "unavailable_without_adjudicated_gold", "accuracy_claim_allowed": False}}
    normalized_dir = OUTPUTS / "semantic_model_deepseek_v1_normalized"
    result = audit_model_run(pack_dir, raw_dir, normalized_dir if normalized_dir.exists() else None)
    result["status"] = "audited"
    validate_public_payload(result)
    return result


def collect_data() -> dict[str, Any]:
    pinned = load_catalog(CONFIG)
    cache: dict[Path, str] = {}
    audits = [audit_run(run, CONFIG.parent, ROOT / "research", OUTPUTS, cache) for run in pinned]
    if any(run["status"] != "passed" for run in audits):
        raise ValueError("workbench refuses to display runs with failed pinned integrity checks")
    integrity = checked_report(OUTPUTS / "integrity_catalog_2018_2020", "integrity_manifest.json",
                               "integrity_results.json", ROOT / "research/registry/verify_catalog.py")
    reexecution = checked_report(OUTPUTS / "reexecution_catalog_2018_2020", "reexecution_manifest.json",
                                 "reexecution_results.json", ROOT / "research/registry/reexecute.py")
    if (integrity["catalog_sha256"] != file_sha256(CONFIG)
            or integrity["passed_runs"] != len(pinned)
            or reexecution["integrity_catalog_sha256"] != file_sha256(CONFIG)
            or reexecution["config_sha256"] != file_sha256(ROOT / "research/configs/reexecution_catalog_2020.json")
            or reexecution["passed_runs"] != len(pinned)):
        raise ValueError("stored audit or reexecution report is incomplete or belongs to another catalog")
    expected_ids = {run["run_id"] for run in pinned}
    if ({run["run_id"] for run in integrity["runs"]} != expected_ids
            or {run["run_id"] for run in reexecution["runs"]} != expected_ids
            or any(run["status"] != "equivalent" for run in reexecution["runs"])):
        raise ValueError("stored audit or reexecution run statuses do not match the pinned catalog")
    reviewed = checked_semantic_review()
    financial_dictionary = checked_financial_dictionary()
    model_run = checked_model_run()
    manifests = {run["run_id"]: (CONFIG.parent / run["manifest"]).resolve() for run in pinned}

    def artifact(run_id: str, name: str) -> dict[str, Any]:
        return json.loads((manifests[run_id].parent / name).read_text(encoding="utf-8"))

    event = artifact("observed_event", "event_results.json")
    event_2018 = artifact("observed_event_2018", "event_results.json")
    activity = artifact("activity_event", "event_activity.json")
    prediction = artifact("text_prediction", "prediction_results.json")
    stress = artifact("synthetic_stress", "stress_results.json")
    replay_q1 = artifact("historical_replay_q1", "historical_replay.json")
    replay_later = artifact("historical_replay_later", "historical_replay.json")
    replay_2018 = artifact("historical_replay_2018", "historical_replay.json")
    placebo = artifact("event_date_diagnostic", "placebo_results.json")
    dataset_manifest = json.loads(manifests["unified_dataset"].read_text(encoding="utf-8"))
    run_status = {r["run_id"]: r for r in reexecution["runs"]}
    market_periods = []
    market_dates = set()
    for run_id in ("observed_market_2018", "observed_market"):
        dates = json.loads(manifests[run_id].read_text(encoding="utf-8"))["session_dates"]
        market_dates.update(dates)
        market_periods.append({"start": dates[0], "end": dates[-1], "sessions": len(dates)})
    summary = {
        "version": VERSION,
        "context": "探索性研究工作台：结果不构成因果结论、可交易收益或监管预测。",
        "overview": {"question_rows": dataset_manifest["counts"]["qa_rows"],
                     "stocks": dataset_manifest["counts"]["companies"],
                     "market_sessions": len(market_dates),
                     "market_periods": market_periods,
                     "integrity_passed": integrity["passed_runs"],
                     "reexecution_passed": reexecution["passed_runs"],
                     "run_total": len(pinned)},
        "runs": [{"id": run["run_id"], "integrity": audit["status"],
                  "reexecution": run_status[run["run_id"]]["status"],
                  "hash_checks": sum(audit["checks"].values()),
                  "compared_artifacts": len(run_status[run["run_id"]]["artifacts"]),
                  "artifacts": [{"name": item["artifact"], "status": item["status"],
                                 "expected_sha256": item["expected_sha256"],
                                 "regenerated_sha256": item["regenerated_sha256"]}
                                for item in run_status[run["run_id"]]["artifacts"]]}
                 for run, audit in zip(pinned, audits)],
        "events": [{"event_id": row["event_id"], "stock_code": row["stock_code"],
                    "event_date": row["event_date_used"], "car": row["cumulative_abnormal_return"],
                    "window_before": row["window_before"], "window_after": row["window_after"],
                    "daily": [{"relative_day": d["relative_trade_day"], "date": d["trade_date"],
                               "abnormal_return": d["abnormal_return"]} for d in row["abnormal_returns"]]}
                   for report in (event_2018, event) for row in report["results"]],
        "activity": [{"event_id": row["event_id"], "stock_code": row["stock_code"],
                      "amount_fold": row["event_day_amount_fold"],
                      "volume_fold": row["event_day_volume_fold"]} for row in activity["runs"]],
        "placebo": {"event_ids": [row["event_id"] for row in event["config"]["events"]],
                    "before": placebo["diagnostic"]["excluded_or_retained_dates"].get("before_actual_event", 0),
                    "after": placebo["diagnostic"]["excluded_or_retained_dates"].get("after_actual_event", 0),
                    "blackout_start": placebo["diagnostic"]["blackout_start"],
                    "blackout_end": placebo["diagnostic"]["blackout_end"]},
        "prediction": {"rows": prediction["evaluation"]["test_metrics"]["market_only"]["pooled"]["rows"],
                       "market_mae": prediction["evaluation"]["test_metrics"]["market_only"]["pooled"]["mae"],
                       "text_mae": prediction["evaluation"]["test_metrics"]["market_plus_text"]["pooled"]["mae"],
                       "paired_difference": prediction["evaluation"]["paired_mae_difference"]["mean"],
                       "interval_95": prediction["evaluation"]["paired_mae_difference"]["interval_95"]},
        "stress": {"steps": stress["steps"],
                   "agents": [{"name": name, "role": row["role"], "trades": row["trades"],
                               "max_drawdown": row["max_drawdown"]} for name, row in stress["summary"].items()]},
        "replays": [],
        "semantic": {"items": reviewed["pack_items"], "dual_reviewed": reviewed["dual_reviewed_items"],
                     "pending": reviewed["pending_items"], "conflicts": reviewed["conflict_items"],
                     "gold_ready": reviewed["gold_ready"], "status": reviewed["status"]},
        "financial_dictionary": financial_dictionary,
        "model_run": model_run,
        "evidence": [{"label": "人民银行：2018 资管新规答记者问", "url": event_2018["config"]["events"][0]["evidence_source"]},
                     {"label": "新华社：武汉通告", "url": "https://www.xinhuanet.com/politics/2020-01/23/c_1125495557.htm"},
                     {"label": "上交所：春节休市调整", "url": "http://www.sse.com.cn/disclosure/announcement/general/c/c_20200127_4991582.shtml"},
                     {"label": "BaoStock API 文档", "url": "https://www.baostock.com/mainContent?file=pythonAPI.md"}],
        "limitations": ["原始问答公开时点、财务单位与标签定义尚未独立核实。",
                        "三类 Agent 参数是示意值；没有可观察持仓、净订单流或盘口深度作行为与冲击校准。",
                        "2018 资管新规是去杠杆背景下的一个节点；窗口包含发布前交易日，存在预期和同期冲击。2018 回放未接入当年问答或政策文本。",
                        "文本预测增益区间包含零；语义标注尚无双人完成条目。"],
    }
    for label, replay in (("2018 H1", replay_2018), ("2020 Q1", replay_q1), ("2020 Apr–Dec", replay_later)):
        for code, paths in replay["paths"].items():
            active, control = paths["market_signal"]["summary"], paths["zero_signal"]["summary"]
            for name, row in active.items():
                initial = replay["agents"][name]["initial_cash"]
                summary["replays"].append({"period": label, "stock_code": code, "role": row["role"],
                                           "agent": name, "signal_multiple": row["final_wealth"] / initial,
                                           "control_multiple": control[name]["final_wealth"] / initial,
                                           "buyhold_multiple": row["buy_hold_reference_wealth"] / initial,
                                           "max_drawdown": row["max_drawdown"], "trades": row["trades"]})
    validate_public_payload(summary)
    return summary


def render_report(data: dict[str, Any]) -> str:
    """Render the same whitelisted summary used by the browser into Markdown."""
    overview = data["overview"]
    role_labels = {"aggressive": "激进型", "conservative": "保守型", "institutional": "机构型"}
    lines = ["# MarketMirror 研究摘要报告", "", data["context"], "",
             "> 本文件由已通过本机完整性核验的摘要生成；它不包含问答原文、个人路径或完整数据库。", "",
             "## 研究概况", "",
             f"- 问答来源行：{overview['question_rows']:,}",
             f"- 来源股票代码：{overview['stocks']:,}",
             f"- 行情交易日：{overview['market_sessions']}",
             f"- 固定运行核验：{overview['integrity_passed']}/{overview['run_total']}",
             f"- 独立重跑：{overview['reexecution_passed']}/{overview['run_total']}", "",
             "## 历史事件窗口", "",
             "CAR 是事件前后整个窗口的异常日收益之和，包含发布前的交易日，不能解释为政策发布后的跌幅或因果效应。", "",
             "| 事件口径 | 股票 | 对齐日 | CAR |", "|---|---:|---|---:|"]
    for row in data["events"]:
        lines.append(f"| {row['event_id']} | {row['stock_code']} | {row['event_date']} | {row['car']:.4%} |")
    if overview.get("market_periods"):
        lines += ["", "行情覆盖分段（合计交易日按日期去重）："]
        lines.extend(f"- {p['start']} 至 {p['end']}：{p['sessions']} 日。" for p in overview["market_periods"])
    if data["events"] and "window_before" in data["events"][0]:
        lines += ["", "事件窗口（相对交易日）："]
        windows = {r["event_id"]: (r["window_before"], r["window_after"]) for r in data["events"]}
        lines.extend(f"- {event_id}：[-{before}, +{after}]。" for event_id, (before, after) in windows.items())
    prediction = data["prediction"]
    lines += ["", "## 文本增量预测", "",
              f"测试预测 {prediction['rows']} 条；纯行情 MAE {prediction['market_mae']:.4%}，行情加文本 MAE {prediction['text_mae']:.4%}。",
              f"配对 MAE 差值（文本 − 行情）{prediction['paired_difference']:.4%}，近似 95% 区间 [{prediction['interval_95'][0]:.4%}, {prediction['interval_95'][1]:.4%}]。", "",
              "## Agent 规则回放", "",
              "| 时期 | 股票 | 角色 | 市场信号 | 零信号 | 买入持有 | 最大回撤 | 交易 |", "|---|---:|---|---:|---:|---:|---:|---:|"]
    for row in data["replays"]:
        lines.append(f"| {row['period']} | {row['stock_code']} | {role_labels.get(row['role'], row['role'])} | {row['signal_multiple']:.3f} | {row['control_multiple']:.3f} | {row['buyhold_multiple']:.3f} | {row['max_drawdown']:.2%} | {row['trades']} |")
    lines += ["", "## 运行核验", "", "| 运行 | 完整性 | 重跑 | 哈希检查 | 比较产物 |", "|---|---|---|---:|---:|"]
    for row in data["runs"]:
        lines.append(f"| {row['id']} | {row['integrity']} | {row['reexecution']} | {row['hash_checks']} | {row['compared_artifacts']} |")
    semantic = data["semantic"]
    lines += ["", "## 产物证据", "", "每项产物名称与比较状态来自独立重跑报告；哈希在本机运行目录中保存。", ""]
    for row in data["runs"]:
        lines.append(f"### {row['id']}")
        lines.extend(f"- `{item['name']}`：{item['status']}" for item in row.get("artifacts", []))
        lines.append("")
    lines += ["## 语义审核状态", "", f"样本 {semantic['items']} 条；双人完成 {semantic['dual_reviewed']} 条；待审 {semantic['pending']} 条；分歧 {semantic['conflicts']} 条；状态 `{semantic['status']}`。", "",
              "## 公开证据", ""]
    lines.extend(f"- [{item['label']}]({item['url']})" for item in data["evidence"])
    financial = data.get("financial_dictionary")
    if financial:
        scope = financial["scope"]
        lines += ["", "## 财务字段口径状态", "",
                  f"字段 {scope['field_count']} 个（快照 {scope['snapshot_field_count']}，行上下文 {scope['row_context_field_count']}）；来源行 {scope['source_rows']:,}。所有值状态为 `unverified`，该字典只描述结构统计。", "",
                  "| 字段 | 层级 | 缺失率 | 数值 | 非数值 | 范围 | 状态 |",
                  "|---|---|---:|---:|---:|---|---|"]
        for item in financial["fields"]:
            minimum, maximum = item["numeric_min"], item["numeric_max"]
            value_range = "—" if minimum is None else f"{minimum:g} … {maximum:g}"
            rate = "—" if item["missing_rate"] is None else f"{item['missing_rate']:.2%}"
            lines.append(f"| {item['field_name'].replace('|', '/')} | {item['role']} | {rate} | {item['numeric_count']:,} | {item['non_numeric_count']:,} | {value_range} | `{item['verification_status']}` |")
        lines += ["", "### 尚未确认", ""]
        lines.extend(f"- `{item['key']}`：{item['question']}（{item['status']}）" for item in financial["unresolved_semantics"])
    model_run = data.get("model_run")
    if model_run:
        lines += ["", "## LLM 运行状态", ""]
        if model_run.get("status") == "not_run":
            lines.append("尚未执行 DeepSeek 语义抽取；没有模型输出或准确率结论。")
        else:
            raw = model_run["raw"]
            normalized = model_run["normalized"]
            lines.append(f"模型 `{model_run.get('model_id')}`；原始响应 {raw['rows']}/{model_run['scope']['requested_rows']} 条，失败 {raw['request_failures']} 条；标准化状态 `{normalized['status']}`；`accuracy_claim_allowed=false`。")
    lines += ["", "## 研究限制", ""]
    lines.extend(f"- {item}" for item in data["limitations"])
    return "\n".join(lines) + "\n"


def build_workbench(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty workbench output directory")
    data = collect_data()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        for name in ("index.html", "app.js", "style.css"):
            shutil.copyfile(ASSETS / name, staging / name)
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        (staging / "data.js").write_text("window.MARKETMIRROR_DATA = " + payload + ";\n", encoding="utf-8")
        (staging / "report.md").write_text(render_report(data), encoding="utf-8")
        source_reports = {name: file_sha256(path) for name, path in {
            "integrity_results": OUTPUTS / "integrity_catalog_2018_2020/integrity_results.json",
            "reexecution_results": OUTPUTS / "reexecution_catalog_2018_2020/reexecution_results.json",
            "semantic_comparison": OUTPUTS / "semantic_review_comparison_pilot_2020/comparison_results.json",
            "financial_quality_report": OUTPUTS / "financial_2020/financial_quality_report.json",
            "financial_dictionary": OUTPUTS / "financial_2020/field_dictionary.json"}.items()}
        model_manifest = OUTPUTS / "semantic_model_deepseek_v1/model_run_manifest.json"
        if model_manifest.exists():
            source_reports["semantic_model_manifest"] = file_sha256(model_manifest)
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "integrity_catalog_sha256": file_sha256(CONFIG),
                    "source_reports": source_reports,
                    "code_sha256": file_sha256(Path(__file__)),
                    "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "workbench_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return {"output_dir": str(output_dir), "run_count": data["overview"]["run_total"],
            "reviewed_labels": data["semantic"]["dual_reviewed"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_workbench(args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
