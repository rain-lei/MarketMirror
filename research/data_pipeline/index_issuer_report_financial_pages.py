"""Build a full-cohort financial statement review queue from original reports."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import fitz

from . import financial_report_pages

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/issuer_q3_reports_raw_2019_v1/manifest.json"
OUTPUT = ROOT / "research_outputs/issuer_q3_financial_page_index_2019_v1.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute() -> dict:
    manifest = json.loads(SOURCE.read_text(encoding="utf-8"))
    bindings = {str(SOURCE): digest(SOURCE),
                **{str(ROOT / key): value for key, value in {**manifest["inputs"], **manifest["code_sha256"]}.items()}}
    metadata_audit = ROOT / "research_outputs/issuer_disclosure_inventory_audit_2019_2020_v1.json"
    audit = json.loads(metadata_audit.read_text(encoding="utf-8"))
    bindings[str(metadata_audit)] = digest(metadata_audit)
    bindings[str(ROOT / "research/data_pipeline/audit_issuer_disclosure_inventory.py")] = audit["code_sha256"]
    for row in manifest["reports"].values():
        if row["status"] != "FETCHED":
            raise ValueError("full-cohort financial review index requires every selected raw PDF")
        bindings[str(ROOT / row["original_pdf"]["archive_path"])] = row["original_pdf"]["sha256"]

    def verify() -> None:
        if any(digest(Path(name)) != value for name, value in bindings.items()):
            raise ValueError("financial report review index source, producer or PDF changed")

    verify()
    companies = []
    for code, saved in sorted(manifest["reports"].items()):
        path = ROOT / saved["original_pdf"]["archive_path"]
        with fitz.open(path) as document:
            pages = [page.get_text() for page in document]
        index = financial_report_pages.locate_balance_sheets(pages)
        companies.append({"stock_code": code, "historical_short_name": saved["historical_short_name"],
                          "industry_code": saved["industry_code"], "report_metadata": saved["report_metadata"],
                          "original_pdf_path": str(path), "original_pdf_sha256": saved["original_pdf"]["sha256"],
                          "pdf_pages": len(pages), **index})
    verify()
    return {"pipeline_version": "literal-full-cohort-financial-statement-page-index-v1",
            "companies": companies, "summary": {"companies": len(companies), "pdf_pages_scanned": sum(row["pdf_pages"] for row in companies),
                "review_queue_status_counts": dict(Counter(row["review_queue_status"] for row in companies)),
                "balance_sheet_scope_counts": dict(Counter(section["scope_heading"] for row in companies for section in row["balance_sheet_sections"])),
                "balance_sheet_unit_status_counts": dict(Counter(section["unit_status"] for row in companies for section in row["balance_sheet_sections"])),
                "literal_unit_counts": dict(Counter(unit for row in companies for section in row["balance_sheet_sections"] for unit in section["distinct_reported_units"]))},
            "inputs": dict(sorted(bindings.items())),
            "code_sha256": {str(Path(__file__).resolve()): digest(Path(__file__)),
                            str(Path(financial_report_pages.__file__).resolve()): digest(Path(financial_report_pages.__file__))},
            "pdf_parser_version": fitz.VersionBind, "financial_values_verified": False, "agent_signal_enabled": False,
            "interpretation": "Literal headings, date/unit evidence and full statement lines only. Column order, current consolidated numbers, applicability and exposure remain to be verified; no ratio or zero-filled financial feature is exported."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    value = compute()
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("financial statement page index differs from frozen raw reconstruction")
        print("Full-cohort financial statement page index rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(value["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
