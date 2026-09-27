"""Gate and adapt reviewed semantic predictions into Agent text signals.

This module deliberately stops before historical replay.  It produces a
provenance-bound signal stream only after ``compare_holdout`` has established
the pre-registered research gate.  A passing gate means that a controlled
Agent ablation is eligible; it does not calibrate investors or market impact.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .signal_validation import load_pack, read_jsonl, validate_predictions

VERSION = "semantic-agent-signal-adapter-v1"
GATE_PIPELINE = "semantic-holdout-comparison-v1"
GATE_SCOPE = ("Eligibility for a controlled Agent signal ablation only; not real investor calibration, "
              "causal historical reproduction, or regulatory forecasting.")
DIRECTION_SIGN = {"positive": 1.0, "negative": -1.0, "neutral": 0.0, "unknown": 0.0}
ROOT = Path(__file__).resolve().parents[2]


def _verified_comparison(comparison_dir: Path, experiment_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = comparison_dir / "comparison_manifest.json"
    result_path = comparison_dir / "holdout_comparison.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("pipeline_version") != GATE_PIPELINE:
        raise ValueError("comparison is not a semantic holdout comparison")
    if manifest.get("code_sha256", {}).get("compare_holdout.py") != file_sha256(
            Path(__file__).with_name("compare_holdout.py")):
        raise ValueError("comparison was not produced by the current holdout scorer")
    declared = manifest.get("artifacts", {}).get(result_path.name, {}).get("sha256")
    if not declared or file_sha256(result_path) != declared:
        raise ValueError("holdout comparison result differs from its manifest")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("experiment_id") != experiment_id:
        raise ValueError("holdout comparison belongs to another annotation pack")
    gate = result.get("research_signal_gate")
    if not isinstance(gate, dict) or gate.get("scope") != GATE_SCOPE:
        raise ValueError("comparison does not carry the registered Agent signal gate")
    if gate.get("passed") is not True:
        raise ValueError("research signal gate has not passed; human gold review is required")
    checks = gate.get("checks")
    if not isinstance(checks, dict) or not checks or any(value is not True for value in checks.values()):
        raise ValueError("research signal gate contains a failed or malformed check")
    return manifest, result


def verify_gate(comparison_dir: Path, experiment_id: str) -> dict[str, Any]:
    """Return the verified gate summary or raise before any signal is emitted."""
    manifest, result = _verified_comparison(comparison_dir.resolve(), experiment_id)
    return {"pipeline_version": manifest["pipeline_version"],
            "comparison_manifest_sha256": file_sha256(comparison_dir / "comparison_manifest.json"),
            "comparison_result_sha256": file_sha256(comparison_dir / "holdout_comparison.json"),
            "passed": True,
            "checks": result["research_signal_gate"]["checks"],
            "scope": result["research_signal_gate"]["scope"]}


def _event_contribution(event: dict[str, Any]) -> float:
    value = DIRECTION_SIGN[event["direction"]] * event["intensity"] * (1.0 - event["uncertainty"])
    if not math.isfinite(value):
        raise ValueError("event contribution must be finite")
    return value


def prediction_to_signal(prediction: dict[str, Any]) -> tuple[float, float, int]:
    """Map events to a bounded signed signal and an explicit uncertainty."""
    if prediction["parse_error"] is not None:
        return 0.0, 1.0, 0
    events = prediction["events"]
    if not events:
        return 0.0, 1.0, 0
    signal = sum(_event_contribution(event) for event in events) / len(events)
    uncertainty = sum(event["uncertainty"] for event in events) / len(events)
    return max(-1.0, min(1.0, signal)), max(0.0, min(1.0, uncertainty)), len(events)


def build_signal_rows(pack_dir: Path, predictions_path: Path,
                      comparison_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate a complete model run and create rows suitable for an Agent observation."""
    items, pack_manifest = load_pack(pack_dir.resolve())
    gate = verify_gate(comparison_dir, pack_manifest["experiment_id"])
    predictions = read_jsonl(predictions_path.resolve())
    audit = validate_predictions(items, predictions)
    if audit["prediction_rows"] != len(items) or audit["missing_predictions"]:
        raise ValueError("Agent signal adaptation requires one prediction for every holdout item")
    models = {(row["model_id"], row["prompt_version"]) for row in predictions}
    if len(models) != 1:
        raise ValueError("Agent signal adaptation requires one model and prompt version")
    rows = []
    for prediction in predictions:
        item = items[prediction["item_id"]]
        signal, uncertainty, event_count = prediction_to_signal(prediction)
        rows.append({"item_id": prediction["item_id"], "stock_code": item["stock_code"],
                     "available_at": item["available_at"], "stage": item["stage"],
                     "source_text_sha256": item["source_text_sha256"],
                     "model_id": prediction["model_id"], "prompt_version": prediction["prompt_version"],
                     "text_signal": signal, "uncertainty": uncertainty,
                     "event_count": event_count, "parse_error": prediction["parse_error"],
                     "text_evidence": f"sha256:{item['source_text_sha256']}"})
    rows.sort(key=lambda row: (row["available_at"], row["stock_code"], row["item_id"]))
    return rows, {"gate": gate, "prediction_audit": audit, "items": len(rows),
                  "parse_error_items": sum(row["parse_error"] is not None for row in rows)}


