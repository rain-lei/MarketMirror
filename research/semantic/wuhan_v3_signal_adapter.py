"""Gate independent Wuhan v3 predictions before emitting Agent text signals."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .agent_signal_adapter import prediction_to_signal
from .score_wuhan_v3_validation import VERSION as SCORE_VERSION, score
from .signal_validation import read_jsonl, validate_predictions
from .wuhan_protocol_v3 import MODEL, PREDICTIONS, VERSION as PROMPT_VERSION, audit_v3, verify_pack


VERSION = "wuhan-v3-independent-agent-signal-v1"
MODULE = Path(__file__).resolve()
COMPARISON_NAME = "independent_comparison.json"
COMPARISON_MANIFEST = "independent_comparison_manifest.json"
ROWS_NAME = "agent_signal_rows.jsonl"
RESULT_NAME = "agent_signal_result.json"
MANIFEST_NAME = "agent_signal_manifest.json"
REPORT_NAME = "agent_signal_report.md"
REQUIRED_CHECKS = {
    "blind_reference_complete_before_model", "source_disjoint", "all_items_scored",
    "parse_error_at_most_5_percent", "event_detection_f1_at_least_0_70",
    "event_detection_f1_not_below_keyword", "supported_type_macro_f1_at_least_0_60",
}


def verify_gate(pack_dir: Path, raw_dir: Path, normal_dir: Path,
                score_dir: Path) -> dict[str, Any]:
    pack_dir, raw_dir, normal_dir, score_dir = (
        path.resolve() for path in (pack_dir, raw_dir, normal_dir, score_dir))
    manifest = json.loads((score_dir / COMPARISON_MANIFEST).read_text(encoding="utf-8"))
    report_path = score_dir / COMPARISON_NAME
    if (manifest.get("pipeline_version") != SCORE_VERSION
            or manifest.get("artifacts") != {
                COMPARISON_NAME: {"sha256": file_sha256(report_path)}}
            or not isinstance(manifest.get("inputs"), dict)
            or not isinstance(manifest.get("code_sha256"), dict)
            or any(file_sha256(Path(path)) != digest
                   for path, digest in manifest["inputs"].items())
            or any(file_sha256(MODULE.with_name(name)) != digest
                   for name, digest in manifest["code_sha256"].items())):
        raise ValueError("independent comparison manifest, source or code hash differs")
    archived = json.loads(report_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="wuhan_v3_gate_recheck_") as temporary:
        fresh_dir = Path(temporary) / "score"
        recalculated = score(pack_dir, raw_dir, normal_dir, fresh_dir)
        fresh_manifest = json.loads((fresh_dir / COMPARISON_MANIFEST).read_text(encoding="utf-8"))
    if (archived != recalculated
            or any(manifest.get(key) != fresh_manifest.get(key)
                   for key in ("inputs", "code_sha256", "artifacts"))):
        raise ValueError("independent comparison differs from source-first recalculation")
    gate = archived.get("research_signal_gate", {})
    checks = gate.get("checks")
    if (archived.get("pipeline_version") != SCORE_VERSION
            or archived.get("sample_role") != "independent_company_validation"
            or not isinstance(checks, dict) or set(checks) != REQUIRED_CHECKS
            or gate.get("passed") is not True or any(value is not True for value in checks.values())):
        raise ValueError("independent v3 research signal gate has not passed")
    return {"passed": True, "checks": checks,
            "scope": gate["scope"], "score_report_sha256": file_sha256(report_path),
            "score_manifest_sha256": file_sha256(score_dir / COMPARISON_MANIFEST)}


def build_signal_rows(pack_dir: Path, raw_dir: Path, normal_dir: Path,
                      score_dir: Path) -> tuple[list[dict], dict]:
    gate = verify_gate(pack_dir, raw_dir, normal_dir, score_dir)
    items, pack = verify_pack(pack_dir)
    raw_audit = audit_v3(pack_dir, raw_dir, normal_dir)
    predictions = read_jsonl(normal_dir / PREDICTIONS)
    prediction_audit = validate_predictions(items, predictions)
    if (prediction_audit["prediction_rows"] != len(items)
            or raw_audit["model_rows"] != len(items)
            or any(row["model_id"] != MODEL or row["prompt_version"] != PROMPT_VERSION
                   for row in predictions)):
        raise ValueError("independent v3 model coverage or identity differs")
    rows = []
    for prediction in predictions:
        item = items[prediction["item_id"]]
        if item["stage"] != "reply" or any(
                span["source"] != "reply" for event in prediction["events"]
                for span in event["evidence_spans"]):
            raise ValueError("independent Agent signal requires company reply evidence")
        signal, uncertainty, event_count = prediction_to_signal(prediction)
        rows.append({"item_id": prediction["item_id"], "stock_code": item["stock_code"],
                     "available_at": item["available_at"], "stage": "reply",
                     "source_text_sha256": item["source_text_sha256"],
                     "model_id": prediction["model_id"],
                     "prompt_version": prediction["prompt_version"],
                     "text_signal": signal, "uncertainty": uncertainty,
                     "event_count": event_count, "parse_error": prediction["parse_error"],
                     "text_evidence": f"sha256:{item['source_text_sha256']}"})
    rows.sort(key=lambda row: (row["available_at"], row["stock_code"], row["item_id"]))
    return rows, {"gate": gate, "pack_experiment_id": pack["experiment_id"],
                  "items": len(items), "parse_error_items": prediction_audit["parse_errors"]}


def run_adapter(pack_dir: Path, raw_dir: Path, normal_dir: Path,
                score_dir: Path, output_dir: Path) -> dict:
    pack_dir, raw_dir, normal_dir, score_dir, output_dir = (
        path.resolve() for path in (pack_dir, raw_dir, normal_dir, score_dir, output_dir))
    if (any(output_dir == path or output_dir in path.parents or path in output_dir.parents
            for path in (pack_dir, raw_dir, normal_dir, score_dir))
            or output_dir.exists() and any(output_dir.iterdir())):
        raise ValueError("v3 Agent signal output must be a new directory separate from inputs")
    rows, audit = build_signal_rows(pack_dir, raw_dir, normal_dir, score_dir)
    sources = (pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
               raw_dir / "model_run_manifest.json", raw_dir / "model_raw_outputs.jsonl",
               normal_dir / "normalization_manifest.json", normal_dir / PREDICTIONS,
               score_dir / COMPARISON_MANIFEST, score_dir / COMPARISON_NAME)
    hashes = {str(path): file_sha256(path) for path in sources}
    result = {"pipeline_version": VERSION,
              "pack_experiment_id": audit["pack_experiment_id"],
              "items": len(rows), "model_id": MODEL, "prompt_version": PROMPT_VERSION,
              "parse_error_items": audit["parse_error_items"],
              "gate": audit["gate"],
              "scope": "Controlled independent-company Agent ablation; no real-investor calibration or causal prediction.",
              "input_sha256": hashes}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        stage = Path(temporary)
        (stage / ROWS_NAME).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8")
        (stage / RESULT_NAME).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
        (stage / REPORT_NAME).write_text(
            "# 武汉独立公司 v3 Agent 文本信号\n\n"
            f"通过已归档的独立公司评分门槛后，生成 {len(rows)} 条回复信号；"
            f"解析失败 {audit['parse_error_items']} 条。\n\n"
            "该信号仅供同池受控消融，不表示真实持仓、订单或疫情因果预测。\n",
            encoding="utf-8")
        manifest = {"pipeline_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "input_sha256": hashes,
                    "code_sha256": {name: file_sha256(MODULE.with_name(name)) for name in (
                        "wuhan_v3_signal_adapter.py", "score_wuhan_v3_validation.py",
                        "agent_signal_adapter.py", "wuhan_protocol_v3.py")},
                    "artifacts": {path.name: {"sha256": file_sha256(path)}
                                  for path in (stage / ROWS_NAME, stage / RESULT_NAME,
                                               stage / REPORT_NAME)}}
        (stage / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
        for path in stage.iterdir():
            path.replace(output_dir / path.name)
    return result


def load_verified_signal_stream(pack_dir: Path, raw_dir: Path, normal_dir: Path,
                                score_dir: Path, signal_dir: Path) -> tuple[list[dict], dict]:
    signal_dir = signal_dir.resolve()
    manifest = json.loads((signal_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    result = json.loads((signal_dir / RESULT_NAME).read_text(encoding="utf-8"))
    sources = (pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
               raw_dir / "model_run_manifest.json", raw_dir / "model_raw_outputs.jsonl",
               normal_dir / "normalization_manifest.json", normal_dir / PREDICTIONS,
               score_dir / COMPARISON_MANIFEST, score_dir / COMPARISON_NAME)
    expected_inputs = {str(path.resolve()): file_sha256(path) for path in sources}
    if (manifest.get("pipeline_version") != VERSION or result.get("pipeline_version") != VERSION
            or manifest.get("artifacts") != {name: {"sha256": file_sha256(signal_dir / name)}
                                             for name in (ROWS_NAME, RESULT_NAME, REPORT_NAME)}
            or manifest.get("code_sha256") != {name: file_sha256(MODULE.with_name(name)) for name in (
                "wuhan_v3_signal_adapter.py", "score_wuhan_v3_validation.py",
                "agent_signal_adapter.py", "wuhan_protocol_v3.py")}
            or manifest.get("input_sha256") != expected_inputs
            or result.get("input_sha256") != expected_inputs
            or any(file_sha256(Path(path)) != digest
                   for path, digest in manifest.get("input_sha256", {}).items())):
        raise ValueError("v3 Agent signal stream artifact, code or source differs")
    rows = read_jsonl(signal_dir / ROWS_NAME)
    expected_rows, expected = build_signal_rows(pack_dir, raw_dir, normal_dir, score_dir)
    if (rows != expected_rows or result["gate"] != expected["gate"]
            or result["pack_experiment_id"] != expected["pack_experiment_id"]
            or result["items"] != len(rows) or result["model_id"] != MODEL
            or result["prompt_version"] != PROMPT_VERSION
            or result["parse_error_items"] != expected["parse_error_items"]):
        raise ValueError("v3 Agent signal rows differ from verified predictions and gate")
    return rows, result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("normal_dir", type=Path)
    parser.add_argument("score_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_adapter(args.pack_dir, args.raw_dir, args.normal_dir,
                         args.score_dir, args.output_dir)
    print(json.dumps({"items": result["items"],
                      "gate_passed": result["gate"]["passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
