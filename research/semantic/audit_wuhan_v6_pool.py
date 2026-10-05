"""Audit unused pre-event Wuhan Q&A capacity without selecting a sixth cohort.

Only company codes, question text, timestamps, and reply availability flags are
read. No reply text, review label, model response, or market outcome is inspected.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing

from .annotation_pack import canonical_hash
from .prepare_wuhan_v5_holdout import (
    BASE_SNAPSHOT, CONFIG_DIR, QUESTION_TERMS, SNAPSHOT_NAME, UNIVERSE_NAME,
    _base_and_frame, _json, _prior_groups, _question_topics, _topic_counts,
    verify_existing,
)


def audit_pool() -> dict:
    base, candidates, source_hashes = _base_and_frame()
    prior_groups, prior_audit = _prior_groups(base, candidates)
    fifth = verify_existing(CONFIG_DIR / UNIVERSE_NAME, CONFIG_DIR / SNAPSHOT_NAME)
    fifth_codes = set(_json(CONFIG_DIR / UNIVERSE_NAME)["stock_codes"])
    excluded = set().union(*prior_groups)
    if (len(fifth_codes) != fifth["companies"] or fifth_codes & excluded
            or not fifth_codes <= candidates):
        raise ValueError("fifth cohort overlaps prior cohorts or source frame")
    excluded |= fifth_codes
    eligible = candidates - excluded
    topics = _question_topics(base, eligible)

    database = (BASE_SNAPSHOT.parent / base["qa_database"]).resolve()
    lower = base["question_window_start"] + "T00:00:00.000000+08:00"
    query = (
        "SELECT stock_code,question_text,question_available_at,"
        "reply_eligible,reply_available_at "
        "FROM qa_record WHERE source_file_hash=? AND question_eligible=1 "
        "AND question_available_at>=? AND question_available_at<=?"
    )
    visible_reply_rows = 0
    visible_reply_companies: set[str] = set()
    visible_question_topic_rows: Counter[str] = Counter()
    visible_question_topic_companies: dict[str, set[str]] = {
        name: set() for name in (*QUESTION_TERMS, "general")}
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        for code, question, question_at, reply_eligible, reply_at in connection.execute(
                query, (base["qa_source_sha256"], lower, base["snapshot_as_of"])):
            if (code not in eligible or reply_eligible != 1
                    or not isinstance(question_at, str)
                    or not isinstance(reply_at, str) or reply_at < question_at
                    or reply_at > base["snapshot_as_of"]):
                continue
            visible_reply_rows += 1
            visible_reply_companies.add(code)
            matched = False
            if isinstance(question, str):
                for name, terms in QUESTION_TERMS.items():
                    if any(term in question for term in terms):
                        visible_question_topic_rows[name] += 1
                        visible_question_topic_companies[name].add(code)
                        matched = True
            if not matched:
                visible_question_topic_rows["general"] += 1
                visible_question_topic_companies["general"].add(code)

    return {
        "purpose": "sixth_cohort_pool_audit_only_no_membership_selected",
        "source_frame_companies": len(candidates),
        "excluded_first_five_company_count": len(excluded),
        "eligible_company_count": len(eligible),
        "eligible_stock_codes_sha256": canonical_hash(sorted(eligible)),
        "eligible_question_topic_company_counts": _topic_counts(topics),
        "eligible_visible_reply_companies": len(visible_reply_companies),
        "eligible_visible_reply_rows": visible_reply_rows,
        "visible_reply_question_topic_row_counts_overlapping": dict(visible_question_topic_rows),
        "visible_reply_question_topic_company_counts_overlapping": {
            name: len(codes) for name, codes in visible_question_topic_companies.items()},
        "fifth_cohort_selected_stock_codes_sha256": fifth["selected_codes_sha256"],
        "prior_cohorts": prior_audit,
        "source_hashes": source_hashes,
        "selection_inputs_allowed": ["pre-event eligible question text", "company code"],
        "selection_inputs_not_read": ["reply text", "review labels", "model outputs",
                                      "market outcomes"],
    }


if __name__ == "__main__":
    print(json.dumps(audit_pool(), ensure_ascii=False))
