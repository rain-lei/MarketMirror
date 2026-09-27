"""Compare extraction protocols on identical items; report no accuracy claims."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .audit_model_run import audit_model_run
from .diagnose_model import diagnose_rows
from .signal_validation import load_pack, read_jsonl, validate_predictions
from ..data_pipeline.provenance import file_sha256

VERSION = "semantic-protocol-comparison-v1"


def compare_rows(items: dict[str, dict[str, Any]], baseline: list[dict[str, Any]],
                 candidate: list[dict[str, Any]]) -> dict[str, int]:
    for rows in (baseline, candidate):
        validate_predictions(items, rows)
    def state(row: dict[str, Any] | None) -> str:
        if row is None:
            return "missing"
        if row["parse_error"] is not None:
            return "failed"
        return "event" if row["events"] else "empty"
    old, new = ({row["item_id"]: row for row in rows} for rows in (baseline, candidate))
    return dict(sorted(Counter(f"{state(old.get(item_id))}_to_{state(new.get(item_id))}"
                               for item_id in items).items()))


def compare_runs(pack_dir: Path, baseline_raw: Path, baseline_normalized: Path,
                 candidate_raw: Path, candidate_normalized: Path, output_dir: Path) -> dict[str, Any]:
    locations = [path.resolve() for path in (pack_dir, baseline_raw, baseline_normalized,
                                           candidate_raw, candidate_normalized)]
    pack_dir, baseline_raw, baseline_normalized, candidate_raw, candidate_normalized = locations
    output_dir = output_dir.resolve()
    if any(output_dir == path or path in output_dir.parents for path in locations):
        raise ValueError("comparison output must be separate from inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty comparison directory")
    inputs = {"pack_manifest": pack_dir / "annotation_manifest.json",
              "pack_items": pack_dir / "annotation_items.jsonl"}
    for label, raw, normalized in (("baseline", baseline_raw, baseline_normalized),
                                   ("candidate", candidate_raw, candidate_normalized)):
        inputs.update({f"{label}_raw_manifest": raw / "model_run_manifest.json",
                       f"{label}_raw": raw / "model_raw_outputs.jsonl",
                       f"{label}_normalized_manifest": normalized / "normalization_manifest.json",
                       f"{label}_predictions": normalized / "model_predictions.jsonl"})
    hashes = {key: file_sha256(path) for key, path in inputs.items()}
    items, manifest = load_pack(pack_dir)
    reports, predictions, settings = {}, {}, []
    for label, raw, normalized in (("baseline", baseline_raw, baseline_normalized),
                                   ("candidate", candidate_raw, candidate_normalized)):
        audit = audit_model_run(pack_dir, raw, normalized)
        rows = read_jsonl(inputs[f"{label}_predictions"])
        reports[label] = {"audit": audit, **diagnose_rows(items, rows)}
        predictions[label] = rows
        run_manifest = json.loads(inputs[f"{label}_raw_manifest"].read_text(encoding="utf-8"))
        settings.append(tuple(run_manifest.get(key) for key in ("model_id", "provider_base_url", "temperature")))
    if settings[0] != settings[1]:
        raise ValueError("protocol comparison requires identical requested model, gateway and temperature")
    report = {"pipeline_version": VERSION, "pack_experiment_id": manifest["experiment_id"],
              **reports, "transitions": compare_rows(items, predictions["baseline"], predictions["candidate"]),
              "accuracy_claim_allowed": False,
              "interpretation": "Same-item protocol development comparison; not semantic accuracy or untouched held-out evaluation. Remote generation can vary."}
    if any(hashes[key] != file_sha256(path) for key, path in inputs.items()):
        raise ValueError("comparison input changed during execution")
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "comparison.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 语义抽取协议开发对照", "",
             "同一批原文、同一请求模型/网关/温度；独立调用两版提示。此样本已用于协议开发，不能称为未接触的最终测试集。", "",
             "| 版本 | 响应 | 通过 | 失败 | 有效空事件 | 有效事件条目 |", "|---|---:|---:|---:|---:|---:|"]
    for label in ("baseline", "candidate"):
        metrics = report[label]["coverage"]
        lines.append(f"| {report[label]['audit']['prompt_version']} | {metrics['returned_rows']} | {metrics['valid_rows']} | {metrics['parse_error_rows']} | {metrics['valid_empty_rows']} | {metrics['valid_event_rows']} |")
    lines += ["", "解析成功不等于语义正确；事件数量增加不证明准确率或市场预测能力提高。仍需独立双人标注与新的留出样本。", ""]
    markdown_path = output_dir / "comparison.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    code_names = ("compare_model_runs.py", "audit_model_run.py", "diagnose_model.py", "parse_model_outputs.py",
                  "quote_grounding.py", "prompt_contract.py", "signal_validation.py")
    output_manifest = {"pipeline_version": VERSION, "input_sha256": hashes,
                       "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in code_names},
                       "artifacts": {path.name: {"sha256": file_sha256(path)} for path in (result_path, markdown_path)}}
    (output_dir / "comparison_manifest.json").write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pack_dir", "baseline_raw", "baseline_normalized", "candidate_raw", "candidate_normalized"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = compare_runs(args.pack_dir, args.baseline_raw, args.baseline_normalized,
                          args.candidate_raw, args.candidate_normalized, args.output_dir)
    print(json.dumps({name: result[name]["coverage"] for name in ("baseline", "candidate")}))


if __name__ == "__main__":
    main()
