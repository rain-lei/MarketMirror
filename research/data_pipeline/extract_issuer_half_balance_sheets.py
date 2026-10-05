"""Read dated June balances for every frozen half-year issuer, retaining QC."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import fitz

from . import financial_balance_sheet as balance
from . import half_year_balance_context as context
from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_balance_sheets_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_balance_sheet_candidates_2019_v1.json"


def extract_report(code: str, report: dict, literal: dict, cfg: dict) -> dict:
    common = {"stock_code": code, "historical_short_name": report["historical_short_name"],
              "industry_code": report["industry_code"], "report_metadata": report["report_metadata"],
              "literal_reader_status": literal["status"], "source_report_status": report["status"],
              "source_pdf_path": report["original_pdf"]["archive_path"] if report["original_pdf"] else None,
              "source_pdf_sha256": report["original_pdf"]["sha256"] if report["original_pdf"] else None,
              "agent_signal_enabled": False}
    if report["status"] != "FETCHED":
        return {**common, "status": "SOURCE_MISSING_NOT_ZERO", "selected_candidate_index": None,
                "candidates": [], "source_issue": report["error"]}
    metadata = report["report_metadata"]
    available, snapshot = map(datetime.fromisoformat, (metadata["available_at_proxy"], cfg["snapshot_at"]))
    if (metadata["stock_code"] != code or metadata["report_end_date"] != cfg["report_end_date"]
            or available.utcoffset() is None or snapshot.utcoffset() is None or available > snapshot):
        raise ValueError("half balance source issuer, period or availability differs")
    with fitz.open(ROOT / common["source_pdf_path"]) as document:
        rows = [row for number, page in enumerate(document, 1)
                for row in balance.geometry_rows(page.get_text("words"), number, page.rect.height)]
    return {**common, **context.extract_company(rows, cfg["report_end_date"])}


def run_reports(reports: dict, literals: dict, cfg: dict, extractor=extract_report) -> list[dict]:
    if set(reports) != set(literals):
        raise ValueError("half balance source/literal cohort association differs")
    companies = []
    for code, report in sorted(reports.items()):
        try:
            company = extractor(code, report, literals[code], cfg)
        except Exception as error:
            company = {"stock_code": code, "historical_short_name": report["historical_short_name"],
                       "industry_code": report["industry_code"], "report_metadata": report["report_metadata"],
                       "literal_reader_status": literals[code]["status"], "source_report_status": report["status"],
                       "source_pdf_path": report["original_pdf"]["archive_path"] if report["original_pdf"] else None,
                       "source_pdf_sha256": report["original_pdf"]["sha256"] if report["original_pdf"] else None,
                       "status": "EXTRACTION_FAILED_REVIEW_REQUIRED", "candidates": [],
                       "selected_candidate_index": None, "error": f"{type(error).__name__}: {error}",
                       "agent_signal_enabled": False}
        companies.append(company)
        print(json.dumps({"stock_code": code, "status": company["status"], "completed": len(companies)},
                         ensure_ascii=False), flush=True)
    return companies


def compute() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (cfg["agent_signal_enabled"] is not False or cfg["report_end_date"] != "2019-06-30"
            or cfg["field_labels"] != {k: list(v) for k, v in balance.FIELDS.items()}
            or fitz.VersionBind != cfg["pymupdf_version"]):
        raise ValueError("half balance extraction protocol differs")
    manifest = json.loads((ROOT / cfg["report_manifest"]).read_text(encoding="utf-8"))
    literal = json.loads((ROOT / cfg["literal_audit_path"]).read_text(encoding="utf-8"))
    literals = {c["stock_code"]: c for c in literal["companies"]}
    if len(literals) != len(literal["companies"]) or len(manifest["reports"]) != cfg["expected_companies"]:
        raise ValueError("half balance fixed cohort differs")
    bindings = {**manifest["inputs"], **manifest["code_sha256"], **literal["inputs"], **cfg["inputs"],
                CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG),
                "research/data_pipeline/audit_issuer_business_evidence.py": literal["audit_code_sha256"]}
    for report in manifest["reports"].values():
        if report["original_pdf"]:
            bindings[report["original_pdf"]["archive_path"]] = report["original_pdf"]["sha256"]
    code = {f"research/data_pipeline/{name}.py": digest(ROOT / f"research/data_pipeline/{name}.py")
            for name in ("extract_issuer_half_balance_sheets", "half_year_balance_context",
                         "financial_balance_sheet", "fetch_issuer_disclosures")}
    validate_bindings({**bindings, **code})
    companies = run_reports(manifest["reports"], literals, cfg)
    validate_bindings({**bindings, **code})
    selected = [c["candidates"][c["selected_candidate_index"]] for c in companies
                if c["status"] == "UNIQUE_RECONCILED_CANDIDATE"]
    summary = {"companies": len(companies), "company_status_counts": dict(Counter(c["status"] for c in companies)),
               "candidate_status_counts": dict(Counter(t["status"] for c in companies for t in c["candidates"])),
               "selected_scope_counts": dict(Counter(t["scope_heading"] for t in selected)),
               "selected_currency_counts": dict(Counter(t["currency_declaration"]["status"] for t in selected)),
               "selected_unit_counts": dict(Counter(t["reported_unit"] for t in selected)),
               "source_reader_qc_companies": sum(c["literal_reader_status"] == "SOURCE_READER_DIAGNOSTICS_REVIEW_REQUIRED" for c in companies),
               "current_numeric_field_cells": sum(t["fields"][f]["cells"][t["current_column_index"]]["status"] == "NUMERIC"
                                                  for t in selected for f in balance.FIELDS)}
    return {"pipeline_version": cfg["version"], "companies": companies, "summary": summary,
            "inputs": dict(sorted(bindings.items())), "code_sha256": code,
            "pymupdf_version": fitz.VersionBind, "snapshot_at": cfg["snapshot_at"],
            "financial_values_independently_audited": False, "agent_signal_enabled": False,
            "interpretation": cfg["interpretation"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("half balance output must be directly under research_outputs")
    result = compute()
    raw = serialize(result)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("half balance reconstruction differs")
        print("Half-year balance candidates rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
