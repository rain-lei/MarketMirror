"""Fresh half-year balances with source-grounded note-reference separation."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import fitz

from . import financial_balance_sheet as balance
from . import half_year_balance_context_v2 as context
from .extract_issuer_half_balance_sheets import run_reports
from .extract_issuer_half_balance_sheets_v2 import source_qc
from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_balance_sheets_2019_v3.json"
OUTPUT = ROOT / "research_outputs/issuer_half_balance_sheet_candidates_2019_v3.json"


def extract_report(code, report, literal, cfg):
    pdf = report["original_pdf"]
    common = {"stock_code": code, "historical_short_name": report["historical_short_name"],
              "industry_code": report["industry_code"], "report_metadata": report["report_metadata"],
              "source_report_status": report["status"], "literal_reader_status": literal["status"],
              "source_pdf_path": pdf["archive_path"] if pdf else None,
              "source_pdf_sha256": pdf["sha256"] if pdf else None, "agent_signal_enabled": False}
    if report["status"] != "FETCHED":
        return {**common, "status": "SOURCE_MISSING_NOT_ZERO", "candidates": [], "selected_candidate_index": None,
                "source_issue": report["error"]}
    meta = report["report_metadata"]
    date, snapshot = map(datetime.fromisoformat, (meta["available_at_proxy"], cfg["snapshot_at"]))
    if (meta["stock_code"] != code or meta["report_end_date"] != cfg["report_end_date"]
            or date.utcoffset() is None or snapshot.utcoffset() is None or date > snapshot):
        raise ValueError("half v3 source issuer, period or availability differs")
    with fitz.open(ROOT / pdf["archive_path"]) as document:
        rows = [row for n, page in enumerate(document, 1)
                for row in balance.geometry_rows(page.get_text("words"), n, page.rect.height)]
    return {**common, **context.extract_company(rows, cfg["report_end_date"])}


def compute():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (cfg["agent_signal_enabled"] is not False or cfg["report_end_date"] != "2019-06-30"
            or fitz.VersionBind != cfg["pymupdf_version"] or cfg["field_labels"] != {k: list(v) for k, v in balance.FIELDS.items()}):
        raise ValueError("half v3 protocol differs")
    manifest = json.loads((ROOT / cfg["report_manifest"]).read_text(encoding="utf-8"))
    literal = json.loads((ROOT / cfg["literal_audit_path"]).read_text(encoding="utf-8"))
    literals = {c["stock_code"]: c for c in literal["companies"]}
    if len(literals) != len(literal["companies"]) or len(manifest["reports"]) != cfg["expected_companies"]:
        raise ValueError("half v3 cohort differs")
    bindings = {**manifest["inputs"], **manifest["code_sha256"], **literal["inputs"], **cfg["inputs"],
                CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG),
                "research/data_pipeline/audit_issuer_business_evidence.py": literal["audit_code_sha256"]}
    for report in manifest["reports"].values():
        if report["original_pdf"]:
            bindings[report["original_pdf"]["archive_path"]] = report["original_pdf"]["sha256"]
    names = ("extract_issuer_half_balance_sheets_v3", "extract_issuer_half_balance_sheets_v2",
             "extract_issuer_half_balance_sheets", "half_year_balance_context_v2", "half_year_balance_context",
             "financial_balance_sheet", "fetch_issuer_disclosures")
    code = {f"research/data_pipeline/{name}.py": digest(ROOT / f"research/data_pipeline/{name}.py") for name in names}
    validate_bindings({**bindings, **code})
    companies = run_reports(manifest["reports"], literals, cfg, extract_report)
    validate_bindings({**bindings, **code})
    selected = [c["candidates"][c["selected_candidate_index"]] for c in companies if c["status"] == "UNIQUE_RECONCILED_CANDIDATE"]
    qc = source_qc(companies)
    summary = {"companies": len(companies), "company_status_counts": dict(Counter(c["status"] for c in companies)),
               "selected_scope_counts": dict(Counter(t["scope_heading"] for t in selected)),
               "selected_currency_counts": dict(Counter(t["currency_declaration"]["status"] for t in selected)),
               "source_reader_qc_companies": len(qc),
               "current_numeric_field_cells": sum(t["fields"][f]["cells"][t["current_column_index"]]["status"] == "NUMERIC"
                                                  for t in selected for f in balance.FIELDS),
               "separated_note_reference_words": sum(len(c.get("financial_note_references_retained_separately", [])) for c in companies)}
    return {"pipeline_version": cfg["version"], "companies": companies, "summary": summary,
            "source_reader_qc_issuers": qc, "inputs": dict(sorted(bindings.items())), "code_sha256": code,
            "pymupdf_version": fitz.VersionBind, "snapshot_at": cfg["snapshot_at"],
            "financial_values_independently_audited": False, "agent_signal_enabled": False, "interpretation": cfg["interpretation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("half balance v3 output must be directly under research_outputs")
    value = compute()
    raw = serialize(value)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("half balance v3 reconstruction differs")
        print("Half-year balance v3 rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(value["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
