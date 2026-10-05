"""Locate literal balance-sheet and unit evidence for subsequent numeric review."""

from __future__ import annotations

import re

SCALES = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}


def statement_heading(text: str) -> tuple[str, str] | None:
    compact = re.sub(r"\s+", "", text)
    compact = re.sub(r"^(?:[（(]?[一二三四五六七八九十\d]+[）)]?[、.．]?)", "", compact)
    compact = re.sub(r"[（(](?:续|未经审计)[）)]$", "", compact)
    scopes = {"合并及母公司": "combined_consolidated_and_parent", "合并资产负债表和": "combined_consolidated_and_parent",
              "合并": "consolidated", "母公司": "parent", "": "unqualified_issuer"}
    for prefix, scope in scopes.items():
        for kind in ("资产负债表", "利润表", "现金流量表", "所有者权益变动表"):
            if compact == prefix + kind:
                return kind, scope
    return None


def locate_balance_sheets(page_texts: list[str]) -> dict:
    if not page_texts or any(not isinstance(text, str) for text in page_texts):
        raise ValueError("financial page index requires page text strings")
    lines = [{"pdf_page": page + 1, "line_number": index + 1, "text": text}
             for page, content in enumerate(page_texts) for index, text in enumerate(content.splitlines())]
    headings = [(index, statement_heading(line["text"])) for index, line in enumerate(lines)]
    headings = [(index, value) for index, value in headings if value is not None]
    sections = []
    for i, (start, (kind, scope)) in enumerate(headings):
        if kind != "资产负债表":
            continue
        end = headings[i + 1][0] if i + 1 < len(headings) else len(lines)
        section = lines[start:end]
        # Unit evidence belongs to this heading's opening context, never a later statement.
        context = section[:50]
        unit_evidence = []
        for line in context:
            compact = re.sub(r"\s+", "", line["text"])
            found = re.search(r"(?:货币|金额)?单位(?:[:：]|为)(?:人民币)?(百万元|千元|万元|亿元|元)", compact)
            if found:
                unit_evidence.append({**line, "reported_unit": found[1], "reported_multiplier_to_yuan": SCALES[found[1]]})
        units = sorted({item["reported_unit"] for item in unit_evidence})
        header = re.sub(r"\s+", "", "".join(line["text"] for line in context))
        sections.append({"scope_heading": scope, "heading": lines[start], "start_pdf_page": section[0]["pdf_page"],
                         "end_pdf_page": section[-1]["pdf_page"], "opening_context": context, "statement_lines": section,
                         "unit_evidence": unit_evidence, "distinct_reported_units": units,
                         "unit_status": "UNIQUE_LITERAL_UNIT" if len(units) == 1 else "MISSING_UNIT" if not units else "CONFLICTING_UNITS",
                         "current_period_header_found": bool(re.search(r"2019年0?9月30日", header)),
                         "prior_year_header_found": "2018年12月31日" in header,
                         "column_order_verified": False, "financial_values_verified": False})
    counts = {scope: sum(row["scope_heading"] == scope for row in sections)
              for scope in ("consolidated", "parent", "combined_consolidated_and_parent", "unqualified_issuer")}
    possible = [row for row in sections if row["scope_heading"] != "parent"]
    status = ("NO_LITERAL_BALANCE_SHEET_HEADING" if not possible else "MULTIPLE_NON_PARENT_SECTIONS_REVIEW_REQUIRED" if len(possible) > 1
              else "SINGLE_NON_PARENT_SECTION_REVIEW_REQUIRED")
    return {"balance_sheet_sections": sections, "section_scope_counts": counts, "review_queue_status": status,
            "financial_values_verified": False, "financial_values": None, "agent_signal_enabled": False}
