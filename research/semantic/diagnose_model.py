"""Describe extraction coverage and validation failures without claiming accuracy."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .audit_model_run import audit_model_run
from .signal_validation import load_pack, read_jsonl, strict_json_loads, validate_predictions
from ..data_pipeline.provenance import file_sha256

VERSION = "semantic-model-diagnostics-v1"


def evidence_locations(items: dict[str, dict[str, Any]], raw_rows: list[dict[str, Any]]) -> dict[str, int]:
    """Diagnose quoted text separately from offsets, without repairing outputs."""
    counts: Counter[str] = Counter()
    for row in raw_rows:
        try:
            payload = strict_json_loads(row["raw_response"])
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            continue
        sources = {segment["source"]: segment["text"] for segment in items[row["item_id"]]["segments"]}
        for event in payload["events"]:
            spans = event.get("evidence_spans") if isinstance(event, dict) else None
            if not isinstance(spans, list):
                continue
            for span in spans:
                if not isinstance(span, dict):
                    counts["invalid_span"] += 1
                    continue
                source, quote = span.get("source"), span.get("quote")
                text = sources.get(source) if isinstance(source, str) else None
                if text is None or not isinstance(quote, str) or not quote:
                    counts["invalid_source_or_quote"] += 1
                    continue
                start, end = span.get("start"), span.get("end")
                exact = (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                         and text[start:end] == quote)
                if exact:
                    counts["exact_offsets"] += 1
                    continue
                location = text.find(quote)
                if location < 0:
                    counts["quote_not_present"] += 1
                elif text.find(quote, location + 1) >= 0:
                    counts["quote_ambiguous_wrong_offsets"] += 1
                else:
                    counts["unique_exact_quote_wrong_offsets"] += 1
    return dict(sorted(counts.items()))


def error_category(error: str | None) -> str | None:
    """Return only fixed categories; parser messages can contain private text."""
    if error is None:
        return None
    if error.startswith("JSONDecodeError:") or "duplicate JSON key" in error:
        return "json_syntax"
    if "evidence quote must exactly match" in error:
        return "evidence_text_or_offsets"
    if "evidence source or offsets" in error:
        return "evidence_source_or_offsets"
    if "source-grounded evidence" in error or "evidence span fields" in error:
        return "evidence_schema"
    return "response_schema_or_value"


def diagnose_rows(items: dict[str, dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    validate_predictions(items, predictions)
    groups: dict[str, dict[str, Any]] = {}
    for dimension in ("stage", "split", "selection_stratum"):
        dimension_groups = {}
        for value in sorted({item[dimension] for item in items.values()}):
            selected = [row for row in predictions if items[row["item_id"]][dimension] == value]
            valid = [row for row in selected if row["parse_error"] is None]
            dimension_groups[value] = {
                "pack_items": sum(item[dimension] == value for item in items.values()),
                "returned_rows": len(selected), "valid_rows": len(valid),
                "parse_error_rows": len(selected) - len(valid),
                "valid_empty_rows": sum(not row["events"] for row in valid),
                "valid_event_rows": sum(bool(row["events"]) for row in valid)}
        groups[dimension] = dimension_groups
    valid = [row for row in predictions if row["parse_error"] is None]
    events = [event for row in valid for event in row["events"]]
    return {
        "coverage": {"pack_items": len(items), "returned_rows": len(predictions),
                     "missing_rows": len(items) - len(predictions),
                     "valid_rows": len(valid), "parse_error_rows": len(predictions) - len(valid),
                     "valid_empty_rows": sum(not row["events"] for row in valid),
                     "valid_event_rows": sum(bool(row["events"]) for row in valid),
                     "validated_events": len(events)},
        "error_categories": dict(sorted(Counter(error_category(row["parse_error"])
                                                for row in predictions if row["parse_error"]).items())),
        "event_types": dict(sorted(Counter(event["event_type"] for event in events).items())),
        "directions": dict(sorted(Counter(event["direction"] for event in events).items())),
        "groups": groups,
        "accuracy_claim_allowed": False,
        "limitations": [
            "Validation checks structure and exact evidence only; semantic accuracy is unknown.",
            "Empty valid responses are not proof that no event exists; failed rows are not empty valid responses.",
            "The sample was stratified; event frequencies do not estimate market-wide prevalence.",
            "Split summaries describe extraction coverage only, not training or held-out performance."]}


def diagnose_run(pack_dir: Path, raw_dir: Path, normalized_dir: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, raw_dir, normalized_dir, output_dir = (
        path.resolve() for path in (pack_dir, raw_dir, normalized_dir, output_dir))
    inputs = {
        "annotation_manifest": pack_dir / "annotation_manifest.json",
        "annotation_items": pack_dir / "annotation_items.jsonl",
        "raw_manifest": raw_dir / "model_run_manifest.json",
        "raw_outputs": raw_dir / "model_raw_outputs.jsonl",
        "normalization_manifest": normalized_dir / "normalization_manifest.json",
        "predictions": normalized_dir / "model_predictions.jsonl"}
    hashes = {key: file_sha256(path) for key, path in inputs.items()}
    audit = audit_model_run(pack_dir, raw_dir, normalized_dir)
    items, _ = load_pack(pack_dir)
    result = {"pipeline_version": VERSION, "audit": audit,
              **diagnose_rows(items, read_jsonl(inputs["predictions"])),
              "proposed_evidence_locations": evidence_locations(items, read_jsonl(inputs["raw_outputs"]))}
    if any(hashes[key] != file_sha256(path) for key, path in inputs.items()):
        raise ValueError("model inputs changed during diagnostics")
    if any(output_dir == path or path in output_dir.parents for path in (pack_dir, raw_dir, normalized_dir)):
        raise ValueError("diagnostics output must be separate from inputs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty diagnostics directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "diagnostics.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    coverage = result["coverage"]
    lines = ["# DeepSeek 语义抽取运行诊断", "",
             f"返回 {coverage['returned_rows']}/{coverage['pack_items']} 条；结构与证据校验通过 {coverage['valid_rows']} 条，解析失败 {coverage['parse_error_rows']} 条。", "",
             f"通过条目中：空事件 {coverage['valid_empty_rows']} 条，有事件 {coverage['valid_event_rows']} 条，共 {coverage['validated_events']} 个事件。", "",
             "这些是输出校验计数，不能作为准确率。失败条目不计入有效空事件。", "",
             "| 失败类别 | 条数 |", "|---|---:|"]
    lines.extend(f"| {key} | {count} |" for key, count in result["error_categories"].items())
    lines += ["", "| 原始响应提出的证据定位情况 | 跨度数 |", "|---|---:|"]
    lines.extend(f"| {key} | {count} |" for key, count in result["proposed_evidence_locations"].items())
    lines += ["", "该表按原始提出的证据跨度计数，包含整条事件已被拒绝的跨度，不是有效事件数；没有据此修复输出。"]
    lines += ["", "人工金标准尚未完成；抽取信号尚未用于 Agent 行为校准或历史效果主张。", ""]
    markdown_path = output_dir / "diagnostics.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    manifest = {"pipeline_version": VERSION, "input_sha256": hashes,
                "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in
                                ("diagnose_model.py", "signal_validation.py", "audit_model_run.py")},
                "artifacts": {path.name: {"sha256": file_sha256(path)} for path in (report_path, markdown_path)}}
    (output_dir / "diagnostics_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("normalized_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(diagnose_run(args.pack_dir, args.raw_dir, args.normalized_dir, args.output_dir)["coverage"]))


if __name__ == "__main__":
    main()
