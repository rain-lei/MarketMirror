"""Prepare a question-topic-balanced fifth cohort after the v5 prompt is frozen.

Selection sees only pre-event eligible questions and six-digit company codes.
It excludes four used cohorts before deterministic ranking; no reply, label or
market outcome is read to choose companies.
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
from typing import Any

from ..data_pipeline.provenance import file_sha256
from .annotation_pack import canonical_hash, digest
from .prepare_wuhan_v4_holdout import verify_existing as verify_v4_cohort


ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "research/configs"
BASE_SNAPSHOT = CONFIG_DIR / "wuhan_pre_event_validation_snapshot_2020.json"
PROMPT = Path(__file__).with_name("PROMPT_WUHAN_V5.md")
PRIOR_UNIVERSES = (
    ("development", CONFIG_DIR / "wuhan_qna_active_universe_2020.json"),
    ("independent_validation", CONFIG_DIR / "wuhan_qna_independent_validation_universe_2020.json"),
    ("consumed_v3_evaluation", CONFIG_DIR / "wuhan_qna_fresh_v3_holdout_universe_2020.json"),
    ("consumed_v4_evaluation", CONFIG_DIR / "wuhan_qna_fresh_v4_holdout_universe_2020.json"),
)
UNIVERSE_NAME = "wuhan_qna_fresh_v5_holdout_universe_2020.json"
SNAPSHOT_NAME = "wuhan_pre_event_fresh_v5_holdout_snapshot_2020.json"
SEED = "marketmirror-wuhan-v5-fifth-question-topic-evaluation-20260929"
SELECTION_RULE = "question_topic_stratified_sha256_after_excluding_four_cohorts"
SAMPLE_SIZE = 126
QUESTION_TERMS = {
    "policy": ("政策", "新规", "监管", "合规", "证监会", "问询", "审批", "许可", "批复", "处罚"),
    "liquidity": ("现金流", "流动性", "债务", "负债", "融资", "偿债", "贷款", "授信", "债券", "质押"),
    "governance": ("分红", "回购", "减持", "股息", "股东回报", "违规", "诉讼", "内控",
                   "治理", "审计", "披露", "董事", "股权"),
    "pandemic": ("疫情", "新冠", "病毒", "口罩", "检测"),
    "earnings": ("业绩", "利润", "营收", "收入", "盈利", "订单", "销量", "毛利"),
}
TOPIC_QUOTAS = {"policy": 24, "liquidity": 24, "governance": 20,
                "pandemic": 16, "earnings": 24, "general": 18}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path.name}")
    return value


def _base_and_frame() -> tuple[dict[str, Any], set[str], dict[str, str]]:
    base = _json(BASE_SNAPSHOT)
    expected = {
        "run_id": "wuhan_pre_event_disjoint_company_validation_source_2020",
        "qa_source_sha256": "a3a1b0afa2cd13b25a56c89393178d93960cd8867a46a7c469fd567bf82ac812",
        "question_window_start": "2020-01-01",
        "snapshot_as_of": "2020-01-22T23:59:59.999999+08:00",
        "selection_rule": "latest_company_confirmed_reply_per_asset_before_cutoff",
    }
    if any(base.get(key) != value for key, value in expected.items()):
        raise ValueError("fifth cohort base snapshot differs from the fixed pre-event source")
    database = (BASE_SNAPSHOT.parent / base["qa_database"]).resolve()
    qa_manifest_path = database.parent / "run_manifest.json"
    qa_manifest = _json(qa_manifest_path)
    matches = [row for row in qa_manifest.get("sources", [])
               if row.get("sha256") == base["qa_source_sha256"]]
    if len(matches) != 1:
        raise ValueError("fifth cohort QA workbook is absent or ambiguous in source manifest")
    source_path = Path(matches[0]["path"])
    if not source_path.is_absolute():
        source_path = (qa_manifest_path.parent / source_path).resolve()
    hashes = {"source_database_sha256": file_sha256(database),
              "source_file_sha256": file_sha256(source_path),
              "source_manifest_sha256": file_sha256(qa_manifest_path)}
    if (hashes["source_file_sha256"] != base["qa_source_sha256"]
            or qa_manifest.get("artifacts", {}).get(database.name, {}).get("sha256")
            != hashes["source_database_sha256"]):
        raise ValueError("fifth cohort workbook or database differs from its provenance")
    lower = base["question_window_start"] + "T00:00:00.000000+08:00"
    query = ("SELECT DISTINCT stock_code FROM qa_record WHERE source_file_hash=? "
             "AND question_eligible=1 AND stock_code IS NOT NULL "
             "AND question_available_at>=? AND question_available_at<=?")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        candidates = {row[0] for row in connection.execute(
            query, (hashes["source_file_sha256"], lower, base["snapshot_as_of"]))
            if isinstance(row[0], str) and re.fullmatch(r"\d{6}", row[0])}
    return base, candidates, hashes


def _prior_groups(base: dict, candidates: set[str]) -> tuple[list[set[str]], list[dict]]:
    verify_v4_cohort(CONFIG_DIR / "wuhan_qna_fresh_v4_holdout_universe_2020.json",
                     CONFIG_DIR / "wuhan_pre_event_fresh_v4_holdout_snapshot_2020.json")
    groups, audit = [], []
    seen: set[str] = set()
    for name, path in PRIOR_UNIVERSES:
        config = _json(path)
        codes = config.get("stock_codes")
        selection = config.get("selection", config.get("selection_audit"))
        if (not isinstance(codes, list) or len(codes) != SAMPLE_SIZE
                or len(set(codes)) != SAMPLE_SIZE
                or not isinstance(selection, dict)
                or selection.get("sample_size") != SAMPLE_SIZE
                or selection.get("qa_source_sha256") != base["qa_source_sha256"]
                or selection.get("question_window_start") != base["question_window_start"]
                or selection.get("snapshot_as_of") != base["snapshot_as_of"]
                or any(code not in candidates for code in codes)):
            raise ValueError(f"prior {name} company cohort differs from fixed source frame")
        group = set(codes)
        if seen & group:
            raise ValueError("prior company cohorts overlap")
        seen |= group
        groups.append(group)
        audit.append({"name": name, "config": path.relative_to(ROOT).as_posix(),
                      "config_sha256": file_sha256(path),
                      "companies": len(group),
                      "stock_codes_sha256": canonical_hash(sorted(group)),
                      "selection_seed": selection.get("seed")})
    return groups, audit


def _question_topics(base: dict, eligible: set[str]) -> dict[str, set[str]]:
    database = (BASE_SNAPSHOT.parent / base["qa_database"]).resolve()
    lower = base["question_window_start"] + "T00:00:00.000000+08:00"
    query = ("SELECT stock_code,question_text FROM qa_record WHERE source_file_hash=? "
             "AND question_eligible=1 AND question_available_at>=? "
             "AND question_available_at<=?")
    topics = {code: set() for code in eligible}
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        for code, question in connection.execute(
                query, (base["qa_source_sha256"], lower, base["snapshot_as_of"])):
            if code not in topics or not isinstance(question, str):
                continue
            for topic, terms in QUESTION_TERMS.items():
                if any(term in question for term in terms):
                    topics[code].add(topic)
    return topics


def _topic_counts(topics: dict[str, set[str]]) -> dict[str, int]:
    return {name: sum(name in value for value in topics.values())
            for name in QUESTION_TERMS} | {
                "general": sum(not value for value in topics.values())}


def select_topic_codes(topics: dict[str, set[str]], seed: str = SEED,
                       quotas: dict[str, int] | None = None) -> tuple[list[str], dict[str, list[str]]]:
    """Use question-only strata; a company appears in at most one quota."""
    quotas = TOPIC_QUOTAS if quotas is None else quotas
    if (set(quotas) != set(QUESTION_TERMS) | {"general"}
            or any(type(value) is not int or value <= 0 for value in quotas.values())
            or not isinstance(seed, str) or not seed.strip()):
        raise ValueError("fifth cohort topic quotas or seed are invalid")
    remaining = set(topics)
    selected: dict[str, list[str]] = {}
    for name, quota in quotas.items():
        candidates = (code for code in remaining
                      if (name in topics[code] if name != "general" else not topics[code]))
        ranked = sorted(candidates, key=lambda code: (digest(f"{seed}|{name}|company|{code}"), code))
        if len(ranked) < quota:
            raise ValueError(f"too few pre-event question-active companies for {name} quota")
        selected[name] = ranked[:quota]
        remaining.difference_update(selected[name])
    codes = [code for name in quotas for code in selected[name]]
    if len(codes) != sum(quotas.values()) or len(set(codes)) != len(codes):
        raise AssertionError("fifth cohort topic sampling repeated a company")
    return codes, selected


def audit_pool() -> dict[str, Any]:
    """Prove the question-only eligible frame without choosing v5 members."""
    base, candidates, hashes = _base_and_frame()
    groups, prior = _prior_groups(base, candidates)
    excluded = set().union(*groups)
    question_topics = _question_topics(base, candidates - excluded)
    return {"source_frame_companies": len(candidates),
            "prior_cohorts": prior,
            "excluded_company_count": len(excluded),
            "excluded_stock_codes_sha256": canonical_hash(sorted(excluded)),
            "eligible_company_count": len(candidates - excluded),
            "question_topic_company_counts": _topic_counts(question_topics),
            "question_topic_quotas": TOPIC_QUOTAS,
            "question_topic_terms_sha256": canonical_hash(QUESTION_TERMS),
            "source_frame_stock_codes_sha256": canonical_hash(sorted(candidates)),
            "selection_inputs": "pre-event eligible question text and active company codes only",
            "prohibited_selection_inputs": ["reply text", "review labels", "model outputs",
                                            "market outcomes"],
            "source_hashes": hashes}


def build_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    if not PROMPT.is_file():
        raise FileNotFoundError("freeze PROMPT_WUHAN_V5.md before selecting the fifth cohort")
    base, candidates, hashes = _base_and_frame()
    groups, prior = _prior_groups(base, candidates)
    excluded = set().union(*groups)
    eligible = candidates - excluded
    if len(eligible) < SAMPLE_SIZE or sum(TOPIC_QUOTAS.values()) != SAMPLE_SIZE:
        raise ValueError("fifth cohort eligible frame is too small")
    question_topics = _question_topics(base, eligible)
    selected, selected_by_topic = select_topic_codes(question_topics)
    if len(selected) != SAMPLE_SIZE or set(selected) & excluded:
        raise ValueError("fifth cohort selection is incomplete or overlaps prior cohorts")
    universe = {"selection_audit": {
        "version": "wuhan-v5-fifth-company-selection-v1",
        "selection_rule": SELECTION_RULE,
        "seed": SEED, "sample_size": SAMPLE_SIZE,
        "prompt_path": PROMPT.relative_to(ROOT).as_posix(),
        "prompt_sha256": file_sha256(PROMPT),
        "qa_source_sha256": base["qa_source_sha256"],
        "question_window_start": base["question_window_start"],
        "snapshot_as_of": base["snapshot_as_of"],
        "source_frame_companies": len(candidates),
        "excluded_cohorts": prior,
        "excluded_company_count": len(excluded),
        "excluded_stock_codes_sha256": canonical_hash(sorted(excluded)),
        "remaining_candidate_companies": len(eligible),
        "question_topic_company_counts": _topic_counts(question_topics),
        "question_topic_quotas": TOPIC_QUOTAS,
        "question_topic_terms_sha256": canonical_hash(QUESTION_TERMS),
        "selected_topic_stock_codes_sha256": {
            name: canonical_hash(codes) for name, codes in selected_by_topic.items()},
        "selected_stock_codes_sha256": canonical_hash(selected),
        "status": "prompt_and_membership_frozen_before_reply_review_and_model_run",
        "selection_inputs": "pre-event eligible question text and active six-digit company codes only",
        "prohibited_selection_inputs": ["reply text", "review labels", "model outputs",
                                        "market outcomes"],
        **hashes},
        "stock_codes": selected}
    snapshot = {**base, "run_id": "wuhan_pre_event_fresh_v5_holdout_2020_v1",
                "universe_config": UNIVERSE_NAME}
    return universe, snapshot


def verify_existing(universe_path: Path, snapshot_path: Path) -> dict[str, Any]:
    expected_universe, expected_snapshot = build_configs()
    actual_universe, actual_snapshot = _json(universe_path), _json(snapshot_path)
    if actual_universe != expected_universe or actual_snapshot != expected_snapshot:
        raise ValueError("fifth cohort config differs from deterministic source reconstruction")
    return {"companies": len(actual_universe["stock_codes"]),
            "excluded_companies": actual_universe["selection_audit"]["excluded_company_count"],
            "eligible_companies": actual_universe["selection_audit"]["remaining_candidate_companies"],
            "prompt_sha256": actual_universe["selection_audit"]["prompt_sha256"],
            "universe_sha256": file_sha256(universe_path),
            "snapshot_sha256": file_sha256(snapshot_path),
            "selected_codes_sha256": canonical_hash(actual_universe["stock_codes"])}


def write_configs() -> dict[str, Any]:
    universe_path = CONFIG_DIR / UNIVERSE_NAME
    snapshot_path = CONFIG_DIR / SNAPSHOT_NAME
    if universe_path.exists() or snapshot_path.exists():
        raise FileExistsError("fifth cohort config already exists; verify instead of overwriting")
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
            raise FileExistsError("fifth cohort config appeared while staging")
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
    parser.add_argument("--universe", type=Path, default=CONFIG_DIR / UNIVERSE_NAME)
    parser.add_argument("--snapshot-config", type=Path, default=CONFIG_DIR / SNAPSHOT_NAME)
    args = parser.parse_args()
    if args.audit_pool:
        result = audit_pool()
    else:
        result = (write_configs() if args.write_configs else
                  verify_existing(args.universe, args.snapshot_config))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
