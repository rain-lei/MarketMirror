"""Independently read half-year bordered balances and currency declarations."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pdfplumber

from .audit_issuer_balance_sheets import (ContentTable, _limits, bordered_field_rows,
                                         table_columns, compare_reading, normalized)
from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_balance_audit_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_balance_sheet_audit_2019_v1.json"


class WithoutNoteColumn:
    """Remove only rectangles belonging to a printed 附注 column."""

    def __init__(self, table, note_intervals):
        values = table.extract()
        drop = []
        for i in range(len(values[0])):
            nonempty = [(row[i], geometry.cells[i]) for row, geometry in zip(values, table.rows)
                        if normalized(row[i])]
            if nonempty and all(box is not None and any(abs(box[0] - lo) <= 3 and abs(box[2] - hi) <= 3
                                                       for lo, hi in note_intervals) for _, box in nonempty):
                drop.append(i)
        self.values = [[value for i, value in enumerate(row) if i not in drop] for row in values]
        self.rows = [SimpleNamespace(cells=[box for i, box in enumerate(row.cells) if i not in drop]) for row in table.rows]
        self.bbox = table.bbox
        self.note_column_cells = [{"text": row[i], "rectangle": geometry.cells[i]}
                                  for row, geometry in zip(values, table.rows) for i in drop if normalized(row[i])]

    def extract(self):
        return self.values


def half_table_read(document, candidate: dict, labels: dict) -> dict:
    """Fresh bordered read with an explicitly labelled financial-note column."""
    reverse = {normalized(alias): field for field, aliases in labels.items() for alias in aliases}
    count = len(candidate["column_mapping"]["columns"])
    readings = {field: [] for field in labels}
    mappings, units, headings, omitted, note_intervals = [], [], [], [], []
    final_page = max(row["pdf_page"] for row in candidate["fields"]["liabilities_and_equity"]["rows"][0]["row_evidence"])
    for number in range(candidate["start_pdf_page"], final_page + 1):
        page = document.pages[number - 1]
        lo, hi = _limits(candidate, number, page.height)
        cropped = page.crop((0, lo, page.width, hi))
        text = cropped.extract_text(layout=False) or ""
        headings.extend(text.splitlines()[:18])
        units.extend({"pdf_page": number, "reported_unit": unit} for unit in
                     re.findall(r"(?:货币|金额)?单位(?:[:：]|为)(?:人民币)?(百万元|千元|万元|亿元|元)", normalized(text)))
        for table in cropped.find_tables():
            values = table.extract()
            if not any(normalized(value) in reverse for row in values for value in row):
                continue
            first_field = next(i for i, row in enumerate(values) if any(normalized(value) in reverse for value in row))
            for row, geometry in zip(values[:first_field], table.rows[:first_field]):
                for value, box in zip(row, geometry.cells):
                    if normalized(value) == "附注" and box is not None:
                        pair = (box[0], box[2])
                        if pair not in note_intervals:
                            note_intervals.append(pair)
            filtered = WithoutNoteColumn(table, note_intervals)
            omitted.extend({"pdf_page": number, **item} for item in filtered.note_column_cells)
            mapping = table_columns(ContentTable(filtered), candidate["scope_heading"], count, labels)
            if mapping:
                mappings.append({"pdf_page": number, "columns": mapping, "table_bbox": list(table.bbox)})
            for field, label, rectangles, cells, boundaries in bordered_field_rows(filtered, reverse, count):
                readings[field].append({"pdf_page": number, "source_label": label,
                                        "table_cell_rectangles": rectangles,
                                        "independent_currency_boundaries": boundaries, "cells": cells})
        page.close()
    return {"fields": readings, "mappings": mappings, "unit_evidence": units, "heading_evidence_text": headings,
            "method": "independent_pdfplumber_vector_border_tables",
            "explicit_note_column_cells_retained_separately": omitted}


def check_declaration(document, item: dict) -> dict:
    """Re-read the indicated physical rectangle, not a matching value elsewhere."""
    page = document.pages[item["pdf_page"] - 1]
    x0, y0, x1, y1 = item["bbox"]
    cropped = page.crop((max(0, x0 - 2), max(0, y0 - 3), min(page.width, x1 + 2), min(page.height, y1 + 3)))
    actual = cropped.extract_text(layout=False) or ""
    text = normalized(actual)
    unit = item["reported_unit"]
    kind = item["declaration_kind"]
    if kind == "EXPLICIT_FINANCIAL_STATEMENT_AND_NOTE_PRESENTATION":
        semantic = re.fullmatch(r"财务附注中报表的单位为[：:]?人民币(百万元|千元|万元|亿元|元)", text)
    elif kind == "LOCAL_STATEMENT_UNIT":
        semantic = re.search(r"(?:货币|金额)?单位(?:[：:]|(?:均)?为)人民币(百万元|千元|万元|亿元|元)", text)
    elif kind == "LOCAL_STATEMENT_CURRENCY":
        semantic = re.search(r"(?:货币|金额)?单位(?:[：:]|为)(百万元|千元|万元|亿元|元)", text) if re.search(r"币种[：:]人民币", text) else None
    else:
        raise ValueError("unknown independent currency declaration kind")
    passed = text == normalized(item["text"]) and semantic is not None and semantic[1] == unit and item["currency"] == "CNY_EXPLICIT"
    return {"pdf_page": item["pdf_page"], "source_rectangle": item["bbox"], "source_text": item["text"],
            "independent_text": actual, "reported_unit": unit, "declaration_kind": kind,
            "status": "PASS_EXPLICIT_CNY_DECLARATION" if passed else "FAIL_DECLARATION_RECTANGLE_OR_UNIT"}


def audit_company(company: dict, cfg: dict) -> dict:
    common = {"stock_code": company["stock_code"], "source_pdf_sha256": company["source_pdf_sha256"],
              "selected_candidate_index": company["selected_candidate_index"],
              "literal_reader_status": company["literal_reader_status"], "agent_signal_enabled": False}
    if company["source_pdf_path"] is None:
        return {**common, "status": "SOURCE_MISSING_NOT_ZERO", "issues": [company["source_report_status"]]}
    if company["status"] != "UNIQUE_RECONCILED_CANDIDATE":
        return {**common, "status": "NO_UNIQUE_RECONCILED_BALANCE_NOT_ADOPTED", "issues": [company["status"]]}
    candidate = company["candidates"][company["selected_candidate_index"]]
    with pdfplumber.open(ROOT / company["source_pdf_path"]) as document:
        read = half_table_read(document, candidate, cfg["field_labels"])
        checked = compare_reading(company, candidate, read, cfg["field_labels"])
        local = [check_declaration(document, row) for row in candidate["currency_declaration"]["evidence"]]
        presentation = [check_declaration(document, row) for row in company["explicit_note_presentation_declarations"]]
    checked.update(common, currency_declaration_status=candidate["currency_declaration"]["status"],
                   independent_currency_declarations=local, independent_note_presentation_declarations=presentation)
    if candidate["currency_declaration"]["status"] == "CNY_EXPLICIT" and not local:
        checked["issues"].append("EXPLICIT_CURRENCY_HAS_NO_SOURCE_DECLARATION")
    if any(row["status"] != "PASS_EXPLICIT_CNY_DECLARATION" for row in local + presentation):
        checked["issues"].append("INDEPENDENT_CURRENCY_OR_NOTE_PRESENTATION_FAILED")
    if candidate["currency_declaration"]["status"] == "CNY_EXPLICIT" and any(row["reported_unit"] != candidate["reported_unit"] for row in local):
        checked["issues"].append("INDEPENDENT_CURRENCY_UNIT_DISAGREES")
    if checked["issues"]:
        checked["status"] = "FAIL_INDEPENDENT_SOURCE_CHECKS_NOT_ADOPTED"
    return checked


def run_companies(companies: list[dict], cfg: dict, auditor=audit_company) -> list[dict]:
    reports = []
    for company in companies:
        try:
            result = auditor(company, cfg)
        except Exception as error:
            result = {"stock_code": company["stock_code"], "source_pdf_sha256": company["source_pdf_sha256"],
                      "selected_candidate_index": company["selected_candidate_index"],
                      "literal_reader_status": company["literal_reader_status"],
                      "status": "INDEPENDENT_READ_FAILED_NOT_ADOPTED", "issues": [f"{type(error).__name__}: {error}"],
                      "agent_signal_enabled": False}
        reports.append(result)
        print(json.dumps({"stock_code": company["stock_code"], "status": result["status"], "completed": len(reports)},
                         ensure_ascii=False), flush=True)
    return reports


def validate_associations(companies: list[dict], manifest: dict, literals: dict, cfg: dict) -> None:
    codes = [c["stock_code"] for c in companies]
    if len(codes) != len(set(codes)) or len(codes) != cfg["expected_companies"] or set(codes) != set(manifest["reports"]) or set(codes) != set(literals):
        raise ValueError("half independent audit cohort differs")
    for company in companies:
        original = manifest["reports"][company["stock_code"]]
        pdf = original["original_pdf"]
        if (company["source_pdf_path"] != (pdf["archive_path"] if pdf else None)
                or company["source_pdf_sha256"] != (pdf["sha256"] if pdf else None)
                or company["report_metadata"] != original["report_metadata"]
                or company["industry_code"] != original["industry_code"]
                or company["literal_reader_status"] != literals[company["stock_code"]]["status"]
                or company["source_report_status"] != original["status"]):
            raise ValueError("half independent source/issuer/literal association differs")
        if pdf:
            metadata = company["report_metadata"]
            date, snapshot = map(datetime.fromisoformat, (metadata["available_at_proxy"], cfg["snapshot_at"]))
            if (metadata["stock_code"] != company["stock_code"] or metadata["report_end_date"] != cfg["report_end_date"]
                    or date.utcoffset() is None or snapshot.utcoffset() is None or date > snapshot):
                raise ValueError("half independent source period or availability differs")


def compute() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    validate_bindings(cfg["inputs"])
    if cfg["agent_signal_enabled"] is not False or pdfplumber.__version__ != cfg["pdfplumber_version"]:
        raise ValueError("half independent audit gate or reader version differs")
    candidates = json.loads((ROOT / cfg["candidates_path"]).read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / cfg["report_manifest"]).read_text(encoding="utf-8"))
    literal = json.loads((ROOT / cfg["literal_audit_path"]).read_text(encoding="utf-8"))
    literals = {c["stock_code"]: c for c in literal["companies"]}
    if len(literals) != len(literal["companies"]):
        raise ValueError("half independent duplicate literal issuer")
    validate_associations(candidates["companies"], manifest, literals, cfg)
    bindings = {**candidates["inputs"], **candidates["code_sha256"], **cfg["inputs"],
                CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG)}
    code = {f"research/data_pipeline/{n}.py": digest(ROOT / f"research/data_pipeline/{n}.py")
            for n in ("audit_issuer_half_balance_sheets", "audit_issuer_balance_sheets", "fetch_issuer_disclosures")}
    validate_bindings({**bindings, **code})
    reports = run_companies(candidates["companies"], cfg)
    validate_bindings({**bindings, **code})
    summary = {"companies": len(reports), "status_counts": dict(Counter(r["status"] for r in reports)),
               "financial_cells_checked": sum(r.get("cells_checked", 0) for r in reports),
               "scope_period_identities_checked": sum(len(r.get("independent_balance_checks", [])) for r in reports),
               "currency_declaration_checks": sum(len(r.get("independent_currency_declarations", [])) for r in reports),
               "universal_note_presentation_checks": sum(len(r.get("independent_note_presentation_declarations", [])) for r in reports)}
    return {"pipeline_version": cfg["version"], "companies": reports, "summary": summary,
            "status": "COMPLETED_WITH_SOURCE_QC" if any(r["status"] != "PASS" for r in reports) else "PASS_INDEPENDENT_SOURCE_CHECKS",
            "inputs": dict(sorted(bindings.items())), "code_sha256": code,
            "pdfplumber_version": pdfplumber.__version__, "agent_signal_enabled": False,
            "interpretation": cfg["interpretation"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("half independent audit output must be directly under research_outputs")
    result = compute()
    raw = serialize(result)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("half independent audit reconstruction differs")
        print("Half-year independent balance audit rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
