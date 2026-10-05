"""Rebuild archived as-of QA feature CSVs and audit real-source isolation.

Only aggregate counts and file hashes are retained. The audit never copies
question/reply text into its output and does not permit financial snapshots,
source-quarter hints or future replies in exported feature columns.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import tempfile
from pathlib import Path

from .aggregate_qa_features import KEYWORDS
from .asof_features import export_asof_features
from .build_dataset import POLICY_PATH, VERSION as DATASET_VERSION
from .provenance import file_sha256


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "research_outputs/unified"
DATABASE = DATA_DIR / "dataset.sqlite"
DATASET_MANIFEST = DATA_DIR / "run_manifest.json"
EXPORT_NAMES = ("qa_asof_2020-01-23.csv", "qa_asof_2026-04-01.csv")
OUTPUT = DATA_DIR / "asof_feature_isolation_audit_v1.json"
BASE_COLUMNS = ("stock_code", "window_start", "as_of", "question_count",
                "known_reply_count", "no_visible_reply_count", "known_reply_share",
                "question_length_mean", "reply_length_mean")
INPUT_WHITELIST = {"stock_code", "question_available_at", "question_text",
                   "reply_available_at", "reply_text"}
REQUIRED_EXCLUSIONS = {"company_violation_label", "source_quarter", "financial_snapshot",
                       "p", "p/e", "eventual_reply_status_counts", "full_quarter_features",
                       "user_name", "user_type", "company_name"}


def allowed_columns() -> list[str]:
    return [*BASE_COLUMNS, *(f"{prefix}_{category}_{measure}"
                             for prefix in ("question", "reply")
                             for category in KEYWORDS
                             for measure in ("count", "share"))]


def check_rows(rows: list[dict[str, str]], sql_counts: dict[str, tuple[int, int]],
               *, start: str, cutoff: str) -> dict[str, int]:
    if len(rows) != len(sql_counts):
        raise ValueError("feature company count differs from as-of SQL")
    seen = set()
    question_total = reply_total = 0
    for row in rows:
        code = row["stock_code"]
        if (code in seen or code not in sql_counts
                or row["window_start"] != start or row["as_of"] != cutoff):
            raise ValueError("feature company identity or cutoff differs")
        seen.add(code)
        question, reply = sql_counts[code]
        if (int(row["question_count"]) != question
                or int(row["known_reply_count"]) != reply
                or int(row["no_visible_reply_count"]) != question - reply
                or float(row["known_reply_share"]) != round(reply / question, 6)):
            raise ValueError("feature counts differ from independent as-of SQL")
        question_total += question
        reply_total += reply
    return {"companies": len(seen), "questions": question_total,
            "visible_replies": reply_total}


def _asof_counts(connection: sqlite3.Connection, start: str,
                 cutoff: str) -> dict[str, tuple[int, int]]:
    query = (
        "SELECT stock_code, COUNT(*), "
        "SUM(CASE WHEN reply_eligible = 1 AND reply_available_at <= ? "
        "THEN 1 ELSE 0 END) FROM qa_record "
        "WHERE question_eligible = 1 AND question_available_at >= ? "
        "AND question_available_at <= ? GROUP BY stock_code ORDER BY stock_code"
    )
    result = {}
    for code, questions, replies in connection.execute(query, (cutoff, start, cutoff)):
        if (not isinstance(code, str) or not code or type(questions) is not int
                or type(replies) is not int or not 0 <= replies <= questions):
            raise ValueError("as-of database returned invalid company counts")
        result[code] = (questions, replies)
    return result


def compute() -> dict:
    dataset_manifest = json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    database_hash = file_sha256(DATABASE)
    policy_hash = file_sha256(POLICY_PATH)
    if (dataset_manifest.get("pipeline_version") != DATASET_VERSION
            or database_hash != dataset_manifest.get("artifacts", {}).get("dataset.sqlite", {}).get("sha256")
            or policy_hash != dataset_manifest.get("feature_policy_sha256")
            or set(policy.get("allowed_inputs", [])) != INPUT_WHITELIST
            or not REQUIRED_EXCLUSIONS <= set(policy.get("excluded", []))):
        raise ValueError("dataset, policy or source whitelist differs")
    with sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        if (connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
            raise ValueError("unified dataset integrity differs")
        metadata = {key: json.loads(value) for key, value in
                    connection.execute("SELECT key, value_json FROM dataset_metadata")}
        if any(metadata.get(key) != dataset_manifest.get(key)
               for key in ("dataset_id", "pipeline_version", "feature_policy_sha256", "code_sha256")):
            raise ValueError("dataset metadata differs from its manifest")
        financial = connection.execute(
            "SELECT COUNT(*), SUM(report_period_end IS NOT NULL), "
            "SUM(published_at IS NOT NULL), SUM(verification_status != 'unverified') "
            "FROM financial_snapshot").fetchone()
        if financial != (dataset_manifest["counts"]["financial_snapshots"], 0, 0, 0):
            raise ValueError("financial quarantine status differs")
        results = []
        for name in EXPORT_NAMES:
            csv_path = DATA_DIR / name
            sidecar_path = DATA_DIR / (name + ".manifest.json")
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if (sidecar.get("feature_version") != "qa-asof-v1"
                    or sidecar.get("dataset_id") != dataset_manifest["dataset_id"]
                    or sidecar.get("dataset_sha256") != database_hash
                    or sidecar.get("feature_policy_sha256") != policy_hash
                    or sidecar.get("stock_code_filter") is not None
                    or sidecar.get("feature_columns") != allowed_columns()
                    or file_sha256(csv_path) != sidecar.get("output_sha256")):
                raise ValueError("archived feature output identity or columns differ")
            for module_name, digest in sidecar["code_sha256"].items():
                if module_name not in {"asof_features.py", "aggregate_qa_features.py", "build_dataset.py"} \
                        or file_sha256(Path(__file__).with_name(module_name)) != digest:
                    raise ValueError("feature export code differs from archived code")
            start, cutoff = sidecar["window_start"], sidecar["as_of"]
            sql_counts = _asof_counts(connection, start, cutoff)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != allowed_columns():
                    raise ValueError("CSV exports unexpected or restricted columns")
                checked = check_rows(list(reader), sql_counts, start=start, cutoff=cutoff)
            if (checked != {"companies": sidecar["company_rows"],
                            "questions": sidecar["question_rows"],
                            "visible_replies": sidecar["visible_reply_rows"]}):
                raise ValueError("independent source counts differ from export manifest")
            # A fresh export tests all derived lengths and keyword features,
            # while SQL above independently checks the time gates and counts.
            with tempfile.TemporaryDirectory(prefix="marketmirror_asof_audit_") as temp:
                fresh_path = Path(temp) / name
                fresh = export_asof_features(DATABASE, fresh_path, start, cutoff)
                if (file_sha256(fresh_path) != sidecar["output_sha256"]
                        or fresh["company_rows"] != checked["companies"]
                        or fresh["question_rows"] != checked["questions"]
                        or fresh["visible_reply_rows"] != checked["visible_replies"]):
                    raise ValueError("fresh feature export differs from archived output")
            results.append({"name": name, "window_start": start, "as_of": cutoff,
                            "feature_columns": len(allowed_columns()), **checked,
                            "archived_csv_sha256": sidecar["output_sha256"],
                            "fresh_export_equal": True})
    if file_sha256(DATABASE) != database_hash:
        raise RuntimeError("dataset changed during feature isolation audit")
    return {"pipeline_version": "qa-asof-feature-isolation-audit-v1",
            "dataset_id": dataset_manifest["dataset_id"],
            "source_sha256": {"database": database_hash,
                              "dataset_manifest": file_sha256(DATASET_MANIFEST),
                              "feature_policy": policy_hash,
                              "audit_code": file_sha256(Path(__file__))},
            "financial_snapshots": financial[0],
            "financial_values_verified_for_prediction": False,
            "exports": results,
            "interpretation": "Both archived as-of text-only CSVs exactly match fresh exports and independent SQL counts at their respective cutoffs. This verifies the present pipeline and artifacts exclude financial snapshots and source-quarter hints. It does not establish actual publication times, future model performance or financial field meanings."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    if args.audit_existing:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != result:
            raise ValueError("feature isolation archive differs from recomputation")
    else:
        if OUTPUT.exists():
            raise ValueError("feature isolation output already exists")
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    print(json.dumps({"financial_snapshots": result["financial_snapshots"],
                      "exports": result["exports"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
