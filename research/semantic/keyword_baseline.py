"""Literal no-LLM comparator; keyword hits are candidate topics, never verified facts."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .signal_validation import load_pack, validate_predictions
from ..data_pipeline.aggregate_qa_features import KEYWORDS
from ..data_pipeline.provenance import file_sha256

VERSION = "literal-keyword-semantic-v1"
TYPE_MAP = {"earnings": "earnings", "policy": "regulation", "risk": "other", "liquidity": "liquidity",
            "capital_return": "other", "pandemic": "other", "governance": "governance"}


def predict_item(item: dict[str, Any]) -> dict[str, Any]:
    # On a reply-stage item, try the newly visible reply first; the prior question is only context.
    for segment in reversed(item["segments"]):
        text = segment["text"]
        for category, terms in KEYWORDS.items():
            for term in terms:
                start = text.find(term)
                if start < 0:
                    continue
                return {"item_id": item["item_id"], "source_text_sha256": item["source_text_sha256"],
                        "model_id": VERSION, "prompt_version": "no-llm", "parse_error": None,
                        "events": [{"event_type": TYPE_MAP[category], "direction": "unknown",
                                    "affected_industries": [], "horizon": "unknown", "intensity": 0.25,
                                    "uncertainty": 1.0,
                                    "evidence_spans": [{"source": segment["source"], "start": start,
                                                        "end": start + len(term), "quote": term}]}]}
    return {"item_id": item["item_id"], "source_text_sha256": item["source_text_sha256"],
            "model_id": VERSION, "prompt_version": "no-llm", "parse_error": None, "events": []}


def run_baseline(pack_dir: Path, output_dir: Path) -> dict[str, Any]:
    pack_dir, output_dir = pack_dir.resolve(), output_dir.resolve()
    items, pack_manifest = load_pack(pack_dir)
    predictions = [predict_item(item) for item in items.values()]
    audit = validate_predictions(items, predictions)
    if output_dir == pack_dir or pack_dir in output_dir.parents:
        raise ValueError("baseline output must be separate from annotation pack")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty baseline output directory")
    inputs = {"annotation_manifest.json": file_sha256(pack_dir / "annotation_manifest.json"),
              "annotation_items.jsonl": file_sha256(pack_dir / "annotation_items.jsonl")}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        with (staging / "keyword_predictions.jsonl").open("w", encoding="utf-8") as handle:
            for prediction in predictions:
                handle.write(json.dumps(prediction, ensure_ascii=False) + "\n")
        report = {"pipeline_version": VERSION, "pack_experiment_id": pack_manifest["experiment_id"],
                  "generated_at": datetime.now(timezone.utc).isoformat(), "input_sha256": inputs,
                  "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in
                                  ("keyword_baseline.py", "signal_validation.py")},
                  "validation": audit,
                  "candidate_events": sum(bool(p["events"]) for p in predictions),
                  "interpretation": "Lexical topic candidates only; direction unknown; no observed predictive quality or gold accuracy.",
                  "artifacts": {"keyword_predictions.jsonl": {"sha256": file_sha256(staging / "keyword_predictions.jsonl")}}}
        if any(file_sha256(pack_dir / name) != h for name, h in inputs.items()):
            raise RuntimeError("annotation pack changed during baseline generation")
        (staging / "keyword_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = run_baseline(args.pack_dir, args.output_dir)
    print(json.dumps({"candidate_events": report["candidate_events"], "validation": report["validation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
