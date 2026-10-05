"""Audit lexical overlap between a development pack and a frozen holdout pack."""

from __future__ import annotations

import argparse
import json
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .signal_validation import load_pack


VERSION = "semantic-holdout-overlap-v1"
THRESHOLDS = (0.5, 0.7, 0.9, 1.0)


def normalize_text(value: str) -> str:
    """Ignore presentation punctuation and width without dropping semantic digits."""
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold()
                   if char.isalnum())


def character_trigrams(value: str) -> frozenset[str]:
    if not value:
        return frozenset()
    if len(value) < 3:
        return frozenset({value})
    return frozenset(value[index:index + 3] for index in range(len(value) - 2))


def trigram_jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _segments(item: dict[str, Any]) -> list[tuple[str, int, frozenset[str]]]:
    result = []
    for segment in item["segments"]:
        normalized = normalize_text(segment["text"])
        result.append((segment["source"], len(normalized), character_trigrams(normalized)))
    return result


def compare_packs(development: dict[str, dict[str, Any]],
                  holdout: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not development or not holdout:
        raise ValueError("both packs must contain items")
    development_segments = {item_id: _segments(item) for item_id, item in development.items()}
    nearest = []
    segment_comparisons = 0
    for holdout_id, item in sorted(holdout.items()):
        candidate_segments = _segments(item)
        best: tuple[float, str, str, int, int] = (-1.0, "", "", 0, 0)
        for development_id, source_segments in development_segments.items():
            for source, length, grams in candidate_segments:
                for other_source, other_length, other_grams in source_segments:
                    if source != other_source:
                        continue
                    segment_comparisons += 1
                    score = trigram_jaccard(grams, other_grams)
                    if score > best[0]:
                        best = (score, development_id, source, length, other_length)
        if best[0] < 0:
            raise ValueError("no matching segment source in development pack")
        nearest.append({"holdout_item_id": holdout_id, "development_item_id": best[1],
                        "segment_source": best[2], "trigram_jaccard": best[0],
                        "holdout_normalized_characters": best[3],
                        "development_normalized_characters": best[4]})
    ordered = sorted(nearest, key=lambda row: (-row["trigram_jaccard"], row["holdout_item_id"]))
    return {"method": "Unicode NFKC + casefold + alphanumeric characters; set Jaccard of character trigrams, compared only between matching question/reply segments",
            "development_items": len(development), "holdout_items": len(holdout),
            "segment_comparisons": segment_comparisons,
            "max_trigram_jaccard": ordered[0]["trigram_jaccard"],
            "holdout_items_at_or_above": {str(threshold): sum(row["trigram_jaccard"] >= threshold for row in nearest)
                                          for threshold in THRESHOLDS},
            "nearest_pairs": ordered,
            "limitations": ["Lexical dissimilarity does not rule out semantic paraphrases or shared facts.",
                            "Topic-stratified holdout scores cannot estimate population prevalence."]}


def audit_overlap(development_dir: Path, holdout_dir: Path, output_dir: Path) -> dict[str, Any]:
    development_dir, holdout_dir, output_dir = (path.resolve() for path in
                                                 (development_dir, holdout_dir, output_dir))
    if development_dir == holdout_dir:
        raise ValueError("development and holdout packs must differ")
    if any(output_dir == path or output_dir in path.parents or path in output_dir.parents
           for path in (development_dir, holdout_dir)):
        raise ValueError("output must be separate from both packs")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty audit output directory")
    development, development_manifest = load_pack(development_dir)
    holdout, holdout_manifest = load_pack(holdout_dir)
    if holdout_manifest.get("development_experiment_id") != development_manifest["experiment_id"]:
        raise ValueError("holdout does not declare this development pack")
    inputs = {path: file_sha256(path) for directory in (development_dir, holdout_dir)
              for path in (directory / "annotation_manifest.json", directory / "annotation_items.jsonl")}
    result = compare_packs(development, holdout)
    if any(file_sha256(path) != digest for path, digest in inputs.items()):
        raise RuntimeError("pack changed during overlap audit")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        staging = Path(temporary)
        report = staging / "overlap_results.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {"pipeline_version": VERSION,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "development_experiment_id": development_manifest["experiment_id"],
                    "holdout_experiment_id": holdout_manifest["experiment_id"],
                    "input_sha256": {str(path): digest for path, digest in inputs.items()},
                    "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in
                                    ("holdout_overlap.py", "signal_validation.py")},
                    "artifacts": {report.name: {"sha256": file_sha256(report)}}}
        (staging / "overlap_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("development_dir", type=Path)
    parser.add_argument("holdout_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = audit_overlap(args.development_dir, args.holdout_dir, args.output_dir)
    print(json.dumps({"max_trigram_jaccard": result["max_trigram_jaccard"],
                      "holdout_items_at_or_above": result["holdout_items_at_or_above"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
