"""Export only timestamp-gated text features under an explicit research policy."""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aggregate_qa_features import KEYWORDS
from .build_dataset import POLICY_PATH, VERSION, normalize_timestamp, valid_stock_code
from .provenance import file_sha256


@dataclass
class VisibleTextFeatures:
    questions: int = 0
    replies: int = 0
    question_length: int = 0
    reply_length: int = 0
    question_keywords: Counter = field(default_factory=Counter)
    reply_keywords: Counter = field(default_factory=Counter)

    def add(self, question: str, visible_reply: str | None) -> None:
        self.questions += 1
        self.question_length += len(question)
        for category, words in KEYWORDS.items():
            self.question_keywords[category] += int(any(word in question for word in words))
        if visible_reply is not None:
            self.replies += 1
            self.reply_length += len(visible_reply)
            for category, words in KEYWORDS.items():
                self.reply_keywords[category] += int(any(word in visible_reply for word in words))

    def row(self, code: str, start: str, cutoff: str) -> dict[str, Any]:
        row = {
            "stock_code": code, "window_start": start, "as_of": cutoff,
            "question_count": self.questions, "known_reply_count": self.replies,
            "no_visible_reply_count": self.questions - self.replies,
            "known_reply_share": round(self.replies / self.questions, 6) if self.questions else 0.0,
            "question_length_mean": round(self.question_length / self.questions, 3) if self.questions else None,
            "reply_length_mean": round(self.reply_length / self.replies, 3) if self.replies else None,
        }
        for prefix, counts, denominator in (
            ("question", self.question_keywords, self.questions), ("reply", self.reply_keywords, self.replies)
        ):
            for category in KEYWORDS:
                row[f"{prefix}_{category}_count"] = counts[category]
                row[f"{prefix}_{category}_share"] = round(counts[category] / denominator, 6) if denominator else 0.0
        return row


def export_asof_features(database: Path, output: Path, window_start: str, as_of: str,
                         stock_code: str | None = None) -> dict[str, Any]:
    start, cutoff = normalize_timestamp(window_start), normalize_timestamp(as_of)
    if start is None or cutoff is None or start > cutoff:
        raise ValueError("window_start and as_of must be explicit timestamps with window_start <= as_of")
    requested = valid_stock_code(stock_code) if stock_code is not None else None
    if stock_code is not None and requested is None:
        raise ValueError("invalid stock code")
    database, output = database.resolve(), output.resolve()
    manifest_path = database.parent / "run_manifest.json"
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = source_manifest["artifacts"][database.name]["sha256"]
    database_hash = file_sha256(database)
    if database_hash != expected:
        raise ValueError("dataset hash differs from run manifest")
    policy_hash = file_sha256(POLICY_PATH)
    if source_manifest["feature_policy_sha256"] != policy_hash:
        raise ValueError("feature policy changed; rebuild the dataset before exporting features")
    sidecar = output.with_suffix(output.suffix + ".manifest.json")
    protected = {database, manifest_path, POLICY_PATH}
    protected.update(Path(item["path"]).resolve() for item in source_manifest["sources"])
    protected.update(Path(item["path"]).resolve() for item in source_manifest["financial_inputs"])
    if output in protected or sidecar in protected:
        raise ValueError("feature output must not overwrite dataset inputs or metadata")
    groups = {}
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        metadata = {k: json.loads(v) for k, v in conn.execute("SELECT key, value_json FROM dataset_metadata")}
        if metadata["dataset_id"] != source_manifest["dataset_id"] or metadata["pipeline_version"] != VERSION:
            raise ValueError("dataset metadata differs from run manifest or supported version")
        if metadata["feature_policy_sha256"] != policy_hash or metadata["code_sha256"] != source_manifest["code_sha256"]:
            raise ValueError("dataset provenance differs from the export policy or run manifest")
        if requested:
            if conn.execute("SELECT 1 FROM company WHERE stock_code = ?", (requested,)).fetchone() is None:
                raise ValueError("requested stock code is absent from the dataset")
            groups[requested] = VisibleTextFeatures()
        query = (
            "SELECT stock_code, question_text, "
            "CASE WHEN reply_eligible = 1 AND reply_available_at <= ? THEN reply_text ELSE NULL END "
            "FROM qa_record WHERE question_eligible = 1 AND question_available_at >= ? AND question_available_at <= ?"
        )
        parameters = [cutoff, start, cutoff]
        if requested:
            query += " AND stock_code = ?"
            parameters.append(requested)
        query += " ORDER BY stock_code, question_available_at, qa_id"
        for code, question, reply in conn.execute(query, parameters):
            groups.setdefault(code, VisibleTextFeatures()).add(question, reply)
    if file_sha256(database) != database_hash:
        raise RuntimeError("dataset changed during feature export")
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(VisibleTextFeatures().row("000001", start, cutoff))
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False, suffix=".csv.tmp", mode="w",
                                     encoding="utf-8-sig", newline="") as handle:
        temp_path = Path(handle.name)
        try:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for code in sorted(groups):
                writer.writerow(groups[code].row(code, start, cutoff))
        except Exception:
            handle.close()
            temp_path.unlink(missing_ok=True)
            raise
    try:
        temp_path.replace(output)
    finally:
        temp_path.unlink(missing_ok=True)
    report = {
        "feature_version": "qa-asof-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_id": source_manifest["dataset_id"], "dataset_sha256": database_hash,
        "feature_policy_sha256": policy_hash, "window_start": start, "as_of": cutoff,
        "stock_code_filter": requested, "company_rows": len(groups),
        "question_rows": sum(v.questions for v in groups.values()),
        "visible_reply_rows": sum(v.replies for v in groups.values()),
        "feature_columns": fields, "output_sha256": file_sha256(output),
        "code_sha256": {name: file_sha256(Path(__file__).with_name(name)) for name in (
            "asof_features.py", "aggregate_qa_features.py", "build_dataset.py")},
        "assumptions": source_manifest["assumptions"],
    }
    sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