def run_adapter(pack_dir: Path, predictions_path: Path, comparison_dir: Path,
                output_dir: Path) -> dict[str, Any]:
    pack_dir, predictions_path, comparison_dir, output_dir = (
        path.resolve() for path in (pack_dir, predictions_path, comparison_dir, output_dir))
    sources = (pack_dir, predictions_path, comparison_dir)
    if any(output_dir == path or output_dir in path.parents or path in output_dir.parents for path in sources):
        raise ValueError("Agent signal output must be separate from all inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty Agent signal output directory")
    rows, audit = build_signal_rows(pack_dir, predictions_path, comparison_dir)
    source_paths = (pack_dir / "annotation_manifest.json", pack_dir / "annotation_items.jsonl",
                    predictions_path, comparison_dir / "comparison_manifest.json",
                    comparison_dir / "holdout_comparison.json")
    source_hashes = {str(path): file_sha256(path) for path in source_paths}
    result = {"pipeline_version": VERSION, "pack_experiment_id": load_pack(pack_dir)[1]["experiment_id"],
              "items": len(rows), "parse_error_items": audit["parse_error_items"],
              "model_id": rows[0]["model_id"] if rows else None,
              "prompt_version": rows[0]["prompt_version"] if rows else None,
              "gate": audit["gate"],
              "scope": "Controlled Agent signal ablation eligibility; no historical replay or investor calibration.",
              "input_sha256": source_hashes}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        rows_path = staging / "agent_signal_rows.jsonl"
        rows_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        result_path = staging / "agent_signal_result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = staging / "agent_signal_report.md"
        report.write_text("# Agent 语义信号适配结果\n\n"
                          f"已生成 {len(rows)} 条带来源哈希的文本信号；解析失败 {audit['parse_error_items']} 条。\n\n"
                          "该产物只有在留出集独立双审比较门槛通过后才会生成，且仅有资格用于受控 Agent 消融实验；"
                          "不表示历史投资者校准、因果复现或监管预测。\n", encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "input_sha256": source_hashes,
                    "code_sha256": {name: file_sha256(ROOT / "research/semantic" / name)
                                    for name in ("agent_signal_adapter.py", "signal_validation.py")},
                    "artifacts": {path.name: {"sha256": file_sha256(path)} for path in staging.iterdir()}}
        (staging / "agent_signal_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("comparison_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_adapter(args.pack_dir, args.predictions, args.comparison_dir,
                                 args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
