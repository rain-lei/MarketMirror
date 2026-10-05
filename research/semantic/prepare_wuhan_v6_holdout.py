"""Prepare a sixth company-disjoint question-topic evaluation after v6 freezes.

Selection reads only company codes, eligible question text, question/reply
timestamps, reply visibility flags and QA identifiers. Reply text, labels,
model outputs and market outcomes are never used to choose members or pairs.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .annotation_pack import canonical_hash, digest
from .prepare_wuhan_v5_holdout import (
    BASE_SNAPSHOT, CONFIG_DIR, QUESTION_TERMS, SNAPSHOT_NAME as FIFTH_SNAPSHOT,
    UNIVERSE_NAME as FIFTH_UNIVERSE, ROOT, _base_and_frame, _json,
    _prior_groups, verify_existing as verify_fifth,
)
from .wuhan_v6_gate import load_policy


PROMPT = Path(__file__).with_name("PROMPT_WUHAN_V6.md")
POLICY = CONFIG_DIR / "wuhan_v6_evaluation_policy.json"
UNIVERSE_NAME = "wuhan_qna_fresh_v6_holdout_universe_2020.json"
SNAPSHOT_NAME = "wuhan_pre_event_fresh_v6_holdout_snapshot_2020.json"
SEED = "marketmirror-wuhan-v6-sixth-visible-question-topic-20260929"
SELECTION_RULE = "visible_question_topic_pair_sha256_after_excluding_five_cohorts"
TOPIC_QUOTAS = {"pandemic": 10, "policy": 70, "liquidity": 70,
                "governance": 30, "earnings": 40, "general": 30}
SAMPLE_SIZE = sum(TOPIC_QUOTAS.values())


def _topic_names(question: str) -> tuple[str, ...]:
    matched = tuple(name for name, terms in QUESTION_TERMS.items()
                    if any(term in question for term in terms))
    return matched or ("general",)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 8 * 3600:
        raise ValueError("pre-event Q&A timestamps must use +08:00")
    return parsed


def _candidate_pairs(base: dict, eligible: set[str]) -> dict[str, dict[str, dict]]:
    """Keep the latest visible same-topic Q&A per company without reply text."""
    database = (BASE_SNAPSHOT.parent / base["qa_database"]).resolve()
    lower = _timestamp(base["question_window_start"] + "T00:00:00.000000+08:00")
    cutoff = _timestamp(base["snapshot_as_of"])
    query = (
        "SELECT qa_id,stock_code,question_available_at,question_text,"
        "reply_available_at,reply_eligible FROM qa_record "
        "WHERE source_file_hash=? AND question_eligible=1 "
        "AND question_available_at>=? AND question_available_at<=?"
    )
    pools: dict[str, dict[str, dict]] = {name: {} for name in TOPIC_QUOTAS}
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        for qa_id, code, question_at, question, reply_at, reply_eligible in connection.execute(
                query, (base["qa_source_sha256"], lower.isoformat(timespec="microseconds"),
                        cutoff.isoformat(timespec="microseconds"))):
            if (code not in eligible or reply_eligible != 1
                    or not all(isinstance(value, str) for value in
                               (qa_id, question_at, question, reply_at))):
                continue
            try:
                question_time, reply_time = _timestamp(question_at), _timestamp(reply_at)
            except ValueError:
                continue
            if not lower <= question_time <= reply_time <= cutoff:
                continue
            pair = {"stock_code": code, "qa_id": qa_id,
                    "question_available_at": question_at,
                    "reply_available_at": reply_at}
            for topic in _topic_names(question):
                previous = pools[topic].get(code)
                if (previous is None or (reply_at, qa_id)
                        > (previous["reply_available_at"], previous["qa_id"])):
                    pools[topic][code] = pair
    return pools


def _frame() -> tuple[dict, dict[str, dict[str, dict]], dict]:
    base, candidates, source_hashes = _base_and_frame()
    prior_groups, prior_audit = _prior_groups(base, candidates)
    fifth = verify_fifth(CONFIG_DIR / FIFTH_UNIVERSE, CONFIG_DIR / FIFTH_SNAPSHOT)
    fifth_codes = set(_json(CONFIG_DIR / FIFTH_UNIVERSE)["stock_codes"])
    excluded = set().union(*prior_groups)
    if (len(fifth_codes) != fifth["companies"] or fifth_codes & excluded
            or not fifth_codes <= candidates):
        raise ValueError("fifth cohort is not disjoint from prior source frame")
    excluded |= fifth_codes
    eligible = candidates - excluded
    pools = _candidate_pairs(base, eligible)
    audit = {"source_frame_companies": len(candidates),
             "excluded_company_count": len(excluded),
             "excluded_stock_codes_sha256": canonical_hash(sorted(excluded)),
             "eligible_company_count": len(eligible),
             "visible_topic_company_counts": {name: len(group) for name, group in pools.items()},
             "prior_cohorts": prior_audit + [{
                 "name": "consumed_v5_evaluation",
                 "config": (CONFIG_DIR / FIFTH_UNIVERSE).relative_to(ROOT).as_posix(),
                 "config_sha256": file_sha256(CONFIG_DIR / FIFTH_UNIVERSE),
                 "companies": fifth["companies"],
                 "stock_codes_sha256": fifth["selected_codes_sha256"]}],
             "source_hashes": source_hashes}
    return base, pools, audit


def audit_pool() -> dict:
    """Count candidate capacity without ranking or selecting any company."""
    _, pools, audit = _frame()
    previous_quotas = 0
    lower_bounds: dict[str, int] = {}
    for topic, quota in TOPIC_QUOTAS.items():
        lower_bounds[topic] = max(0, len(pools[topic]) - previous_quotas)
        previous_quotas += quota
    return {"purpose": "sixth_cohort_capacity_only_no_membership_selected",
            **audit, "topic_quotas": TOPIC_QUOTAS,
            "remaining_topic_company_lower_bounds": lower_bounds,
            "all_quotas_guaranteed_feasible": all(
                lower_bounds[name] >= quota for name, quota in TOPIC_QUOTAS.items()),
            "candidate_pairs_use": "question text, QA id, company code, visibility timestamps only",
            "prohibited_selection_inputs": ["reply text", "review labels",
                                            "model outputs", "market outcomes"]}


def select_pairs(pools: dict[str, dict[str, dict]], seed: str = SEED,
                 quotas: dict[str, int] | None = None) -> list[dict]:
    """Pure deterministic selection; each company contributes one visible pair."""
    quotas = TOPIC_QUOTAS if quotas is None else quotas
    if (tuple(quotas) != tuple(TOPIC_QUOTAS) or not isinstance(seed, str)
            or not seed.strip() or any(type(value) is not int or value <= 0
                                        for value in quotas.values())):
        raise ValueError("sixth cohort seed or quotas are invalid")
    selected: list[dict] = []
    used: set[str] = set()
    for topic, quota in quotas.items():
        if topic not in pools:
            raise ValueError(f"missing {topic} candidate pool")
        ranked = sorted((code for code in pools[topic] if code not in used),
                        key=lambda code: (digest(f"{seed}|{topic}|company|{code}"), code))
        if len(ranked) < quota:
            raise ValueError(f"too few company-disjoint visible {topic} replies")
        for code in ranked[:quota]:
            selected.append({**pools[topic][code], "question_topic": topic})
            used.add(code)
    if len(selected) != sum(quotas.values()) or len(used) != len(selected):
        raise AssertionError("sixth cohort repeated a company")
    return selected


def build_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    if not PROMPT.is_file():
        raise FileNotFoundError("freeze PROMPT_WUHAN_V6.md before sixth cohort selection")
    policy = load_policy(POLICY)
    if (policy.get("protocol") != "wuhan-sixth-company-source-first-evaluation-v6"
            or policy.get("sample_role") != "sixth_disjoint_company_evaluation"
            or policy.get("sample_size") != SAMPLE_SIZE
            or policy.get("selection_rule") != SELECTION_RULE
            or policy.get("review_before_model_outputs") is not True
            or policy.get("model_outputs_previously_seen") is not False
            or policy.get("human_gold_ready") is not False
            or policy.get("external_preregistration") is not False):
        raise ValueError("sixth evaluation policy identity differs")
    base, pools, audit = _frame()
    selected = select_pairs(pools)
    if len(selected) != SAMPLE_SIZE:
        raise AssertionError("sixth cohort sample size differs from frozen quotas")
    selected_by_topic = {name: [row for row in selected if row["question_topic"] == name]
                         for name in TOPIC_QUOTAS}
    universe = {"selection_audit": {
        "version": "wuhan-v6-sixth-visible-question-topic-selection-v1",
        "selection_rule": SELECTION_RULE, "seed": SEED,
        "sample_size": SAMPLE_SIZE, "topic_quotas": TOPIC_QUOTAS,
        "prompt_path": PROMPT.relative_to(ROOT).as_posix(),
        "prompt_sha256": file_sha256(PROMPT),
        "evaluation_policy_path": POLICY.relative_to(ROOT).as_posix(),
        "evaluation_policy_sha256": file_sha256(POLICY),
        "qa_source_sha256": base["qa_source_sha256"],
        "question_window_start": base["question_window_start"],
        "snapshot_as_of": base["snapshot_as_of"],
        "selected_pairs_sha256": canonical_hash(selected),
        "selected_topic_pairs_sha256": {
            name: canonical_hash(rows) for name, rows in selected_by_topic.items()},
        "status": "prompt_and_membership_frozen_before_reply_review_and_model_run",
        "selection_inputs": "company code, question text, QA id and pre-event visibility only",
        "prohibited_selection_inputs": ["reply text", "review labels",
                                        "model outputs", "market outcomes"],
        **audit},
        "selected_pairs": selected,
        "stock_codes": [row["stock_code"] for row in selected]}
    snapshot = {**base,
                "run_id": "wuhan_pre_event_fresh_v6_holdout_2020_v1",
                "universe_config": UNIVERSE_NAME,
                "selection_rule": SELECTION_RULE}
    return universe, snapshot


def verify_existing(universe_path: Path, snapshot_path: Path) -> dict:
    expected_universe, expected_snapshot = build_configs()
    actual_universe, actual_snapshot = _json(universe_path), _json(snapshot_path)
    if actual_universe != expected_universe or actual_snapshot != expected_snapshot:
        raise ValueError("sixth cohort differs from deterministic source reconstruction")
    return {"companies": len(actual_universe["stock_codes"]),
            "selected_pairs_sha256": canonical_hash(actual_universe["selected_pairs"]),
            "prompt_sha256": actual_universe["selection_audit"]["prompt_sha256"],
            "universe_sha256": file_sha256(universe_path),
            "snapshot_sha256": file_sha256(snapshot_path)}


def write_configs() -> dict:
    universe_path = CONFIG_DIR / UNIVERSE_NAME
    snapshot_path = CONFIG_DIR / SNAPSHOT_NAME
    if universe_path.exists() or snapshot_path.exists():
        raise FileExistsError("sixth cohort already exists; verify instead of overwriting")
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
            raise FileExistsError("sixth cohort appeared while staging")
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
    group.add_argument("--audit-pool", action="store_true")
    group.add_argument("--write-configs", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = (audit_pool() if args.audit_pool else
              write_configs() if args.write_configs else
              verify_existing(CONFIG_DIR / UNIVERSE_NAME, CONFIG_DIR / SNAPSHOT_NAME))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
