"""Freeze a fourth Wuhan company cohort, disjoint from three consumed groups.

Membership uses only pre-event question-active stock codes. It does not inspect
reply text, reference labels, model outputs, or market outcomes.
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
from .event_snapshot import UNIVERSE_SELECTION_RULE, select_point_in_time_universe


ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "research/configs"
BASE_SNAPSHOT = CONFIG_DIR / "wuhan_pre_event_validation_snapshot_2020.json"
PROMPT = Path(__file__).with_name("PROMPT_WUHAN_V4.md")
PRIOR_UNIVERSES = (
    ("development", CONFIG_DIR / "wuhan_qna_active_universe_2020.json"),
    ("independent_validation", CONFIG_DIR / "wuhan_qna_independent_validation_universe_2020.json"),
    ("consumed_v3_evaluation", CONFIG_DIR / "wuhan_qna_fresh_v3_holdout_universe_2020.json"),
)
UNIVERSE_NAME = "wuhan_qna_fresh_v4_holdout_universe_2020.json"
SNAPSHOT_NAME = "wuhan_pre_event_fresh_v4_holdout_snapshot_2020.json"
SEED = "marketmirror-wuhan-v4-independent-evaluation-20260929"
SELECTION_RULE = "sha256_rank_after_excluding_three_consumed_company_cohorts"
SAMPLE_SIZE = 126


def select_fresh_codes(candidates: Iterable[str], excluded_groups: Iterable[Iterable[str]],
                       seed: str, sample_size: int) -> tuple[list[str], list[str]]:
    eligible = {code for code in candidates if isinstance(code, str) and re.fullmatch(r"\d{6}", code)}
    excluded: set[str] = set()
    for group in excluded_groups:
        normalized = {code for code in group if isinstance(code, str) and re.fullmatch(r"\d{6}", code)}
        if excluded & normalized:
            raise ValueError("previous cohorts overlap; resolve membership before selecting v4")
        excluded |= normalized
    remaining = eligible - excluded
    if len(remaining) < sample_size:
        raise ValueError("remaining pre-event company frame is smaller than the requested sample")
    return select_point_in_time_universe(remaining, seed, sample_size), sorted(excluded)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _selection(config: dict[str, Any]) -> dict[str, Any]:
    selection = config.get("selection", config.get("selection_audit"))
    if not isinstance(selection, dict):
        raise ValueError("prior cohort is missing its selection record")
    return selection


def build_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    base = _json(BASE_SNAPSHOT)
    expected = {
        "run_id": "wuhan_pre_event_disjoint_company_validation_source_2020",
        "qa_source_sha256": "a3a1b0afa2cd13b25a56c89393178d93960cd8867a46a7c469fd567bf82ac812",
        "question_window_start": "2020-01-01",
        "snapshot_as_of": "2020-01-22T23:59:59.999999+08:00",
        "selection_rule": "latest_company_confirmed_reply_per_asset_before_cutoff",
    }
    if any(base.get(key) != value for key, value in expected.items()):
        raise ValueError("base snapshot differs from the frozen pre-event source frame")
    if not PROMPT.is_file():
        raise FileNotFoundError("the v4 evaluation prompt must be frozen before selecting a cohort")

    database = (BASE_SNAPSHOT.parent / base["qa_database"]).resolve()
    qa_manifest_path = database.parent / "run_manifest.json"
    qa_manifest = _json(qa_manifest_path)
    matches = [row for row in qa_manifest.get("sources", [])
               if row.get("sha256") == base["qa_source_sha256"]]
    if len(matches) != 1:
        raise ValueError("configured QA source is absent or ambiguous in its run manifest")
    source_path = Path(matches[0]["path"])
    if not source_path.is_absolute():
        source_path = (qa_manifest_path.parent / source_path).resolve()
    db_hash = file_sha256(database)
    source_hash = file_sha256(source_path)
    if (source_hash != base["qa_source_sha256"]
            or qa_manifest.get("artifacts", {}).get(database.name, {}).get("sha256") != db_hash):
        raise ValueError("QA source or unified database differs from its source manifest")

    lower = base["question_window_start"] + "T00:00:00.000000+08:00"
    upper = base["snapshot_as_of"]
    query = ("SELECT DISTINCT stock_code FROM qa_record WHERE source_file_hash=? "
             "AND question_eligible=1 AND stock_code IS NOT NULL "
             "AND question_available_at>=? AND question_available_at<=?")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        candidates = {row[0] for row in connection.execute(query, (source_hash, lower, upper))
                      if isinstance(row[0], str) and re.fullmatch(r"\d{6}", row[0])}

    groups: list[set[str]] = []
    exclusion_audit = []
    for name, path in PRIOR_UNIVERSES:
        config = _json(path)
        codes = config.get("stock_codes")
        selection = _selection(config)
        if (not isinstance(codes, list) or len(codes) != SAMPLE_SIZE
                or len(set(codes)) != SAMPLE_SIZE
                or selection.get("sample_size") != SAMPLE_SIZE
                or selection.get("qa_source_sha256") != base["qa_source_sha256"]
                or selection.get("question_window_start") != base["question_window_start"]
                or selection.get("snapshot_as_of") != base["snapshot_as_of"]
                or any(code not in candidates for code in codes)):
            raise ValueError(f"prior {name} cohort is not valid in the fixed source frame")
        groups.append(set(codes))
        exclusion_audit.append({"name": name, "config": path.relative_to(ROOT).as_posix(),
                                "config_sha256": file_sha256(path),
                                "companies": len(codes),
                                "stock_codes_sha256": canonical_hash(sorted(codes)),
                                "selection_seed": selection.get("seed")})

    selected, excluded = select_fresh_codes(candidates, groups, SEED, SAMPLE_SIZE)
    universe = {
        "selection_audit": {
            "version": "wuhan-v4-unseen-company-selection-v1",
            "selection_rule": SELECTION_RULE,
            "seed": SEED,
            "sample_size": SAMPLE_SIZE,
            "prompt_path": PROMPT.relative_to(ROOT).as_posix(),
            "prompt_sha256": file_sha256(PROMPT),
            "qa_source_sha256": base["qa_source_sha256"],
            "question_window_start": base["question_window_start"],
            "snapshot_as_of": base["snapshot_as_of"],
            "source_frame_companies": len(candidates),
            "excluded_cohorts": exclusion_audit,
            "excluded_company_count": len(excluded),
            "excluded_stock_codes_sha256": canonical_hash(excluded),
            "remaining_candidate_companies": len(candidates - set(excluded)),
            "selected_stock_codes_sha256": canonical_hash(selected),
            "status": "prompt_and_membership_frozen_before_reply_review_and_model_run",
            "selection_inputs": "pre-event question-active six-digit company codes only",
            "prohibited_selection_inputs": ["reply labels", "model outputs", "market outcomes"],
            "source_database_sha256": db_hash,
            "source_file_sha256": source_hash,
            "source_manifest_sha256": file_sha256(qa_manifest_path),
        },
        "stock_codes": selected,
    }
    snapshot = {**base, "run_id": "wuhan_pre_event_fresh_v4_holdout_2020_v1",
                "universe_config": UNIVERSE_NAME}
    return universe, snapshot


def verify_existing(universe_path: Path, snapshot_path: Path) -> dict[str, Any]:
    expected_universe, expected_snapshot = build_configs()
    actual_universe, actual_snapshot = _json(universe_path), _json(snapshot_path)
    if actual_universe != expected_universe or actual_snapshot != expected_snapshot:
        raise ValueError("v4 cohort configs differ from deterministic source reconstruction")
    return {"companies": len(actual_universe["stock_codes"]),
            "excluded_companies": actual_universe["selection_audit"]["excluded_company_count"],
            "remaining_candidates": actual_universe["selection_audit"]["remaining_candidate_companies"],
            "prompt_sha256": actual_universe["selection_audit"]["prompt_sha256"],
            "universe_sha256": file_sha256(universe_path),
            "snapshot_sha256": file_sha256(snapshot_path),
            "selected_codes_sha256": canonical_hash(actual_universe["stock_codes"])}


def write_configs() -> dict[str, Any]:
    universe_path, snapshot_path = CONFIG_DIR / UNIVERSE_NAME, CONFIG_DIR / SNAPSHOT_NAME
    if universe_path.exists() or snapshot_path.exists():
        raise FileExistsError("v4 cohort config exists; verify it instead of overwriting")
    universe, snapshot = build_configs()
    staged: list[Path] = []
    try:
        for destination, data in ((universe_path, universe), (snapshot_path, snapshot)):
            descriptor, name = tempfile.mkstemp(prefix=f".{destination.stem}-", suffix=".tmp",
                                                dir=CONFIG_DIR)
            path = Path(name)
            staged.append(path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        if universe_path.exists() or snapshot_path.exists():
            raise FileExistsError("v4 cohort config appeared while writing; refusing to overwrite")
        staged[0].replace(universe_path)
        staged[1].replace(snapshot_path)
    finally:
        for path in staged:
            if path.exists():
                path.unlink()
    return verify_existing(universe_path, snapshot_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write-configs", action="store_true")
    group.add_argument("--verify", action="store_true")
    parser.add_argument("--universe", type=Path, default=CONFIG_DIR / UNIVERSE_NAME)
    parser.add_argument("--snapshot-config", type=Path, default=CONFIG_DIR / SNAPSHOT_NAME)
    args = parser.parse_args()
    result = (write_configs() if args.write_configs else
              verify_existing(args.universe, args.snapshot_config))
    print(json.dumps({"written": args.write_configs, "verified": args.verify, **result},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
