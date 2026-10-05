"""Select a third outcome-blind Wuhan cohort disjoint from both used cohorts.

This module only freezes stock-code membership and the source snapshot config.
It does not review labels, inspect market outcomes, call a model, or emit text signals.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from ..data_pipeline.provenance import file_sha256
from .annotation_pack import canonical_hash
from .event_snapshot import (UNIVERSE_SELECTION_RULE, select_point_in_time_universe)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "research/configs"
BASE_SNAPSHOT = CONFIG_DIR / "wuhan_pre_event_validation_snapshot_2020.json"
EXCLUDED_UNIVERSES = (
    ("development", CONFIG_DIR / "wuhan_qna_active_universe_2020.json"),
    ("consumed_v3_validation", CONFIG_DIR / "wuhan_qna_independent_validation_universe_2020.json"),
)
OUTPUT_UNIVERSE_RELATIVE = "wuhan_qna_fresh_v3_holdout_universe_2020.json"
OUTPUT_SNAPSHOT_RELATIVE = "wuhan_pre_event_fresh_v3_holdout_snapshot_2020.json"
SEED = "marketmirror-wuhan-v3-fresh-holdout-v1"
FRESH_SELECTION_RULE = "sha256_rank_after_excluding_prior_development_and_v3_validation_cohorts"


def select_fresh_codes(candidates: Iterable[str], excluded_groups: Iterable[Iterable[str]],
                       seed: str, sample_size: int) -> tuple[list[str], list[str]]:
    """Select deterministically from candidate companies after excluding fixed cohorts."""
    eligible = {code for code in candidates if isinstance(code, str) and re.fullmatch(r"\d{6}", code)}
    excluded: set[str] = set()
    for group in excluded_groups:
        normalized = {code for code in group if isinstance(code, str) and re.fullmatch(r"\d{6}", code)}
        if excluded & normalized:
            raise ValueError("excluded cohorts overlap; resolve their identities before selecting a fresh sample")
        excluded |= normalized
    remaining = eligible - excluded
    if len(remaining) < sample_size:
        raise ValueError("remaining pre-event company frame is smaller than the requested sample")
    selected = select_point_in_time_universe(remaining, seed, sample_size)
    return selected, sorted(excluded)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _load_source_frame(snapshot: dict[str, Any], snapshot_path: Path) -> tuple[set[str], dict[str, Any]]:
    database = (snapshot_path.parent / snapshot["qa_database"]).resolve()
    source_hash = snapshot["qa_source_sha256"]
    manifest_path = database.parent / "run_manifest.json"
    manifest = _load_json(manifest_path)
    matching_sources = [source for source in manifest.get("sources", [])
                        if source.get("sha256") == source_hash]
    if len(matching_sources) != 1:
        raise ValueError("configured QA source is absent or ambiguous in the unified manifest")
    source_path = Path(matching_sources[0]["path"])
    if not source_path.is_absolute():
        source_path = (manifest_path.parent / source_path).resolve()
    db_artifact = manifest.get("artifacts", {}).get(database.name, {})
    if (file_sha256(database) != db_artifact.get("sha256")
            or file_sha256(source_path) != source_hash):
        raise ValueError("QA source or unified database differs from its source manifest")

    lower = snapshot["question_window_start"] + "T00:00:00.000000+08:00"
    upper = snapshot["snapshot_as_of"]
    query = ("SELECT DISTINCT stock_code FROM qa_record WHERE source_file_hash=? "
             "AND question_eligible=1 AND stock_code IS NOT NULL "
             "AND question_available_at>=? AND question_available_at<=?")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        candidate_codes = {row[0] for row in connection.execute(query, (source_hash, lower, upper))
                           if isinstance(row[0], str) and re.fullmatch(r"\d{6}", row[0])}
    return candidate_codes, {"database": database, "source_path": source_path,
                             "manifest_path": manifest_path, "manifest": manifest}


def build_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    """Recompute a third company cohort from source and return both frozen configs."""
    base = _load_json(BASE_SNAPSHOT)
    if (base.get("event_id") != "wuhan_date_only_conservative"
            or base.get("snapshot_as_of") != "2020-01-22T23:59:59.999999+08:00"
            or base.get("selection_rule") != "latest_company_confirmed_reply_per_asset_before_cutoff"):
        raise ValueError("base snapshot is not the fixed Wuhan pre-event source frame")
    candidates, source = _load_source_frame(base, BASE_SNAPSHOT)
    excluded_rows = []
    excluded_groups = []
    for name, path in EXCLUDED_UNIVERSES:
        config = _load_json(path)
        selection = config.get("selection")
        codes = config.get("stock_codes")
        if (not isinstance(selection, dict) or selection.get("selection_rule") != UNIVERSE_SELECTION_RULE
                or selection.get("sample_size") != 126
                or selection.get("qa_source_sha256") != base["qa_source_sha256"]
                or selection.get("question_window_start") != base["question_window_start"]
                or selection.get("snapshot_as_of") != base["snapshot_as_of"]
                or not isinstance(codes, list) or len(codes) != 126
                or len(set(codes)) != 126 or any(code not in candidates for code in codes)):
            raise ValueError(f"excluded {name} universe differs from the fixed source frame")
        excluded_groups.append(codes)
        excluded_rows.append({"name": name, "config": path.relative_to(ROOT).as_posix(),
                              "config_sha256": file_sha256(path), "companies": len(codes),
                              "stock_codes_sha256": canonical_hash(sorted(codes)),
                              "seed": selection["seed"]})

    selected, excluded = select_fresh_codes(candidates, excluded_groups, SEED, 126)
    universe = {
        "selection_audit": {
            "version": "wuhan-fresh-holdout-selection-v1",
            "selection_rule": FRESH_SELECTION_RULE,
            "seed": SEED,
            "sample_size": 126,
            "qa_source_sha256": base["qa_source_sha256"],
            "question_window_start": base["question_window_start"],
            "snapshot_as_of": base["snapshot_as_of"],
            "source_frame_companies": len(candidates),
            "excluded_cohorts": excluded_rows,
            "excluded_company_count": len(excluded),
            "excluded_stock_codes_sha256": canonical_hash(excluded),
            "remaining_candidate_companies": len(candidates - set(excluded)),
            "selected_stock_codes_sha256": canonical_hash(selected),
            "status": "membership_frozen_text_unreviewed_model_unrun",
            "usage_rule": "do not inspect labels or run a model until the prompt is frozen",
            "source_database_sha256": file_sha256(source["database"]),
            "source_file_sha256": base["qa_source_sha256"],
            "source_manifest_sha256": file_sha256(source["manifest_path"]),
        },
        "stock_codes": selected,
    }
    snapshot_config = {**base,
                       "run_id": "wuhan_pre_event_fresh_v3_holdout_2020_v1",
                       "universe_config": OUTPUT_UNIVERSE_RELATIVE}
    return universe, snapshot_config


def verify_existing(universe_path: Path, snapshot_path: Path) -> dict[str, Any]:
    expected_universe, expected_snapshot = build_configs()
    actual_universe, actual_snapshot = _load_json(universe_path), _load_json(snapshot_path)
    if actual_universe != expected_universe or actual_snapshot != expected_snapshot:
        raise ValueError("fresh holdout configs differ from deterministic source reconstruction")
    codes = actual_universe["stock_codes"]
    excluded_count = actual_universe["selection_audit"]["excluded_company_count"]
    return {"companies": len(codes), "excluded_companies": excluded_count,
            "remaining_candidates": actual_universe["selection_audit"]["remaining_candidate_companies"],
            "universe_sha256": file_sha256(universe_path),
            "snapshot_config_sha256": file_sha256(snapshot_path),
            "stock_codes_sha256": canonical_hash(codes)}


def write_configs() -> dict[str, Any]:
    """Write both frozen config files without overwriting existing research inputs."""
    universe_path = CONFIG_DIR / OUTPUT_UNIVERSE_RELATIVE
    snapshot_path = CONFIG_DIR / OUTPUT_SNAPSHOT_RELATIVE
    if universe_path.exists() or snapshot_path.exists():
        raise FileExistsError("fresh holdout config already exists; verify it instead of replacing it")
    universe, snapshot = build_configs()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    staged_paths: list[Path] = []
    try:
        for destination, payload in ((universe_path, universe), (snapshot_path, snapshot)):
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.stem}-", suffix=".tmp", dir=CONFIG_DIR)
            staged = Path(temporary_name)
            staged_paths.append(staged)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        if universe_path.exists() or snapshot_path.exists():
            raise FileExistsError("fresh holdout config appeared during creation; refusing to overwrite")
        staged_paths[0].replace(universe_path)
        staged_paths[1].replace(snapshot_path)
    finally:
        for staged in staged_paths:
            if staged.exists():
                staged.unlink()
    return verify_existing(universe_path, snapshot_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true",
                        help="verify the checked-in fresh-cohort configs against source data")
    parser.add_argument("--universe", type=Path,
                        default=CONFIG_DIR / OUTPUT_UNIVERSE_RELATIVE)
    parser.add_argument("--snapshot-config", type=Path,
                        default=CONFIG_DIR / OUTPUT_SNAPSHOT_RELATIVE)
    parser.add_argument("--write-configs", action="store_true",
                        help="write the deterministic configs once; existing files are never overwritten")
    args = parser.parse_args()
    if args.verify and args.write_configs:
        parser.error("choose either --verify or --write-configs")
    if args.write_configs:
        result = write_configs()
        print(json.dumps({"written": True, **result}, ensure_ascii=False))
    elif args.verify:
        result = verify_existing(args.universe, args.snapshot_config)
        print(json.dumps({"verified": True, **result}, ensure_ascii=False))
    else:
        universe, snapshot = build_configs()
        print(json.dumps({"universe": universe, "snapshot_config": snapshot},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
