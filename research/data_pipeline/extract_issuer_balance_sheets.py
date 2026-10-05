"""Reconstruct full-cohort current balance-sheet candidates from pinned PDFs."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import fitz

from . import financial_balance_sheet as reader

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_q3_balance_sheets_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_q3_balance_sheet_candidates_2019_v1.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute() -> dict:
    protocol = json.loads(CONFIG.read_text(encoding="utf-8"))
    if protocol["field_labels"] != {k: list(v) for k, v in reader.FIELDS.items()} or protocol["agent_signal_enabled"] is not False:
        raise ValueError("balance field definitions or disabled economic gate changed")
    source = ROOT / protocol["report_manifest"]
    manifest = json.loads(source.read_text(encoding="utf-8"))
    bindings = {str(CONFIG): digest(CONFIG), **{str(ROOT / path): value for path, value in protocol["inputs"].items()},
                **{str(ROOT / path): value for path, value in {**manifest["inputs"], **manifest["code_sha256"]}.items()}}
    reports = manifest["reports"]
    if len(reports) != protocol["expected_companies"]:
        raise ValueError("balance-sheet cohort changed")
    for report in reports.values():
        if report["status"] != "FETCHED":
            raise ValueError("original report is not successfully archived")
        bindings[str(ROOT / report["original_pdf"]["archive_path"])] = report["original_pdf"]["sha256"]

    def verify() -> None:
        for name, expected in bindings.items():
            if digest(Path(name)) != expected:
                raise ValueError("financial extraction source, source producer or original PDF changed: " + name)

    verify()
    companies = []
    for code, report in sorted(reports.items()):
        metadata = report["report_metadata"]
        if metadata["stock_code"] != code or metadata["report_end_date"] != protocol["report_end_date"]:
            raise ValueError("selected report is for another issuer or period")
        if datetime.fromisoformat(metadata["available_at_proxy"]) > datetime.fromisoformat(protocol["snapshot_at"]):
            raise ValueError("selected source was not yet available at the frozen snapshot")
        path = ROOT / report["original_pdf"]["archive_path"]
        with fitz.open(path) as document:
            rows = [row for page_number, page in enumerate(document)
                    for row in reader.geometry_rows(page.get_text("words"), page_number + 1, page.rect.height)]
        result = reader.extract_company(rows, protocol["report_end_date"])
        companies.append({"stock_code": code, "historical_short_name": report["historical_short_name"],
                          "industry_code": report["industry_code"], "report_metadata": metadata,
                          "source_pdf_path": report["original_pdf"]["archive_path"],
                          "source_pdf_sha256": report["original_pdf"]["sha256"], **result})
    verify()
    selected = [row["candidates"][row["selected_candidate_index"]] for row in companies
                if row["selected_candidate_index"] is not None]
    summary = {"companies": len(companies), "company_status_counts": dict(Counter(row["status"] for row in companies)),
               "candidate_status_counts": dict(Counter(c["status"] for row in companies for c in row["candidates"])),
               "current_unit_counts": dict(Counter(c["reported_unit"] for c in selected)),
               "current_scope_counts": dict(Counter(c["column_mapping"]["columns"][c["current_column_index"]]["scope"] for c in selected)),
               "column_balance_check_counts": dict(Counter(check["status"] for c in selected for check in c["balance_checks"])),
               "current_cell_status_counts": {field: dict(Counter(c["fields"][field]["cells"][c["current_column_index"]]["status"] for c in selected))
                                              for field in reader.FIELDS}}
    return {"pipeline_version": protocol["version"], "companies": companies, "summary": summary,
            "inputs": dict(sorted(bindings.items())),
            "code_sha256": {str(Path(__file__).resolve()): digest(Path(__file__)), str(Path(reader.__file__).resolve()): digest(Path(reader.__file__))},
            "pdf_parser_version": fitz.VersionBind, "snapshot_at": protocol["snapshot_at"],
            "financial_values_independently_audited": False, "agent_signal_enabled": False,
            "interpretation": "Complete dated balance-sheet candidates with raw labels, page geometry, explicitly mapped scope/date columns, source units and exact Decimal totals. Missing cells remain null; no economic response, certified unchanged historical snapshot or market increment is claimed."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("balance-sheet candidates differ from frozen original-PDF reconstruction")
        print("Full-cohort balance-sheet candidates rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
