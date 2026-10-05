"""Read dated balance-sheet columns, preserving blanks and source geometry.

This module does not assign a trading direction or a policy coefficient.  A
nearby preparation date is never a numeric-column date.  Only a complete table
with explicit columns and reconciled totals can become a candidate snapshot.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from statistics import median

FIELDS = {
    "assets": ("资产总计", "资产合计"),
    "liabilities": ("负债合计", "负债总计"),
    "equity": ("所有者权益合计", "所有者权益总计", "股东权益合计",
               "所有者权益（或股东权益）合计"),
    "liabilities_and_equity": ("负债和所有者权益总计", "负债及所有者权益总计",
                               "负债和股东权益总计", "负债及股东权益总计",
                               "负债和所有者权益（或股东权益）总计"),
    "money_funds": ("货币资金",),
    "current_assets": ("流动资产合计",),
    "current_liabilities": ("流动负债合计",),
    "short_term_borrowings": ("短期借款",),
    "long_term_borrowings": ("长期借款",),
    "bonds_payable": ("应付债券",),
    "noncurrent_liabilities_due_within_one_year": ("一年内到期的非流动负债",),
    "lease_liabilities": ("租赁负债",),
    "trade_receivables": ("应收账款",),
    "inventories": ("存货",),
    "customer_deposits_in_money_funds": ("其中：客户存款", "其中：客户资金存款"),
    "bank_cash_and_central_bank_balances": ("现金及存放中央银行款项",),
}
SCALES = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}
NUMBER = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
DATE = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
UNIT = re.compile(r"(?:货币|金额)?单位(?:[:：]|为)(?:人民币)?(百万元|千元|万元|亿元|元)")
DASHES = {"-", "--", "—", "——", "–", "－"}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("(", "（").replace(")", "）")


def statement_heading(text: str) -> dict | None:
    value = compact(text)
    value = re.sub(r"^(?:[（]?[一二三四五六七八九十\d]+[）]?[、.．]?)", "", value)
    continued = "（续）" in value
    value = re.sub(r"（(?:续|未经审计)）$", "", value)
    for kind in ("资产负债表", "利润表", "现金流量表", "所有者权益变动表", "损益表"):
        if value in ("合并及母公司" + kind, "合并" + kind + "和" + kind,
                     "合并" + kind + "及" + kind):
            return {"kind": kind, "scope": "combined", "continued": continued}
        for prefix, scope in (("合并", "consolidated"), ("母公司", "parent"), ("", "issuer_as_stated")):
            if value == prefix + kind:
                return {"kind": kind, "scope": scope, "continued": continued}
    return None


def geometry_rows(words: list[tuple], pdf_page: int, page_height: float) -> list[dict]:
    """Combine same-baseline words without merging adjacent printed rows."""
    groups: list[list] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        y = (word[1] + word[3]) / 2
        if groups and abs(y - median((w[1] + w[3]) / 2 for w in groups[-1])) <= 3:
            groups[-1].append(word)
        else:
            groups.append([word])
    rows = []
    for group in groups:
        ordered = sorted(group, key=lambda w: w[0])
        text = " ".join(w[4] for w in ordered)
        if (re.fullmatch(r"\d{1,3}[/／]\d{1,3}", compact(text)) and min(w[1] for w in group) > page_height * .8
                or compact(text) == str(pdf_page) and min(w[1] for w in group) > page_height * .9):
            continue
        rows.append({"pdf_page": pdf_page, "page_height": page_height,
                     "bbox": [min(w[0] for w in group), min(w[1] for w in group),
                              max(w[2] for w in group), max(w[3] for w in group)],
                     "text": text,
                     "words": [{"text": w[4], "bbox": list(w[:4])} for w in ordered]})
    return rows


def _cell_number(text: str) -> str | None:
    value = compact(text).replace("−", "-").replace("－", "-")
    if value.startswith("（") and value.endswith("）"):
        value = "-" + value[1:-1]
    if NUMBER.fullmatch(value):
        return value.replace(",", "")
    return None


def numeric_cells(row: dict) -> list[dict]:
    return [{**word, "reported_value": value} for word in row["words"]
            if (value := _cell_number(word["text"])) is not None]


def _label(row: dict) -> str:
    if "normalized_label" in row:
        return row["normalized_label"]
    # The left side of a numeric row is a label, never a positional list of values.
    parts = []
    for word in row["words"]:
        if _cell_number(word["text"]) is not None or compact(word["text"]) in DASHES:
            break
        parts.append(word["text"])
    return compact("".join(parts)).rstrip("：:")


def _field(label: str) -> str | None:
    for field, aliases in FIELDS.items():
        if label in {compact(alias) for alias in aliases}:
            return field
    return None


def _evidence(row: dict) -> dict:
    return {key: row[key] for key in ("pdf_page", "bbox", "text")}


def unfold_labels(rows: list[dict]) -> list[dict]:
    """Join a wrapped exact label and its cells, not arbitrary neighboring rows."""
    aliases = {compact(alias) for group in FIELDS.values() for alias in group}
    result = []
    i = 0
    while i < len(rows):
        row = rows[i]
        label = _label(row)
        end = i
        if label and any(alias.startswith(label) for alias in aliases):
            combined = label
            for j in range(i + 1, min(i + 4, len(rows))):
                following = rows[j]
                if following["pdf_page"] != row["pdf_page"] or following["bbox"][1] - rows[j - 1]["bbox"][3] >= 16:
                    break
                fragment = _label(following)
                if combined in aliases and (numeric_cells(row) or fragment):
                    break
                combined += fragment
                if not any(alias.startswith(combined) for alias in aliases):
                    break
                if combined in aliases and any(numeric_cells(r) for r in rows[i:j + 1]):
                    label, end = combined, j
                    break
        if end > i:
            merged = rows[i:end + 1]
            row = {**row, "normalized_label": label,
                   "text": " / ".join(r["text"] for r in merged),
                   "words": [w for r in merged for w in r["words"]],
                   "source_rows": [_evidence(r) for r in merged]}
        result.append(row)
        i = end + 1
    return result


def _dates_in_row(row: dict) -> list[str]:
    return [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in DATE.findall(compact(row["text"]))]


def _date_centres(row: dict) -> list[float]:
    text, owners = "", []
    for word in row["words"]:
        part = compact(word["text"])
        text += part
        owners.extend([word] * len(part))
    positions = []
    for match in DATE.finditer(text):
        matched = owners[match.start():match.end()]
        positions.append((min(w["bbox"][0] for w in matched) + max(w["bbox"][2] for w in matched)) / 2)
    return positions


def _centres_fit(centres: list[float], edges: list[float]) -> bool:
    return len(centres) == len(edges) and all((edges[i - 1] + 1.5 if i else -float("inf")) < x <= edges[i] + 1.5
                                            for i, x in enumerate(centres))


def _scope_tokens(row: dict) -> list[str]:
    found = []
    for word in row["words"]:
        text = compact(word["text"])
        if text in ("合并数", "本集团", "集团"):
            found.append("consolidated")
        elif text in ("公司数", "本行", "母公司"):
            found.append("parent")
    return found


def _header_columns(header: list[dict], column_count: int, scope: str, edges: list[float]) -> dict:
    date_rows = [(row, _dates_in_row(row)) for row in header]
    date_rows = [(row, dates) for row, dates in date_rows if len(dates) in (2, column_count)]
    date_evidence = []
    date_type = None
    if date_rows:
        row, dates = date_rows[-1]
        date_evidence = [_evidence(row)]
        if len(dates) == column_count:
            if not _centres_fit(_date_centres(row), edges):
                return {"status": "DATE_HEADERS_DO_NOT_ALIGN_WITH_NUMERIC_COLUMNS"}
            period_ends = dates
            date_type = "one_explicit_date_per_column"
        elif column_count == 4 and len(dates) == 2:
            if not _centres_fit(_date_centres(row), [edges[1], edges[3]]):
                return {"status": "MERGED_DATE_HEADERS_DO_NOT_ALIGN_WITH_COLUMN_PAIRS"}
            period_ends = [dates[0], dates[0], dates[1], dates[1]]
            date_type = "two_merged_date_headers_over_column_pairs"
        else:
            return {"status": "AMBIGUOUS_DATE_COLUMNS"}
    else:
        # Some bank headers put the year and month/day on separate printed rows.
        year_rows = [row for row in header if len(re.findall(r"20\d{2}年", compact(row["text"]))) == column_count]
        if not year_rows:
            # A previous-year header can sit one printed row above 项目/current.
            # Require one full date inside each numeric column's horizontal area.
            full_rows = [row for row in header if _dates_in_row(row)
                         and not any(token in row["text"] for token in ("编制", "单位"))]
            parts = []
            for i in range(column_count):
                lo = edges[i - 1] + 1.5 if i else -float("inf")
                hi = edges[i] + 1.5
                matches = []
                for row in full_rows:
                    text = "".join(w["text"] for w in row["words"]
                                   if lo < (w["bbox"][0] + w["bbox"][2]) / 2 <= hi)
                    matches.extend(DATE.findall(compact(text)))
                if len(matches) != 1:
                    return {"status": "NO_EXPLICIT_COLUMN_DATES"}
                y, m, d = matches[0]
                parts.append(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
            period_ends = parts
            date_evidence = [_evidence(row) for row in full_rows]
            date_type = "one_explicit_date_per_column_on_separate_printed_rows"
        else:
            year_row = year_rows[-1]
            index = header.index(year_row)
            subsequent = header[index + 1:index + 4]
            month_rows = [row for row in subsequent
                          if len(re.findall(r"\d{1,2}月\d{1,2}日", compact(row["text"]))) == column_count]
            if len(month_rows) != 1:
                return {"status": "AMBIGUOUS_STACKED_DATES"}
            month_row = month_rows[0]
            # Map fragments to actual geometric columns; printed order alone is insufficient.
            parts = []
            for i in range(column_count):
                lo = edges[i - 1] + 1.5 if i else -float("inf")
                hi = edges[i] + 1.5
                text = "".join(word["text"] for row in (year_row, month_row) for word in row["words"]
                               if lo < (word["bbox"][0] + word["bbox"][2]) / 2 <= hi)
                dates = DATE.findall(compact(text))
                if len(dates) != 1:
                    return {"status": "STACKED_DATES_DO_NOT_ALIGN_WITH_NUMERIC_COLUMNS"}
                y, m, d = dates[0]
                parts.append(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
            period_ends = parts
            date_evidence = [_evidence(year_row), _evidence(month_row)]
            date_type = "stacked_year_and_month_day_per_column"

    scope_evidence = []
    if scope == "combined":
        scope_rows = [(row, _scope_tokens(row)) for row in header]
        scope_rows = [(row, tokens) for row, tokens in scope_rows if len(tokens) in (2, 4)]
        if len(scope_rows) != 1 or column_count != 4:
            return {"status": "AMBIGUOUS_COMBINED_SCOPE_COLUMNS"}
        row, tokens = scope_rows[0]
        scope_evidence = [_evidence(row)]
        centres = [(w["bbox"][0] + w["bbox"][2]) / 2 for w in row["words"]
                   if compact(w["text"]) in ("合并数", "本集团", "集团", "公司数", "本行", "母公司")]
        if tokens == ["consolidated", "parent"] and date_type != "two_merged_date_headers_over_column_pairs":
            if not _centres_fit(centres, [edges[1], edges[3]]):
                return {"status": "MERGED_SCOPE_HEADERS_DO_NOT_ALIGN_WITH_COLUMN_PAIRS"}
            scopes = ["consolidated", "consolidated", "parent", "parent"]
        elif tokens == ["consolidated", "parent", "consolidated", "parent"] and date_type == "two_merged_date_headers_over_column_pairs":
            if not _centres_fit(centres, edges):
                return {"status": "SCOPE_HEADERS_DO_NOT_ALIGN_WITH_NUMERIC_COLUMNS"}
            scopes = tokens
        else:
            return {"status": "UNSUPPORTED_COMBINED_HEADER_LAYOUT"}
    else:
        if column_count != 2 or date_type == "two_merged_date_headers_over_column_pairs":
            return {"status": "UNSUPPORTED_ORDINARY_HEADER_LAYOUT"}
        scopes = [scope] * column_count
    columns = [{"column_index": i, "scope": scopes[i], "period_end": period_ends[i],
                "numeric_right_edge": edges[i]} for i in range(column_count)]
    qualifiers = [_evidence(row) for row in header if any(token in row["text"] for token in ("重述", "调整", "经审计", "未经审计"))]
    return {"status": "EXPLICIT_COLUMN_MAPPING", "columns": columns, "date_layout": date_type,
            "date_evidence": date_evidence, "scope_evidence": scope_evidence,
            "reported_header_qualifiers": qualifiers,
            "comparison_columns_are_not_prior_publication_snapshots": True}


def in_column(word: dict, i: int, edges: list[float]) -> bool:
    x = (word["bbox"][0] + word["bbox"][2]) / 2
    lo = edges[i - 1] + 1.5 if i else edges[0] - (edges[1] - edges[0])
    return lo < x <= edges[i] + 1.5


def _read_fields(body: list[dict], edges_by_page: dict[int, list[float]], multiplier: int) -> dict:
    rows_by_field = {key: [] for key in FIELDS}
    for index, row in enumerate(body):
        edges = edges_by_page[row["pdf_page"]]
        label = _label(row)
        key = _field(label)
        label_rows = [row]
        if key is None and index and row["pdf_page"] == body[index - 1]["pdf_page"]:
            previous = body[index - 1]
            # A wrapped label may have its numbers on either printed line.
            if not numeric_cells(previous) and row["bbox"][1] - previous["bbox"][3] < 12:
                key = _field(_label(previous) + label)
                if key:
                    label_rows = [previous, row]
        if key is None and index + 1 < len(body):
            following = body[index + 1]
            if following["pdf_page"] == row["pdf_page"] and not numeric_cells(following) and following["bbox"][1] - row["bbox"][3] < 12:
                key = _field(label + _label(following))
                if key:
                    label_rows = [row, following]
        if key is None:
            continue
        cells = []
        for i, edge in enumerate(edges):
            matches = [word for word in row["words"] if in_column(word, i, edges)
                       and (_cell_number(word["text"]) is not None or compact(word["text"]) in DASHES)]
            if len(matches) != 1:
                cells.append({"column_index": i, "status": "REPORTED_BLANK" if not matches else "AMBIGUOUS_CELL",
                              "reported_value": None, "value_yuan": None, "raw_cell": matches})
            else:
                word = matches[0]
                value = _cell_number(word["text"])
                cells.append({"column_index": i, "status": "NUMERIC" if value is not None else "REPORTED_DASH",
                              "reported_value": value,
                              "value_yuan": str(Decimal(value) * multiplier) if value is not None else None,
                              "raw_cell": word})
        # Numbers outside a verified column cannot silently turn into a blank.
        unassigned = [word for word in numeric_cells(row)
                      if not any(in_column(word, i, edges) for i in range(len(edges)))]
        if unassigned:
            for cell in cells:
                cell.update({"status": "UNASSIGNED_NUMBER_IN_ROW", "reported_value": None, "value_yuan": None})
        evidence = row.get("source_rows", [_evidence(r) for r in label_rows])
        rows_by_field[key].append({"label": label, "row_evidence": evidence,
                                   "cells": cells, "unassigned_numeric_cells": unassigned})
    fields = {}
    column_count = len(next(iter(edges_by_page.values())))
    for key, found in rows_by_field.items():
        if not found:
            fields[key] = {"status": "ROW_ABSENT", "rows": [], "cells": [
                {"column_index": i, "status": "ROW_ABSENT", "reported_value": None, "value_yuan": None}
                for i in range(column_count)]}
        elif len(found) > 1:
            fields[key] = {"status": "DUPLICATE_LABEL_REVIEW_REQUIRED", "rows": found, "cells": [
                {"column_index": i, "status": "DUPLICATE_LABEL_REVIEW_REQUIRED", "reported_value": None, "value_yuan": None}
                for i in range(column_count)]}
        else:
            fields[key] = {"status": "EXACT_LABEL_ROW", "rows": found, "cells": found[0]["cells"]}
    return fields


def reconcile(fields: dict, columns: list[dict], multiplier: int) -> list[dict]:
    checks = []
    for column in columns:
        i = column["column_index"]
        cells = [fields[key]["cells"][i] for key in ("assets", "liabilities", "equity", "liabilities_and_equity")]
        if any(cell["status"] != "NUMERIC" for cell in cells):
            checks.append({"column_index": i, "status": "TOTAL_MISSING_OR_AMBIGUOUS"})
            continue
        a, l, e, repeated = [Decimal(cell["reported_value"]) for cell in cells]
        resolution = max(Decimal(1).scaleb(value.as_tuple().exponent) for value in (a, l, e, repeated))
        tolerance = resolution * Decimal("1.5")
        residual = a - l - e
        repeat_residual = a - repeated
        status = ("EXACT" if residual == 0 and repeat_residual == 0 else
                  "WITHIN_REPORTED_ROUNDING" if abs(residual) <= tolerance and abs(repeat_residual) <= resolution
                  else "BALANCE_MISMATCH")
        checks.append({"column_index": i, "status": status, "assets_minus_liabilities_minus_equity_yuan": str(residual * multiplier),
                       "assets_minus_reported_balance_total_yuan": str(repeat_residual * multiplier),
                       "maximum_rounding_residual_yuan": str(tolerance * multiplier)})
    return checks


def parse_balance_sheet(rows: list[dict], scope: str, target_period: str) -> dict:
    rows = unfold_labels(rows)
    first_body = next((i for i, row in enumerate(rows) if _field(_label(row)) is not None), None)
    if first_body is None:
        return {"status": "NO_KNOWN_BALANCE_SHEET_ROWS"}
    header = rows[:first_body]
    body = rows[first_body:]
    # Totals determine the number of columns, not the first nonblank cash cell.
    total_rows = [row for row in body if _field(_label(row)) in ("assets", "liabilities", "equity", "liabilities_and_equity")]
    counts = [len(numeric_cells(row)) for row in total_rows]
    if len(total_rows) != 4 or len(set(counts)) != 1 or counts[0] not in (2, 4):
        return {"status": "INCOMPLETE_OR_NONSTANDARD_TOTAL_ROWS", "total_row_numeric_counts": counts}
    count = counts[0]
    edges_by_page = {}
    for page in sorted({row["pdf_page"] for row in body}):
        # Continuations can change physical column widths. Never carry x-coordinates
        # from the first page, nor shift a solitary previous-year cell left.
        aligned = [numeric_cells(row) for row in body if row["pdf_page"] == page
                   and len(numeric_cells(row)) == count and _field(_label(row)) is not None]
        if not aligned:
            continue
        page_edges = [median(cells[i]["bbox"][2] for cells in aligned) for i in range(count)]
        if any(page_edges[i + 1] - page_edges[i] < 20 for i in range(count - 1)):
            return {"status": "NUMERIC_COLUMNS_NOT_SEPARABLE"}
        if any(not in_column(cells[i], i, page_edges) for cells in aligned for i in range(count)):
            return {"status": "INCONSISTENT_NUMERIC_COLUMN_ALIGNMENT"}
        edges_by_page[page] = page_edges
    first_page = body[0]["pdf_page"]
    if first_page not in edges_by_page:
        return {"status": "FIRST_TABLE_PAGE_HAS_NO_ALIGNED_NUMERIC_COLUMNS"}
    # Pages with no selected field rows do not need inherited numeric coordinates.
    if any(_field(_label(row)) is not None and row["pdf_page"] not in edges_by_page for row in body):
        return {"status": "FIELD_PAGE_HAS_NO_ALIGNED_NUMERIC_COLUMNS"}
    edges = edges_by_page[first_page]
    units = [{**_evidence(row), "reported_unit": match[1], "multiplier_to_yuan": SCALES[match[1]]}
             for row in header if (match := UNIT.search(compact(row["text"])))]
    unit_set = {row["reported_unit"] for row in units}
    if len(unit_set) != 1:
        return {"status": "MISSING_OR_CONFLICTING_TABLE_UNIT", "unit_evidence": units}
    unit = next(iter(unit_set))
    # Actual 项目 headers override a preparation date printed above the table.
    project_rows = [i for i, row in enumerate(header)
                    if any(compact(w["text"]) in ("项目", "项目名称") for w in row["words"])]
    if project_rows:
        start = project_rows[-1]
        while start and header[start - 1]["pdf_page"] == header[start]["pdf_page"] and _dates_in_row(header[start - 1]):
            start -= 1
        table_header = header[start:]
    else:
        table_header = header
    mapped = _header_columns(table_header, count, scope, edges)
    if mapped["status"] != "EXPLICIT_COLUMN_MAPPING":
        return {**mapped, "reported_unit": unit, "unit_evidence": units}
    columns = mapped["columns"]
    current = [c["column_index"] for c in columns if c["period_end"] == target_period
               and c["scope"] in ("consolidated", "issuer_as_stated")]
    if len(current) != 1:
        return {"status": "NO_UNIQUE_CURRENT_NONPARENT_COLUMN", "column_mapping": mapped,
                "reported_unit": unit, "unit_evidence": units}
    continuation_headers = []
    for page, page_edges in edges_by_page.items():
        if page == first_page:
            continue
        page_rows = [row for row in rows if row["pdf_page"] == page]
        end = next(i for i, row in enumerate(page_rows) if _field(_label(row)) is not None)
        page_header = page_rows[:end]
        declared_units = [match[1] for row in page_header if (match := UNIT.search(compact(row["text"])))]
        if any(reported != unit for reported in declared_units):
            return {"status": "CONTINUATION_UNIT_CONFLICT"}
        if not any(_dates_in_row(row) or re.search(r"20\d{2}年", compact(row["text"])) for row in page_header
                   if not any(t in row["text"] for t in ("第三季度报告", "编制"))):
            continue
        page_map = _header_columns(page_header, count, scope, page_edges)
        if page_map["status"] != "EXPLICIT_COLUMN_MAPPING" or [(c["scope"], c["period_end"]) for c in page_map["columns"]] != [(c["scope"], c["period_end"]) for c in columns]:
            return {"status": "CONTINUATION_COLUMN_DECLARATION_CONFLICT", "continuation_mapping": page_map}
        continuation_headers.append({"pdf_page": page, "mapping": page_map})
    body = [row for row in body if row["pdf_page"] in edges_by_page]
    fields = _read_fields(body, edges_by_page, SCALES[unit])
    checks = reconcile(fields, columns, SCALES[unit])
    accepted = {"EXACT", "WITHIN_REPORTED_ROUNDING"}
    status = "RECONCILED_CURRENT_BALANCE_SHEET" if all(c["status"] in accepted for c in checks) else "TOTAL_RECONCILIATION_FAILED"
    return {"status": status, "reported_unit": unit, "multiplier_to_yuan": SCALES[unit], "unit_evidence": units,
            "column_mapping": mapped, "numeric_right_edges_by_pdf_page": edges_by_page,
            "continuation_column_mappings": continuation_headers,
            "current_column_index": current[0], "fields": fields, "balance_checks": checks}


def extract_company(rows: list[dict], target_period: str = "2019-09-30") -> dict:
    headings = [(i, heading) for i, row in enumerate(rows) if (heading := statement_heading(row["text"]))]
    candidates = []
    h = 0
    while h < len(headings):
        start, heading = headings[h]
        if heading["kind"] != "资产负债表" or heading["scope"] == "parent":
            h += 1
            continue
        last = h
        while last + 1 < len(headings) and headings[last + 1][1]["kind"] == heading["kind"] and headings[last + 1][1]["scope"] == heading["scope"] and headings[last + 1][1]["continued"]:
            last += 1
        end = headings[last + 1][0] if last + 1 < len(headings) else len(rows)
        section = rows[start:end]
        candidates.append({"candidate_index": len(candidates), "scope_heading": heading["scope"],
                           "heading_evidence": [_evidence(rows[headings[j][0]]) for j in range(h, last + 1)],
                           "start_pdf_page": section[0]["pdf_page"], "end_pdf_page": section[-1]["pdf_page"],
                           **parse_balance_sheet(section, heading["scope"], target_period)})
        h = last + 1
    complete = [c for c in candidates if c["status"] == "RECONCILED_CURRENT_BALANCE_SHEET"]
    return {"status": "UNIQUE_RECONCILED_CANDIDATE" if len(complete) == 1 else
            "NO_RECONCILED_CANDIDATE" if not complete else "MULTIPLE_RECONCILED_CANDIDATES",
            "selected_candidate_index": complete[0]["candidate_index"] if len(complete) == 1 else None,
            "candidates": candidates, "candidate_status_counts": dict(Counter(c["status"] for c in candidates)),
            "financial_values_independently_audited": False, "agent_signal_enabled": False}


def available_snapshot(company: dict, asof: str) -> dict | None:
    """A selected revised source is available only from its own disclosure date."""
    moment = datetime.fromisoformat(asof)
    available = datetime.fromisoformat(company["report_metadata"]["available_at_proxy"])
    if moment.utcoffset() is None or available.utcoffset() is None:
        raise ValueError("financial availability comparisons require timezone-aware timestamps")
    if company["status"] != "UNIQUE_RECONCILED_CANDIDATE" or available > moment:
        return None
    return company["candidates"][company["selected_candidate_index"]]
