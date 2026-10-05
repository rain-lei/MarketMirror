"""Independently re-read all candidate amounts using PDF table cell borders.

The producer reads positioned PyMuPDF words. This auditor uses pdfplumber's
vector-border table finder, including merged header-cell rectangles. The two
borderless bank statements use complete-page reviewed column anchors and a
fresh pdfplumber line read. No production parsing/selection function is called.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from types import SimpleNamespace

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_q3_balance_sheets_2019.json"
CANDIDATES = ROOT / "research_outputs/issuer_q3_balance_sheet_candidates_2019_v1.json"
OUTPUT = ROOT / "research_outputs/issuer_q3_balance_sheet_audit_2019_v1.json"
UNIT_FACTORS = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").replace("(", "（").replace(")", "）")


def independent_cell(text: str | None) -> dict:
    value = normalized(text).replace("−", "-").replace("－", "-")
    if not value:
        return {"status": "REPORTED_BLANK", "reported_value": None, "source_cell_text": text}
    if value in ("-", "--", "—", "——", "–"):
        return {"status": "REPORTED_DASH", "reported_value": None, "source_cell_text": text}
    if value.startswith("（") and value.endswith("）"):
        value = "-" + value[1:-1]
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", value):
        return {"status": "UNPARSABLE_INDEPENDENT_CELL", "reported_value": None, "source_cell_text": text}
    return {"status": "NUMERIC", "reported_value": str(Decimal(value.replace(",", ""))), "source_cell_text": text}


def dates(text: str | None) -> list[str]:
    return [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in
            re.findall(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", normalized(text))]


class ContentTable:
    """Ignore globally empty padding columns, preserving real cell rectangles."""

    def __init__(self, table):
        values = table.extract()
        keep = [i for i in range(len(values[0])) if any(normalized(row[i]) for row in values)]
        self.values = [[row[i] for i in keep] for row in values]
        self.rows = [SimpleNamespace(cells=[row.cells[i] for i in keep]) for row in table.rows]
        self.bbox = table.bbox

    def extract(self):
        return self.values


def bordered_field_rows(table, reverse: dict, count: int) -> list[tuple]:
    """Map actual rectangles to money columns, including fragmented borders.

    Global PDF table column indexes can include empty padding or spurious cuts
    within one real currency column. Use complete amount rows to establish the
    column boundaries for this page, then keep solitary previous values in place.
    """
    found, full = [], []
    raw_rows = list(zip(table.extract(), table.rows))
    index = 0
    while index < len(raw_rows):
        values, geometry = raw_rows[index]
        labels = [(i, reverse[normalized(value)]) for i, value in enumerate(values) if normalized(value) in reverse]
        if not labels:
            prefixes = [(i, normalized(value)) for i, value in enumerate(values)
                        if len(normalized(value)) >= 2 and any(alias.startswith(normalized(value)) for alias in reverse)]
            merged = None
            for label_index, prefix in prefixes:
                rect = geometry.cells[label_index]
                text = prefix
                parts = [(value, box) for i, (value, box) in enumerate(zip(values, geometry.cells))
                         if i != label_index and normalized(value) and box is not None]
                boxes = list(geometry.cells)
                for end in range(index + 1, min(index + 5, len(raw_rows))):
                    following, following_geometry = raw_rows[end]
                    fragments = []
                    for value, box in zip(following, following_geometry.cells):
                        if not normalized(value) or box is None:
                            continue
                        if rect[0] <= (box[0] + box[2]) / 2 <= rect[2]:
                            fragments.append(value)
                        else:
                            parts.append((value, box))
                    text += normalized("".join(fragments))
                    boxes.extend(following_geometry.cells)
                    if not any(alias.startswith(text) for alias in reverse):
                        break
                    if text in reverse:
                        merged = (reverse[text], text, boxes, [(independent_cell(value), box) for value, box in parts], rect, end)
                        break
                if merged:
                    break
            if merged is None:
                index += 1
                continue
            field, label, rectangles, amounts, label_rectangle, end = merged
            index = end + 1
        else:
            if len(labels) != 1:
                raise ValueError("multiple independent field labels in one border row")
            label_index, field = labels[0]
            label = values[label_index]
            rectangles = geometry.cells
            label_rectangle = geometry.cells[label_index]
            amounts = [(independent_cell(value), rectangle) for i, (value, rectangle) in enumerate(zip(values, geometry.cells))
                       if i != label_index and normalized(value) and rectangle is not None]
            index += 1
        found.append((field, label, rectangles, amounts))
        numeric = [(cell, rect) for cell, rect in amounts if cell["status"] == "NUMERIC"]
        if len(numeric) == count:
            full.append((label_rectangle, numeric))
    if not found:
        return []
    if not full:
        raise ValueError("independent border page has no complete numeric row for column boundaries")
    boundaries = [median(label[2] for label, _ in full)]
    boundaries += [median(cells[i][1][2] for _, cells in full) for i in range(count - 1)]
    boundaries.append(table.bbox[2])
    if any(boundaries[i] >= boundaries[i + 1] for i in range(count)):
        raise ValueError("independent currency rectangle boundaries are not separable")
    result = []
    for field, label, rectangles, amounts in found:
        cells = []
        for i in range(count):
            matching = [(cell, rect) for cell, rect in amounts
                        if boundaries[i] - .1 < (rect[0] + rect[2]) / 2 <= boundaries[i + 1] + .1]
            if len(matching) > 1:
                raise ValueError("more than one nonempty rectangle in one financial column")
            cells.append({**matching[0][0], "source_cell_rectangle": matching[0][1]} if matching else
                         {"status": "REPORTED_BLANK", "reported_value": None, "source_cell_text": ""})
        if sum(bool(normalized(cell["source_cell_text"])) for cell in cells) != len(amounts):
            raise ValueError("nonempty financial rectangle could not be assigned exactly once")
        result.append((field, label, rectangles, cells, boundaries))
    return result


def table_columns(table, scope: str, count: int, field_labels: dict) -> list[dict] | None:
    extracted = table.extract()
    label_set = {normalized(alias) for group in field_labels.values() for alias in group}
    first_field = next((i for i, row in enumerate(extracted) if normalized(row[0]) in label_set), len(extracted))
    headers = extracted[:first_field]
    if not headers:
        return None
    date_rows = [(i, row) for i, row in enumerate(headers)
                 if len(row) == count + 1 and any(dates(cell) for cell in row[1:])]
    if not date_rows:
        return None  # The previous page's declared columns carry to this continuation.
    i, date_row = date_rows[-1]
    rectangles = table.rows[i].cells
    if scope == "combined":
        scope_rows = [row for row in headers if len(row) == count + 1 and
                      [normalized(v) for v in row[1:]] == ["合并数", "公司数", "合并数", "公司数"]]
        if len(scope_rows) != 1 or count != 4:
            raise ValueError("independent combined table scope header is not explicit")
        # A None header cell may inherit only from a demonstrably merged rectangle.
        base = next(row for row in table.rows[i + 1:] if all(cell is not None for cell in row.cells[1:]))
        periods = []
        for col in range(1, count + 1):
            x = (base.cells[col][0] + base.cells[col][2]) / 2
            containers = [j for j, rect in enumerate(rectangles[1:], 1)
                          if rect is not None and rect[0] < x < rect[2] and dates(date_row[j])]
            if len(containers) != 1 or len(dates(date_row[containers[0]])) != 1:
                raise ValueError("independent merged date rectangle does not cover one column")
            periods.append(dates(date_row[containers[0]])[0])
        scopes = ["consolidated", "parent", "consolidated", "parent"]
    else:
        if any(len(dates(cell)) != 1 for cell in date_row[1:]):
            raise ValueError("independent ordinary date cells are missing or ambiguous")
        periods = [dates(cell)[0] for cell in date_row[1:]]
        scopes = [scope] * count
    return [{"scope": scopes[i], "period_end": periods[i]} for i in range(count)]


def _limits(candidate: dict, page: int, height: float) -> tuple[float, float]:
    start = candidate["heading_evidence"][0]
    lo = max(0, start["bbox"][1] - 4) if page == start["pdf_page"] else 0
    evidence = candidate["fields"]["liabilities_and_equity"]["rows"][0]["row_evidence"]
    final_page = max(row["pdf_page"] for row in evidence)
    hi = min(height, max(row["bbox"][3] for row in evidence if row["pdf_page"] == page) + 4) if page == final_page else height
    return lo, hi


def table_read(document, candidate: dict, labels: dict) -> dict:
    reverse = {normalized(alias): field for field, aliases in labels.items() for alias in aliases}
    count = len(candidate["column_mapping"]["columns"])
    readings = {field: [] for field in labels}
    mappings, unit_evidence, heading_text = [], [], []
    final_page = max(row["pdf_page"] for row in candidate["fields"]["liabilities_and_equity"]["rows"][0]["row_evidence"])
    for page_number in range(candidate["start_pdf_page"], final_page + 1):
        page = document.pages[page_number - 1]
        lo, hi = _limits(candidate, page_number, page.height)
        cropped = page.crop((0, lo, page.width, hi))
        text = cropped.extract_text(layout=False) or ""
        heading_text.extend(text.splitlines()[:18])
        units = re.findall(r"(?:货币|金额)?单位(?:[:：]|为)(?:人民币)?(百万元|千元|万元|亿元|元)", normalized(text))
        unit_evidence.extend({"pdf_page": page_number, "reported_unit": unit} for unit in units)
        for original_table in cropped.find_tables():
            table = ContentTable(original_table)
            rows = table.extract()
            present = [value for row in rows for value in row if normalized(value) in reverse]
            if not present:
                continue
            mapping = table_columns(table, candidate["scope_heading"], count, labels)
            if mapping:
                mappings.append({"pdf_page": page_number, "columns": mapping, "table_bbox": list(table.bbox)})
            for field, label, rectangles, cells, boundaries in bordered_field_rows(original_table, reverse, count):
                readings[field].append({"pdf_page": page_number, "source_label": label,
                                        "table_cell_rectangles": rectangles,
                                        "independent_currency_boundaries": boundaries, "cells": cells})
        page.close()
    return {"fields": readings, "mappings": mappings, "unit_evidence": unit_evidence,
            "heading_evidence_text": heading_text, "method": "independent_pdfplumber_vector_border_tables"}


def reviewed_bank_read(document, candidate: dict, labels: dict, anchor: dict) -> dict:
    reverse = {normalized(alias): field for field, aliases in labels.items() for alias in aliases}
    readings = {field: [] for field in labels}
    count = len(anchor["columns_reviewed_left_to_right"])
    mappings = [{"pdf_page": candidate["start_pdf_page"], "columns": [
        {"scope": "consolidated" if c["scope"] == "consolidated_group" else
         "parent" if c["scope"] == "parent_bank" else "issuer_as_stated", "period_end": c["period_end"]}
        for c in anchor["columns_reviewed_left_to_right"]], "basis": anchor["review_basis"]}]
    units, headings = [], []
    final_page = max(row["pdf_page"] for row in candidate["fields"]["liabilities_and_equity"]["rows"][0]["row_evidence"])
    for number in range(candidate["start_pdf_page"], final_page + 1):
        page = document.pages[number - 1]
        lo, hi = _limits(candidate, number, page.height)
        text = page.crop((0, lo, page.width, hi)).extract_text(layout=False) or ""
        headings.extend(text.splitlines()[:18])
        units.extend({"pdf_page": number, "reported_unit": unit} for unit in re.findall(
            r"(?:货币|金额)?单位(?:[:：]|为)(?:人民币)?(百万元|千元|万元|亿元|元)", normalized(text)))
        for line in text.splitlines():
            for label, field in reverse.items():
                # Bank rows are not wrapped for these exact field labels. Scope order
                # is reviewed independently; no missing text token is shifted left.
                tokens = line.split()
                if tokens and normalized(tokens[0]) == label:
                    cells = tokens[1:]
                    if len(cells) != count:
                        raise ValueError("borderless reviewed bank row requires complete explicit value columns")
                    readings[field].append({"pdf_page": number, "source_label": tokens[0],
                                            "source_line": line, "cells": [independent_cell(cell) for cell in cells]})
        page.close()
    return {"fields": readings, "mappings": mappings, "unit_evidence": units,
            "heading_evidence_text": headings, "method": "independent_pdfplumber_lines_with_complete_page_reviewed_bank_columns"}


def compare_reading(company: dict, candidate: dict, read: dict, labels: dict) -> dict:
    issues, values, checked = [], {}, 0
    expected_columns = [{"scope": c["scope"], "period_end": c["period_end"]} for c in candidate["column_mapping"]["columns"]]
    if not read["mappings"] or any(m["columns"] != expected_columns for m in read["mappings"]):
        issues.append("INDEPENDENT_COLUMN_MAPPING_DISAGREES")
    if {row["reported_unit"] for row in read["unit_evidence"]} != {candidate["reported_unit"]}:
        issues.append("INDEPENDENT_UNIT_DISAGREES")
    heading = normalized("".join(read["heading_evidence_text"]))
    required_heading = "合并及母公司资产负债表" if candidate["scope_heading"] == "combined" and read["method"].endswith("tables") else \
        "合并资产负债表和资产负债表" if candidate["scope_heading"] == "combined" else \
        "合并资产负债表" if candidate["scope_heading"] == "consolidated" else "资产负债表"
    if required_heading not in heading:
        issues.append("INDEPENDENT_SCOPE_HEADING_MISSING")
    for field in labels:
        found = read["fields"][field]
        expected = candidate["fields"][field]
        if len(found) > 1:
            issues.append(field + ":MULTIPLE_INDEPENDENT_ROWS")
            continue
        if not found:
            if expected["status"] != "ROW_ABSENT":
                issues.append(field + ":INDEPENDENT_ROW_MISSING")
            else:
                values[field] = [{"status": "ROW_ABSENT", "reported_value": None} for _ in expected_columns]
            continue
        cells = found[0]["cells"]
        values[field] = cells
        for i, (actual, wanted) in enumerate(zip(cells, expected["cells"])):
            checked += 1
            if actual["status"] != wanted["status"]:
                issues.append(f"{field}:{i}:CELL_STATUS_DISAGREES")
            elif actual["status"] == "NUMERIC":
                if Decimal(actual["reported_value"]) != Decimal(wanted["reported_value"]):
                    issues.append(f"{field}:{i}:NUMBER_DISAGREES")
                if Decimal(actual["reported_value"]) * UNIT_FACTORS[candidate["reported_unit"]] != Decimal(wanted["value_yuan"]):
                    issues.append(f"{field}:{i}:UNIT_NORMALIZATION_DISAGREES")
    checks = []
    for i, column in enumerate(expected_columns):
        totals = [values.get(field, [{}] * len(expected_columns))[i] for field in ("assets", "liabilities", "equity", "liabilities_and_equity")]
        if any(cell.get("status") != "NUMERIC" for cell in totals):
            issues.append(f"{i}:INDEPENDENT_TOTAL_MISSING")
            continue
        a, l, e, total = [Decimal(cell["reported_value"]) for cell in totals]
        precision = max(Decimal(1).scaleb(v.as_tuple().exponent) for v in (a, l, e, total))
        residual = a - l - e
        if abs(residual) > precision * Decimal("1.5") or abs(a - total) > precision:
            issues.append(f"{i}:INDEPENDENT_BALANCE_IDENTITY_FAILED")
        checks.append({"column_index": i, **column, "assets_minus_liabilities_minus_equity_yuan": str(residual * UNIT_FACTORS[candidate["reported_unit"]]),
                       "assets_minus_reported_total_yuan": str((a - total) * UNIT_FACTORS[candidate["reported_unit"]]),
                       "identity_exact": residual == 0 and a == total})
    return {"stock_code": company["stock_code"], "status": "PASS" if not issues else "FAIL",
            "issues": issues, "selected_candidate_index": company["selected_candidate_index"],
            "source_pdf_sha256": company["source_pdf_sha256"], "cells_checked": checked,
            "independent_balance_checks": checks, "independent_reading": read, "agent_signal_enabled": False}


def compute(source: Path) -> dict:
    protocol = json.loads(CONFIG.read_text(encoding="utf-8"))
    candidates = json.loads(source.read_text(encoding="utf-8"))
    anchors_path = ROOT / protocol["reviewed_bank_anchors"]
    anchors = json.loads(anchors_path.read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / protocol["report_manifest"]).read_text(encoding="utf-8"))
    by_pdf = {row["source_pdf_sha256"]: row for row in anchors["sources"]}
    bindings = {str(source): digest(source), str(CONFIG): digest(CONFIG),
                **candidates["inputs"], **candidates["code_sha256"]}

    def verify():
        for path, expected in bindings.items():
            if digest(Path(path)) != expected:
                raise ValueError("independent financial audit source or producer changed: " + path)

    verify()
    codes = [company["stock_code"] for company in candidates["companies"]]
    if len(codes) != len(set(codes)) or set(codes) != set(manifest["reports"]) or len(codes) != protocol["expected_companies"]:
        raise ValueError("independent financial audit cohort membership changed")
    reports = []
    for n, company in enumerate(candidates["companies"], 1):
        original = manifest["reports"][company["stock_code"]]
        if (company["source_pdf_path"] != original["original_pdf"]["archive_path"] or
                company["source_pdf_sha256"] != original["original_pdf"]["sha256"] or
                company["report_metadata"] != original["report_metadata"] or
                company["industry_code"] != original["industry_code"] or
                company["report_metadata"]["stock_code"] != company["stock_code"] or
                company["report_metadata"]["report_end_date"] != protocol["report_end_date"] or
                datetime.fromisoformat(company["report_metadata"]["available_at_proxy"]) > datetime.fromisoformat(protocol["snapshot_at"])):
            raise ValueError("independent financial audit issuer/source/period/availability association changed")
        if company["status"] != "UNIQUE_RECONCILED_CANDIDATE":
            reports.append({"stock_code": company["stock_code"], "status": "FAIL", "issues": [company["status"]]})
            continue
        candidate = company["candidates"][company["selected_candidate_index"]]
        try:
            with pdfplumber.open(ROOT / company["source_pdf_path"]) as document:
                anchor = by_pdf.get(company["source_pdf_sha256"])
                read = reviewed_bank_read(document, candidate, protocol["field_labels"], anchor) if anchor else \
                    table_read(document, candidate, protocol["field_labels"])
            reports.append(compare_reading(company, candidate, read, protocol["field_labels"]))
        except (ValueError, IndexError, StopIteration) as error:
            reports.append({"stock_code": company["stock_code"], "status": "FAIL", "issues": [str(error)]})
        if n % 30 == 0:
            print(f"Independently read {n}/{len(candidates['companies'])} reports.", flush=True)
    verify()
    summary = {"companies": len(reports), "status_counts": dict(Counter(row["status"] for row in reports)),
               "methods": dict(Counter(row["independent_reading"]["method"] for row in reports if "independent_reading" in row)),
               "financial_cells_checked": sum(row.get("cells_checked", 0) for row in reports),
               "scope_period_identities_checked": sum(len(row.get("independent_balance_checks", [])) for row in reports)}
    return {"pipeline_version": "independent-full-cohort-border-table-and-reviewed-bank-balance-audit-v1",
            "reports": reports, "summary": summary, "status": "PASS" if all(row["status"] == "PASS" for row in reports) and len(reports) == protocol["expected_companies"] else "FAIL",
            "inputs": dict(sorted(bindings.items())), "code_sha256": digest(Path(__file__)), "pdfplumber_version": pdfplumber.__version__,
            "agent_signal_enabled": False,
            "interpretation": "Independent border-cell extraction for ordinary statements, with actual merged-date rectangles and scope headers; reviewed column anchors plus new line reads for the two borderless banks. All declared fields, missing statuses, Decimal units and balances are checked. Automated agreement is not an economic validation or full-cohort manual certification."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute(args.candidates)
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("independent financial audit differs from frozen re-read")
        print("Full-cohort independent financial audit rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))
    for row in result["reports"]:
        if row["status"] != "PASS":
            print("QC", row["stock_code"], row["issues"])
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
