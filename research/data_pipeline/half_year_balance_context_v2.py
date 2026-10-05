"""Read explicitly labelled note references without promoting them to amounts."""

from __future__ import annotations

import re

from . import financial_balance_sheet as balance
from .half_year_balance_context import (candidate_currency, evidence, explicit_presentation,
                                        running_title)

NOTE_REFERENCE = re.compile(r"[（(]?[一二三四五六七八九十\d]+[）)]?(?:[、.．-]\d+)*")


def separate_note_references(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """A note column needs its own printed header and horizontal ownership."""
    result, removed = [], []
    scope, bounds, header_evidence = None, None, None
    for row in rows:
        heading = balance.statement_heading(row["text"])
        if heading is not None:
            if heading["kind"] != "资产负债表" or heading["scope"] == "parent":
                scope, bounds, header_evidence = None, None, None
            elif not heading["continued"]:
                scope, bounds, header_evidence = heading["scope"], None, None
        words = row["words"]
        notes = [w for w in words if balance.compact(w["text"]) == "附注"]
        dates = balance._dates_in_row(row)
        projects = [w for w in words if balance.compact(w["text"]) in {"项目", "项目名称"}]
        if scope is not None and len(notes) == 1 and len(projects) == 1 and len(dates) in {2, 4}:
            centre = (notes[0]["bbox"][0] + notes[0]["bbox"][2]) / 2
            left = (projects[0]["bbox"][0] + projects[0]["bbox"][2]) / 2
            date_centres = balance._date_centres(row)
            right = min(date_centres)
            if left < centre < right:
                bounds = ((left + centre) / 2, (centre + right) / 2)
                header_evidence = {**evidence(row), "note_column_interval": list(bounds),
                                   "note_header_word": notes[0], "geometry_rule": "midpoints_between_explicit_project_note_and_first_date_headers"}
            else:
                bounds, header_evidence = None, None
            result.append(row)
            continue
        if bounds is None or scope is None or running_title(row):
            result.append(row)
            continue
        found = [w for w in words if bounds[0] < (w["bbox"][0] + w["bbox"][2]) / 2 < bounds[1]
                 and NOTE_REFERENCE.fullmatch(balance.compact(w["text"]))]
        if not found:
            result.append(row)
            continue
        keep = [w for w in words if w not in found]
        # An explicitly known financial label must remain after note separation.
        trial = {**row, "words": keep}
        if balance._field(balance._label(trial)) is None:
            result.append(row)
            continue
        removed.extend({"pdf_page": row["pdf_page"], "source_row": evidence(row),
                        "note_word": w, "explicit_header_evidence": header_evidence} for w in found)
        result.append(trial)
    return result, removed


def extract_company(rows: list[dict], target_period: str) -> dict:
    ignored = [evidence(row) for row in rows if running_title(row)]
    kept = [row for row in rows if not running_title(row)]
    separated, removed = separate_note_references(kept)
    result = balance.extract_company(separated, target_period)
    for candidate in result["candidates"]:
        candidate["currency_declaration"] = candidate_currency(kept, candidate)
    result.update(ignored_running_report_titles=ignored, financial_note_references_retained_separately=removed,
                  explicit_note_presentation_declarations=[item for row in kept
                                                         if (item := explicit_presentation(row)) is not None])
    return result
