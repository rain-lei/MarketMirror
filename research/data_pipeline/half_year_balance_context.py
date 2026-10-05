"""Bound half-year statement context without inventing dates or currencies."""

from __future__ import annotations

import re

from . import financial_balance_sheet as balance


RUNNING_TITLE = re.compile(
    r".*20\d{2}年半年度(?:财务)?报告(?:全文|摘要|[（(].*[）)])?"
)
PRESENTATION = re.compile(
    r"财务附注中报表的单位为[：:]?人民币(百万元|千元|万元|亿元|元)"
)
CNY_UNIT = re.compile(
    r"(?:货币|金额)?单位(?:[：:]|(?:均)?为)人民币(百万元|千元|万元|亿元|元)"
)


def evidence(row: dict) -> dict:
    return {key: row[key] for key in ("pdf_page", "bbox", "text")}


def running_title(row: dict) -> bool:
    return (row["bbox"][1] < row["page_height"] * .12
            and RUNNING_TITLE.fullmatch(balance.compact(row["text"])) is not None)


def explicit_presentation(row: dict) -> dict | None:
    match = PRESENTATION.fullmatch(balance.compact(row["text"]))
    if not match:
        return None
    return {**evidence(row), "reported_unit": match[1], "currency": "CNY_EXPLICIT",
            "declaration_kind": "EXPLICIT_FINANCIAL_STATEMENT_AND_NOTE_PRESENTATION"}


def candidate_currency(rows: list[dict], candidate: dict) -> dict:
    """CNY requires a printed local declaration or a matching universal unit."""
    if candidate["status"] != "RECONCILED_CURRENT_BALANCE_SHEET":
        return {"status": "CURRENCY_NOT_ESTABLISHED", "evidence": []}
    start = candidate["heading_evidence"][0]
    first = min((row for field in candidate["fields"].values() for item in field["rows"]
                 for row in item["row_evidence"]), key=lambda row: (row["pdf_page"], row["bbox"][1]))
    local = [r for r in rows if r["pdf_page"] == start["pdf_page"]
             and start["bbox"][1] <= r["bbox"][1] < first["bbox"][1]]
    declarations = []
    for row in local:
        text = balance.compact(row["text"])
        match = CNY_UNIT.search(text)
        if match:
            declarations.append({**evidence(row), "reported_unit": match[1],
                                 "currency": "CNY_EXPLICIT", "declaration_kind": "LOCAL_STATEMENT_UNIT"})
        elif re.search(r"币种[：:]人民币", text) and balance.UNIT.search(text):
            unit = balance.UNIT.search(text)[1]
            declarations.append({**evidence(row), "reported_unit": unit,
                                 "currency": "CNY_EXPLICIT", "declaration_kind": "LOCAL_STATEMENT_CURRENCY"})
    presentation = [item for row in rows if (item := explicit_presentation(row)) is not None
                    and (row["pdf_page"], row["bbox"][1]) < (start["pdf_page"], start["bbox"][1])]
    if not declarations and presentation:
        declarations = [presentation[-1]]
    if not declarations:
        return {"status": "CURRENCY_UNSPECIFIED", "evidence": []}
    if {item["reported_unit"] for item in declarations} != {candidate["reported_unit"]}:
        return {"status": "CURRENCY_UNIT_CONFLICT_REVIEW_REQUIRED", "evidence": declarations}
    return {"status": "CNY_EXPLICIT", "evidence": declarations}


def extract_company(rows: list[dict], target_period: str) -> dict:
    """Exclude only observed running titles; preserve every statement candidate."""
    ignored = [evidence(row) for row in rows if running_title(row)]
    kept = [row for row in rows if not running_title(row)]
    result = balance.extract_company(kept, target_period)
    for candidate in result["candidates"]:
        candidate["currency_declaration"] = candidate_currency(kept, candidate)
    result.update(ignored_running_report_titles=ignored,
                  explicit_note_presentation_declarations=[item for row in kept
                                                         if (item := explicit_presentation(row)) is not None])
    return result
