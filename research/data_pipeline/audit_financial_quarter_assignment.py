"""Audit the source quarter hint against question and reply dates.

This is an empirical audit of a supplied workbook, not a definition of any
financial reporting period. It reads only the already extracted, source-bound
SQLite fields needed for aggregate timing counts; no question or reply text is
loaded or written.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

from .provenance import file_sha256


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "research_outputs/financial_2020"
DATABASE = DATA_DIR / "financial_unverified.sqlite"
REPORT = DATA_DIR / "financial_quality_report.json"
CATALOG = ROOT / "research/data_contracts/financial_fields.json"
OUTPUT = DATA_DIR / "quarter_assignment_audit_v1.json"
REPLY_BOUNDS = (date(2020, 3, 30), date(2020, 6, 29), date(2020, 9, 29))
BOUNDARY_DAYS = (date(2020, 3, 30), date(2020, 3, 31), date(2020, 4, 1),
                 date(2020, 6, 29), date(2020, 6, 30), date(2020, 7, 1),
                 date(2020, 9, 29), date(2020, 9, 30), date(2020, 10, 1))


def inferred_reply_bucket(reply: date) -> str:
    """Post-hoc rule observed in this particular 2020-labeled workbook."""
    if reply < date(2020, 1, 1):
        raise ValueError("reply precedes the 2020 source window")
    for index, end in enumerate(REPLY_BOUNDS, start=1):
        if reply <= end:
            return str(index)
    return "4"


def _source_date(value: str, name: str) -> date:
    if not isinstance(value, str) or len(value) < 10 or value[4] != "-" or value[7] != "-":
        raise ValueError(f"{name} must begin with an ISO date")
    return date.fromisoformat(value[:10])


def summarize(records: list[tuple[str, str, str]] | sqlite3.Cursor) -> dict:
    counts: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    reply_years: Counter[str] = Counter()
    boundary: Counter[tuple[str, str]] = Counter()
    mismatches = 0
    earliest, latest = None, None
    for source, question_raw, reply_raw in records:
        if source not in {"1", "2", "3", "4"}:
            raise ValueError("source quarter is outside one through four")
        question, reply = (_source_date(question_raw, "question date"),
                           _source_date(reply_raw, "reply date"))
        if question > reply:
            raise ValueError("question date is later than reply date")
        inferred = inferred_reply_bucket(reply)
        counts["rows"] += 1
        by_source[source] += 1
        reply_years[str(reply.year)] += 1
        counts["source_matches_question_calendar_quarter"] += int(
            source == str((question.month - 1) // 3 + 1))
        counts["source_matches_reply_calendar_quarter"] += int(
            source == str((reply.month - 1) // 3 + 1))
        counts["reply_in_2020"] += int(reply.year == 2020)
        if reply.year == 2020:
            counts["source_matches_2020_reply_calendar_quarter"] += int(
                source == str((reply.month - 1) // 3 + 1))
        counts["source_matches_inferred_reply_bucket"] += int(source == inferred)
        mismatches += int(source != inferred)
        if reply in BOUNDARY_DAYS:
            boundary[(reply.isoformat(), source)] += 1
        earliest = reply if earliest is None or reply < earliest else earliest
        latest = reply if latest is None or reply > latest else latest
    if not counts["rows"]:
        raise ValueError("source has no financial reference rows")
    return {
        "source_rows": counts["rows"],
        "source_quarter_counts": dict(sorted(by_source.items())),
        "reply_year_counts": dict(sorted(reply_years.items())),
        "reply_date_range": {"first": earliest.isoformat(), "last": latest.isoformat()},
        "source_matches_question_calendar_quarter": counts[
            "source_matches_question_calendar_quarter"],
        "source_matches_reply_calendar_quarter": counts[
            "source_matches_reply_calendar_quarter"],
        "reply_in_2020": counts["reply_in_2020"],
        "source_matches_2020_reply_calendar_quarter": counts[
            "source_matches_2020_reply_calendar_quarter"],
        "source_matches_inferred_reply_bucket": counts[
            "source_matches_inferred_reply_bucket"],
        "inferred_rule_mismatches": mismatches,
        "boundary_day_source_counts": [
            {"reply_date": day, "source_quarter": quarter, "rows": amount}
            for (day, quarter), amount in sorted(boundary.items())],
    }


def _metadata(connection: sqlite3.Connection, key: str):
    row = connection.execute("SELECT value_json FROM run_metadata WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise ValueError(f"financial extraction metadata lacks {key}")
    return json.loads(row[0])


def compute(source_path: Path | None = None) -> dict:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if (report.get("pipeline_version") != "financial-extract-v1"
            or file_sha256(DATABASE) != report.get("database_sha256")
            or file_sha256(CATALOG) != report.get("catalog_sha256")):
        raise ValueError("financial extraction report, database or catalog hash differs")
    with sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        if (connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
            raise ValueError("financial reference database integrity differs")
        original = Path(_metadata(connection, "source_path"))
        source = source_path.resolve() if source_path else original
        if (source.name != report["source_file"]
                or file_sha256(source) != report["source_file_hash"]
                or _metadata(connection, "source_file_hash") != report["source_file_hash"]
                or _metadata(connection, "catalog_sha256") != report["catalog_sha256"]):
            raise ValueError("source workbook or extraction identity differs")
        status = connection.execute(
            "SELECT COUNT(*), SUM(report_period_end IS NOT NULL), "
            "SUM(published_at IS NOT NULL), SUM(verification_status != 'unverified') "
            "FROM financial_snapshot").fetchone()
        if (status != (report["counts"]["snapshot_count"], 0, 0, 0)
                or connection.execute("SELECT COUNT(*) FROM source_row_reference").fetchone()[0]
                != report["counts"]["source_rows"]):
            raise ValueError("snapshot status or source reference counts differ")
        rows = connection.execute(
            "SELECT s.source_quarter_hint, r.question_time_raw, r.reply_time_raw "
            "FROM source_row_reference r JOIN financial_snapshot s "
            "ON s.snapshot_id = r.snapshot_id ORDER BY r.source_row")
        summary = summarize(rows)
    if (summary["source_rows"] != report["counts"]["source_rows"]
            or summary["source_rows"] - summary["source_matches_question_calendar_quarter"]
            != report["counts"]["question_quarter_mismatches"]):
        raise ValueError("quarter audit differs from the extracted source report")
    return {
        "pipeline_version": "financial-quarter-assignment-audit-v1",
        "source_workbook": report["source_file"],
        "source_sha256": {"workbook": report["source_file_hash"],
                          "financial_database": report["database_sha256"],
                          "financial_report": file_sha256(REPORT),
                          "financial_catalog": report["catalog_sha256"],
                          "audit_code": file_sha256(Path(__file__))},
        "candidate_rule": {
            "status": "post_hoc_observational_fit_not_provider_definition",
            "reply_date_buckets": ["2020-01-01 through 2020-03-30 -> 1",
                                   "2020-03-31 through 2020-06-29 -> 2",
                                   "2020-06-30 through 2020-09-29 -> 3",
                                   "2020-09-30 and later -> 4"]},
        "snapshot_status": {"count": status[0], "all_unverified": True,
                            "report_period_end_populated": 0, "published_at_populated": 0},
        "summary": summary,
        "interpretation": "The source quarter hint exactly follows a reply-date partition in this supplied vintage, including one-day-early quarter boundaries and all later-year replies in bucket 4. This is not evidence that the linked financial values are from those financial reporting quarters or were public at question/reply time. Keep all financial snapshots outside as-of Agent or forecast features pending source definitions, units, reporting periods and publication timestamps."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="source workbook if it moved since extraction")
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute(args.source)
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("quarter assignment archive differs from recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("quarter assignment audit output already exists")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
