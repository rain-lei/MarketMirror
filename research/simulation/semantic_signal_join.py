"""Join a verified semantic signal stream to lagged Agent replay steps.

The join is deliberately separate from the existing market-only replay.  It
does not change any archived result and refuses adapter output that was not
created after the registered semantic holdout gate passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from ..semantic.agent_signal_adapter import VERSION as ADAPTER_VERSION
from ..semantic.signal_validation import read_jsonl

VERSION = "semantic-signal-join-v1"


def _visible_date(value: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("semantic availability time must be a nonempty string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("semantic availability time must be ISO date or datetime") from exc


def _bounded(value: Any, lower: float, upper: float, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} must be finite and between {lower} and {upper}")
    return float(value)


def load_verified_signal_stream(signal_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verify the adapter output before it can influence a replay step."""
    signal_dir = signal_dir.resolve()
    manifest_path = signal_dir / "agent_signal_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("pipeline_version") != ADAPTER_VERSION:
        raise ValueError("signal stream was not produced by the registered Agent adapter")
    expected = {"agent_signal_rows.jsonl", "agent_signal_result.json", "agent_signal_report.md"}
    if set(manifest.get("artifacts", {})) != expected:
        raise ValueError("signal stream artifact set differs from its manifest")
    for name, info in manifest["artifacts"].items():
        if file_sha256(signal_dir / name) != info.get("sha256"):
            raise ValueError(f"signal stream artifact differs from its manifest: {name}")
    result = json.loads((signal_dir / "agent_signal_result.json").read_text(encoding="utf-8"))
    if result.get("pipeline_version") != ADAPTER_VERSION:
        raise ValueError("signal stream result version differs from its adapter")
    gate = result.get("gate", {})
    if gate.get("passed") is not True or not isinstance(gate.get("checks"), dict) or any(
            value is not True for value in gate["checks"].values()):
        raise ValueError("Agent signal stream is not eligible: holdout gate has not passed")
    rows = read_jsonl(signal_dir / "agent_signal_rows.jsonl")
    seen = set()
    for row in rows:
        required = {"item_id", "stock_code", "available_at", "stage", "source_text_sha256",
                    "model_id", "prompt_version", "text_signal", "uncertainty", "event_count",
                    "parse_error", "text_evidence"}
        if set(row) != required or row["item_id"] in seen:
            raise ValueError("semantic signal row fields or item identities are invalid")
        seen.add(row["item_id"])
        for key in ("item_id", "source_text_sha256"):
            if not isinstance(row[key], str) or len(row[key]) != 64:
                raise ValueError(f"{key} must be a source hash")
        if not isinstance(row["stock_code"], str) or not row["stock_code"].strip():
            raise ValueError("stock_code must be a nonempty string")
        _visible_date(row["available_at"])
        _bounded(row["text_signal"], -1.0, 1.0, "text_signal")
        _bounded(row["uncertainty"], 0.0, 1.0, "uncertainty")
        if type(row["event_count"]) is not int or row["event_count"] < 0:
            raise ValueError("event_count must be a nonnegative integer")
        if row["parse_error"] is not None and (not isinstance(row["parse_error"], str) or not row["parse_error"].strip()):
            raise ValueError("parse_error must be null or a nonempty string")
        if not isinstance(row["text_evidence"], str) or not row["text_evidence"].strip():
            raise ValueError("text_evidence is required")
    return rows, result


def _evidence_id(rows: list[dict[str, Any]], cutoff: date) -> str:
    if not rows:
        return f"semantic:none-through:{cutoff.isoformat()}"
    digest = hashlib.sha256("|".join(sorted(row["item_id"] for row in rows)).encode("utf-8")).hexdigest()
    return f"semantic:{digest}"


def join_steps(steps: list[dict[str, Any]], stock_code: str,
               signal_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add an as-of text signal to each replay step without future leakage."""
    if not steps or not isinstance(stock_code, str) or not stock_code.strip():
        raise ValueError("steps and stock_code are required")
    visible = [row for row in signal_rows if row["stock_code"] == stock_code]
    joined = []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("trade_date"), str) \
                or not isinstance(step.get("signal_cutoff_date"), str):
            raise ValueError("replay steps require trade_date and signal_cutoff_date")
        trade_date = _visible_date(step["trade_date"])
        cutoff = _visible_date(step["signal_cutoff_date"])
        if cutoff >= trade_date:
            raise ValueError("semantic signal cutoff must precede the trade date")
        available = [row for row in visible if _visible_date(row["available_at"]) <= cutoff]
        row = dict(step)
        row["text_signal"] = (sum(float(item["text_signal"]) for item in available) / len(available)
                              if available else 0.0)
        row["text_uncertainty"] = (sum(float(item["uncertainty"]) for item in available) / len(available)
                                   if available else 0.0)
        row["text_evidence"] = _evidence_id(available, cutoff)
        row["semantic_item_count"] = len(available)
        joined.append(row)
    return joined


def join_signal_directory(steps_path: Path, signal_dir: Path, stock_code: str,
                          output_path: Path) -> dict[str, Any]:
    """Write an auditable joined-step fixture for a later Agent replay."""
    steps_path, signal_dir, output_path = (path.resolve() for path in (steps_path, signal_dir, output_path))
    if (output_path == steps_path or steps_path in output_path.parents
            or output_path == signal_dir or signal_dir in output_path.parents):
        raise ValueError("joined steps cannot replace an input")
    rows, result = load_verified_signal_stream(signal_dir)
    steps = json.loads(steps_path.read_text(encoding="utf-8"))
    if not isinstance(steps, list):
        raise ValueError("steps input must be a JSON list")
    joined = join_steps(steps, stock_code, rows)
    payload = {"pipeline_version": VERSION, "stock_code": stock_code,
               "steps": joined, "semantic_signal_result_sha256": file_sha256(signal_dir / "agent_signal_result.json"),
               "signal_items": len(rows), "eligible_gate": result["gate"]}
    if output_path.exists():
        raise ValueError("use a fresh joined-step output path")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("steps", type=Path)
    parser.add_argument("signal_dir", type=Path)
    parser.add_argument("stock_code")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = join_signal_directory(args.steps, args.signal_dir, args.stock_code, args.output)
    print(json.dumps({"pipeline_version": result["pipeline_version"],
                      "stock_code": result["stock_code"], "steps": len(result["steps"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
