"""Source-period profit/cash-flow tables and additional balance-sheet rows.

No target returns, trading direction, zero-filled cash flow or free-cash claim.
The immutable balance reader supplies geometry primitives, never new aliases.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from statistics import median

from .financial_balance_sheet import SCALES, UNIT, compact, in_column

ASSET_FIELDS = {
    "trading_financial_assets": ("交易性金融资产",),
    "other_current_assets": ("其他流动资产",),
    "other_debt_investments": ("其他债权投资",),
    "other_equity_instrument_investments": ("其他权益工具投资",),
}
PROFIT_FIELDS = {
    "operating_total_revenue": ("营业总收入", "营业收入合计"),
    "operating_revenue": ("营业收入",),
    "operating_profit": ("营业利润",),
    "profit_total": ("利润总额",),
    "income_tax_expense": ("所得税费用",),
    "net_profit": ("净利润",),
    "net_profit_attributable_to_parent": ("归属于母公司所有者的净利润", "归属于母公司股东的净利润", "归属本行股东的净利润"),
    "minority_profit": ("少数股东损益",),
}
CASH_FIELDS = {
    "operating_cash_inflow": ("经营活动现金流入小计",),
    "operating_cash_outflow": ("经营活动现金流出小计",),
    "operating_net_cashflow": ("经营活动产生的现金流量净额", "经营活动产生/（使用）的现金流量净额", "经营活动（使用）/产生的现金流量净额", "经营活动使用的现金流量净额"),
    "investing_cash_inflow": ("投资活动现金流入小计",),
    "investing_cash_outflow": ("投资活动现金流出小计",),
    "investing_net_cashflow": ("投资活动产生的现金流量净额", "投资活动（使用）/产生的现金流量净额"),
    "financing_cash_inflow": ("筹资活动现金流入小计",),
    "financing_cash_outflow": ("筹资活动现金流出小计",),
    "financing_net_cashflow": ("筹资活动产生的现金流量净额", "筹资活动（使用）的现金流量净额"),
    "fx_effect_on_cash": ("汇率变动对现金及现金等价物的影响",),
    "cash_equivalent_net_change": ("现金及现金等价物净增加额", "现金及现金等价物净减少额", "现金及现金等价物净（减少）/增加额"),
    "cash_equivalent_opening": ("期初现金及现金等价物余额", "年初现金及现金等价物余额", "1月1日的现金及现金等价物余额"),
    "cash_equivalent_closing": ("期末现金及现金等价物余额", "9月30日的现金及现金等价物余额"),
}
NUMBER = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
DASHES = {"-", "--", "—", "——", "–", "－"}


def evidence(row):
    return {k: row[k] for k in ("pdf_page", "bbox", "text")}


def number(text):
    value = compact(text).replace("−", "-").replace("－", "-")
    if value.startswith("（") and value.endswith("）"):
        value = "-" + value[1:-1]
    return value.replace(",", "") if NUMBER.fullmatch(value) else None


def monetary_words(row):
    return [w for i, w in enumerate(row["words"]) if number(w["text"]) is not None and not (
        re.fullmatch(r"\d{1,2}", w["text"]) and i + 1 < len(row["words"]) and
        compact(row["words"][i + 1]["text"]).startswith(("月", "日")))]


def left_label(row):
    parts = []
    for index, word in enumerate(row["words"]):
        date_fragment = (re.fullmatch(r"\d{1,2}", word["text"]) and index + 1 < len(row["words"]) and
                         compact(row["words"][index + 1]["text"]).startswith(("月", "日")))
        if (number(word["text"]) is not None and not date_fragment) or compact(word["text"]) in DASHES:
            break
        parts.append(word["text"])
    return compact("".join(parts))


def canonical_label(text):
    text = compact(text)
    text = re.sub(r"^(?:[一二三四五六七八九十]+[、.．]|\d+[.．、])", "", text)
    text = re.sub(r"^(?:其中|加|减)[:：]", "", text)
    # Only reporting-loss qualifiers are stripped; （续） is not a duplicate field.
    text = re.sub(r"（(?:净|亏|损失)[^（）]*(?:）|$)", "", text)
    return text.rstrip("：:")


def match_field(text, labels):
    clean = canonical_label(text)
    return next((key for key, aliases in labels.items() if clean in {compact(a) for a in aliases}), None)


def labelled_rows(rows, labels):
    """Exact bounded label spans, including an intervening numeric-only line."""
    labels = {**labels, "_guard_continuing_profit": ("持续经营净利润",), "_guard_discontinued_profit": ("终止经营净利润",)}
    rows = [r for r in rows if not ("第三季度报告" in compact(r["text"]) or
            re.fullmatch(r"\d{1,3}", compact(r["text"])) and r["bbox"][1] > r["page_height"] * .9)]
    aliases = {compact(a) for values in labels.values() for a in values}
    result, i = [], 0
    while i < len(rows):
        first = rows[i]
        label = left_label(first)
        field = match_field(label, labels)
        end, combined = i, label
        numeric = monetary_words(first)
        raw_clean = canonical_label(label)
        partial = any(a.startswith(raw_clean) for a in aliases) if raw_clean else False
        if field or partial:
            for j in range(i + 1, min(i + 8, len(rows))):
                following = rows[j]
                previous = rows[j - 1]
                cross_page = (following["pdf_page"] == previous["pdf_page"] + 1 and previous["bbox"][1] > previous["page_height"] * .7 and
                              following["bbox"][1] < following["page_height"] * .2 and
                              any(a.startswith(canonical_label(combined)) and a != canonical_label(combined) for a in aliases))
                if (following["pdf_page"] != previous["pdf_page"] and not cross_page or
                        following["pdf_page"] == previous["pdf_page"] and following["bbox"][1] - previous["bbox"][3] >= 16):
                    break
                fragment = left_label(following)
                candidate = combined + fragment
                clean = canonical_label(candidate)
                found = match_field(candidate, labels)
                # A complete line with amounts is sufficient; do not consume the next field.
                if field and numeric:
                    break
                if match_field(fragment, labels) and fragment and not found:
                    break
                if not found and not any(a.startswith(clean) for a in aliases):
                    break
                combined, end, field = candidate, j, found
                numeric += monetary_words(following)
                if field and numeric:
                    break
        if field:
            group = rows[i:end + 1]
            if not field.startswith("_guard_"):
                result.append({"field": field, "label": combined, "rows": [evidence(r) for r in group],
                               "pdf_page": first["pdf_page"],
                               "words": [{**w, "pdf_page": r["pdf_page"]} for r in group for w in r["words"]]})
            i = end + 1
        else:
            i += 1
    return result


def read_fields(rows, labels, edges_by_page, multiplier):
    collected = {f: [] for f in labels}
    for row in labelled_rows(rows, labels):
        if any(r["pdf_page"] not in edges_by_page for r in row["rows"]):
            continue
        edges = edges_by_page[row["pdf_page"]]
        cells = []
        for i in range(len(edges)):
            words = [w for w in row["words"] if in_column(w, i, edges_by_page[w["pdf_page"]])
                     and (number(w["text"]) is not None or compact(w["text"]) in DASHES)]
            value = number(words[0]["text"]) if len(words) == 1 else None
            status = ("REPORTED_BLANK" if not words else "AMBIGUOUS_CELL" if len(words) > 1 else
                      "NUMERIC" if value is not None else "REPORTED_DASH")
            cells.append({"column_index": i, "status": status, "reported_value": value,
                          "value_yuan": str(Decimal(value) * multiplier) if value is not None else None,
                          "raw_cell": words})
        outside = [w for w in monetary_words(row) if
                   not any(in_column(w, i, edges_by_page[w["pdf_page"]]) for i in range(len(edges)))]
        if outside:
            for cell in cells:
                cell.update(status="UNASSIGNED_NUMBER_IN_ROW", reported_value=None, value_yuan=None)
        collected[row["field"]].append({"label": row["label"], "row_evidence": row["rows"],
                                         "cells": cells, "unassigned_numeric_words": outside})
    count = len(next(iter(edges_by_page.values())))
    result = {}
    for f, found in collected.items():
        if len(found) == 1:
            result[f] = {"status": "EXACT_LABEL_ROW", "rows": found, "cells": found[0]["cells"]}
        else:
            status = "ROW_ABSENT" if not found else "DUPLICATE_LABEL_REVIEW_REQUIRED"
            result[f] = {"status": status, "rows": found,
                         "cells": [{"column_index": i, "status": status, "reported_value": None, "value_yuan": None}
                                   for i in range(count)]}
    return result


def extract_additional_assets(rows, balance_candidate):
    """Use the audited balance-table bounds, not the whole ending PDF page."""
    first = balance_candidate["heading_evidence"][0]
    totals = [r for found in balance_candidate["fields"]["liabilities_and_equity"]["rows"]
              for r in found["row_evidence"]]
    if not totals:
        raise ValueError("audited balance candidate has no final total boundary")
    last = max(totals, key=lambda r: (r["pdf_page"], r["bbox"][3]))
    selected = [r for r in rows if
                (r["pdf_page"] > first["pdf_page"] or r["pdf_page"] == first["pdf_page"] and r["bbox"][1] >= first["bbox"][1]) and
                (r["pdf_page"] < last["pdf_page"] or r["pdf_page"] == last["pdf_page"] and r["bbox"][3] <= last["bbox"][3] + .1)]
    edges = {int(k): v for k, v in balance_candidate["numeric_right_edges_by_pdf_page"].items()}
    return {"heading_boundary": first, "final_total_boundary": last,
            "reported_unit": balance_candidate["reported_unit"],
            "column_mapping": balance_candidate["column_mapping"],
            "current_column_index": balance_candidate["current_column_index"],
            "fields": read_fields(selected, ASSET_FIELDS, edges, balance_candidate["multiplier_to_yuan"])}


def heading(text):
    value = compact(text)
    value = re.sub(r"^(?:（[一二三四五六七八九十]+）[、.]?|\d+[、.．]|[一二三四五六七八九十]+[、.．])", "", value)
    continued = value.endswith("（续）")
    value = re.sub(r"（(?:续|未经审计)）$", "", value)
    match = re.fullmatch(r"(合并及母公司|合并|母公司)?(本报告期|年初到报告期末|年初至报告期末)?(利润表|现金流量表|资产负债表)(?:和(?:利润表|现金流量表|资产负债表))?", value)
    if not match:
        return None
    prefix, window, kind = match.groups()
    scope = "combined" if prefix == "合并及母公司" or "和" in value else "consolidated" if prefix == "合并" else "parent" if prefix == "母公司" else "issuer_as_stated"
    return {"kind": kind, "scope": scope, "flow_window_heading": "quarter" if window == "本报告期" else "ytd" if window else None,
            "continued": continued}


def period(text):
    value = compact(text).replace("—", "-").replace("－", "-").replace("至", "-")
    full = re.findall(r"(20\d{2})年(\d{1,2})月(\d{1,2})日-(20\d{2})年(\d{1,2})月(\d{1,2})日", value)
    if len(full) == 1:
        y, m, d, yy, mm, dd = map(int, full[0])
        return (f"{y:04}-{m:02}-{d:02}", f"{yy:04}-{mm:02}-{dd:02}")
    ranges = re.findall(r"(20\d{2})年(\d{1,2})-(\d{1,2})月", value)
    if len(ranges) == 1:
        y, m, mm = map(int, ranges[0])
        if mm != 9 or m not in (1, 7):
            return None
        return (f"{y:04}-{m:02}-01", f"{y:04}-09-30")
    years = set(re.findall(r"20\d{2}", value))
    if len(years) == 1 and ("前三季度" in value or "第三季度" in value):
        y = next(iter(years))
        return (y + ("-01-01" if "前三季度" in value else "-07-01"), y + "-09-30")
    return None


def text_in_areas(rows, edges):
    return ["".join(w["text"] for row in rows for w in row["words"] if in_column(w, i, edges)) for i in range(len(edges))]


def map_columns(header, edges, declared, report_year):
    count = len(edges)
    project = [i for i, row in enumerate(header) if any(compact(w["text"]) in ("项目", "项目名称") for w in row["words"])]
    if project:
        start = project[-1]
        while start and header[start - 1]["pdf_page"] == header[start]["pdf_page"] and (period(header[start - 1]["text"]) or re.search(r"20\d{2}年", compact(header[start - 1]["text"]))):
            start -= 1
        actual = header[start:]
    else:
        markers = [i for i, row in enumerate(header) if any(compact(w["text"]) in ("本集团", "本行", "合并数", "公司数") for w in row["words"])]
        actual = header[markers[-1]:] if markers else [r for r in header if not any(x in r["text"] for x in ("编制", "单位"))]
    actual = [r for r in actual if not any(token in r["text"] for token in ("第三季度报告", "编制单位", "单位：", "单位:"))]
    scope_rows = [row for row in actual if any(compact(w["text"]) in ("本集团", "本行", "合并数", "公司数") for w in row["words"])]
    scopes = [declared["scope"]] * count
    merged = False
    if declared["scope"] == "combined":
        if len(scope_rows) != 1 or count != 4:
            return {"status": "AMBIGUOUS_SCOPE_HEADERS"}
        tokens = [compact(w["text"]) for w in scope_rows[0]["words"] if compact(w["text"]) in ("本集团", "本行", "合并数", "公司数")]
        if tokens == ["合并数", "公司数", "合并数", "公司数"]:
            scopes, merged = ["consolidated", "parent", "consolidated", "parent"], True
        elif tokens == ["本集团", "本行"]:
            scopes = ["consolidated", "consolidated", "parent", "parent"]
        else:
            return {"status": "UNSUPPORTED_SCOPE_HEADER_PATTERN"}
    period_edges = [edges[1], edges[3]] if merged else edges
    areas = text_in_areas(actual, period_edges)
    periods = [period(text) for text in areas]
    basis = "explicit_geometric_period_headers"
    roles = ["explicit_source_period"] * len(periods)
    if count == 2 and declared["flow_window_heading"]:
        current = [i for i, text in enumerate(areas) if "本期发生额" in compact(text)]
        prior = [i for i, text in enumerate(areas) if "上期发生额" in compact(text)]
        if len(current) == len(prior) == 1 and current != prior:
            start = "-01-01" if declared["flow_window_heading"] == "ytd" else "-07-01"
            expected_current = (str(report_year) + start, str(report_year) + "-09-30")
            if periods[current[0]] is not None and periods[current[0]] != expected_current:
                return {"status": "EXPLICIT_AND_RELATIVE_PERIOD_CONFLICT"}
            periods[current[0]] = expected_current
            roles[current[0]] = "current_period_from_formal_heading_and_report_year"
            # 上期 is a relative label, not an explicit prior-year publication/date.
            if periods[prior[0]] is None:
                periods[prior[0]] = (None, None)
                roles[prior[0]] = "previous_period_as_reported_dates_undeclared"
            basis = "relative_header_current_window_with_undeclared_comparison_dates_retained"
    if any(p is None for p in periods):
        return {"status": "MISSING_OR_AMBIGUOUS_PERIOD_HEADERS", "header_area_text": areas}
    if merged:
        periods = [periods[0], periods[0], periods[1], periods[1]]
        roles = [roles[0], roles[0], roles[1], roles[1]]
    return {"status": "SOURCE_PERIOD_COLUMN_MAPPING", "basis": basis,
            "columns": [{"column_index": i, "scope": scopes[i], "period_start": p[0], "period_end": p[1],
                         "period_basis": roles[i]} for i, p in enumerate(periods)],
            "header_evidence": [evidence(r) for r in actual], "header_area_text": areas,
            "comparison_columns_are_not_prior_publications": True}


def identity(name, fields, index, terms):
    cells = [fields[f]["cells"][index] for f, _ in terms]
    if any(c["status"] != "NUMERIC" for c in cells):
        return {"name": name, "column_index": index, "status": "NOT_CHECKABLE_MISSING_SOURCE_VALUE"}
    values = [Decimal(c["reported_value"]) for c in cells]
    residual = sum((v * sign for v, (_, sign) in zip(values, terms, strict=True)), Decimal(0))
    resolution = max(Decimal(1).scaleb(v.as_tuple().exponent) for v in values)
    tolerance = resolution * Decimal(len(terms)) / 2
    return {"name": name, "column_index": index, "status": "EXACT" if residual == 0 else
            "WITHIN_REPORTED_ROUNDING" if abs(residual) <= tolerance else "IDENTITY_MISMATCH",
            "residual_reported_unit": str(residual), "maximum_rounding_residual_reported_unit": str(tolerance)}


def checks(fields, count, kind):
    result = []
    for i in range(count):
        if kind == "利润表":
            tax = fields["income_tax_expense"]["cells"][i]
            words = tax.get("raw_cell", [])
            signed_parenthesis = len(words) == 1 and compact(words[0]["text"]).startswith("（")
            result.append({**identity("profit_total_tax_net", fields, i, [("profit_total", 1), ("income_tax_expense", 1 if signed_parenthesis else -1), ("net_profit", -1)]),
                           "tax_presentation": "parenthesized_signed_expense_added" if signed_parenthesis else "reported_tax_expense_subtracted"})
            result.append(identity("parent_plus_minority_profit", fields, i, [("net_profit_attributable_to_parent", 1), ("minority_profit", 1), ("net_profit", -1)]))
        else:
            for channel in ("operating", "investing", "financing"):
                words = fields[channel + "_cash_outflow"]["cells"][i].get("raw_cell", [])
                signed_parenthesis = len(words) == 1 and compact(words[0]["text"]).startswith("（")
                result.append({**identity(channel + "_inflow_minus_outflow", fields, i,
                                          [(channel + "_cash_inflow", 1), (channel + "_cash_outflow", 1 if signed_parenthesis else -1),
                                           (channel + "_net_cashflow", -1)]),
                               "outflow_presentation": "parenthesized_signed_outflow_added" if signed_parenthesis else "reported_outflow_subtracted"})
            result.append(identity("opening_plus_change_closing", fields, i, [("cash_equivalent_opening", 1), ("cash_equivalent_net_change", 1), ("cash_equivalent_closing", -1)]))
            result.append(identity("three_activities_plus_fx_change", fields, i, [("operating_net_cashflow", 1), ("investing_net_cashflow", 1), ("financing_net_cashflow", 1), ("fx_effect_on_cash", 1), ("cash_equivalent_net_change", -1)]))
    return result


def parse_statement(rows, declared, report_year):
    kind = declared["kind"]
    labels = PROFIT_FIELDS if kind == "利润表" else CASH_FIELDS
    labelled = labelled_rows(rows, labels)
    numeric_rows = [row for row in labelled if len(monetary_words(row)) >= 2]
    if not numeric_rows:
        return {"status": "NO_RECOGNIZED_MONETARY_ROWS"}
    count = max(len(monetary_words(row)) for row in numeric_rows)
    if count not in (2, 4):
        return {"status": "NONSTANDARD_MONETARY_COLUMN_COUNT", "column_count": count}
    first = next(i for i, r in enumerate(rows) if any(r["pdf_page"] == t["pdf_page"] and r["bbox"] == t["rows"][0]["bbox"] for t in labelled))
    if kind == "现金流量表":
        body_starts = [i for i, row in enumerate(rows[:first + 1]) if canonical_label(left_label(row)) == "经营活动产生的现金流量"]
        if body_starts:
            first = body_starts[-1]
    edges = {}
    for p in sorted({r["pdf_page"] for r in rows[first:]}):
        full = [monetary_words(r) for r in rows[first:] if r["pdf_page"] == p and len(monetary_words(r)) == count]
        if full:
            edges[p] = [median(r[i]["bbox"][2] for r in full) for i in range(count)]
    if not edges:
        return {"status": "NO_COMPLETE_COLUMN_GEOMETRY"}
    # An optional blank-only continuation needs its own complete monetary row,
    # rather than inheriting an arbitrary physical x-coordinate from another page.
    missing_pages = sorted({e["pdf_page"] for r in labelled for e in r["rows"]} - set(edges))
    if missing_pages:
        return {"status": "FIELD_PAGE_WITHOUT_COMPLETE_COLUMN_GEOMETRY", "pdf_pages": missing_pages}
    header = rows[:first]
    units = [{**evidence(r), "reported_unit": m[1]} for r in header if (m := UNIT.search(compact(r["text"])))]
    unique = {r["reported_unit"] for r in units}
    if len(unique) != 1:
        return {"status": "MISSING_OR_CONFLICTING_STATEMENT_UNIT", "unit_evidence": units}
    unit = next(iter(unique))
    if rows[first]["pdf_page"] in edges:
        mapping = map_columns(header, edges[rows[first]["pdf_page"]], declared, report_year)
    else:
        # A table can begin with headings only and put every amount on the next
        # page. Read complete dates from the actual 项目 row in their printed
        # order; never apply another page's physical x-coordinates to that row.
        project = [r for r in header if any(compact(w["text"]) == "项目" for w in r["words"])]
        date_tokens = re.findall(r"20\d{2}年(?:\d{1,2}[-—－]\d{1,2}月|前三季度|第三季度)",
                                 compact(project[-1]["text"])) if project else []
        periods = [period(t) for t in date_tokens]
        if count != 2 or len(periods) != count or any(p is None for p in periods) or declared["scope"] == "combined":
            return {"status": "HEADER_BODY_PAGE_WITHOUT_COMPLETE_COLUMN_GEOMETRY"}
        mapping = {"status": "SOURCE_PERIOD_COLUMN_MAPPING", "basis": "explicit_complete_periods_in_actual_project_header_order",
                   "columns": [{"column_index": i, "scope": declared["scope"], "period_start": p[0], "period_end": p[1],
                                "period_basis": "explicit_source_period"} for i, p in enumerate(periods)],
                   "header_evidence": [evidence(r) for r in project], "header_area_text": date_tokens,
                   "comparison_columns_are_not_prior_publications": True}
    if mapping["status"] != "SOURCE_PERIOD_COLUMN_MAPPING":
        return {**mapping, "reported_unit": unit}
    fields = read_fields(rows[first:], labels, edges, SCALES[unit])
    required = ("profit_total", "income_tax_expense", "net_profit") if kind == "利润表" else (
        "operating_net_cashflow", "cash_equivalent_net_change", "cash_equivalent_opening", "cash_equivalent_closing")
    current = [c["column_index"] for c in mapping["columns"] if c["scope"] != "parent" and c["period_start"] == f"{report_year}-01-01" and c["period_end"] == f"{report_year}-09-30"]
    arithmetic = checks(fields, count, kind)
    status = "SOURCE_DATED_OPERATING_STATEMENT"
    if len(current) != 1:
        status = "NO_UNIQUE_CURRENT_YTD_NONPARENT_COLUMN"
    elif any(fields[f]["cells"][current[0]]["status"] != "NUMERIC" for f in required):
        status = "MISSING_REQUIRED_CURRENT_STATEMENT_TOTAL"
    elif any(c["status"] == "IDENTITY_MISMATCH" for c in arithmetic):
        status = "SOURCE_STATEMENT_IDENTITY_MISMATCH"
    return {"status": status, "reported_unit": unit, "multiplier_to_yuan": SCALES[unit], "unit_evidence": units,
            "column_mapping": mapping, "numeric_right_edges_by_pdf_page": edges,
            "current_ytd_column_index": current[0] if len(current) == 1 else None, "fields": fields, "identity_checks": arithmetic}


def extract_flows(rows, report_year, minimum_page):
    headings = [(i, h) for i, r in enumerate(rows) if r["pdf_page"] >= minimum_page and (h := heading(r["text"]))]
    candidates = {"利润表": [], "现金流量表": []}
    i = 0
    while i < len(headings):
        start, h = headings[i]
        if h["kind"] not in candidates or h["scope"] == "parent":
            i += 1
            continue
        last = i
        while last + 1 < len(headings) and headings[last + 1][1]["kind"] == h["kind"] and headings[last + 1][1]["scope"] == h["scope"] and headings[last + 1][1]["continued"]:
            last += 1
        end = headings[last + 1][0] if last + 1 < len(headings) else len(rows)
        section = rows[start:end]
        result = parse_statement(section, h, report_year)
        candidates[h["kind"]].append({"candidate_index": len(candidates[h["kind"]]), "declared_heading": h,
                                      "heading_evidence": [evidence(rows[headings[j][0]]) for j in range(i, last + 1)],
                                      "start_pdf_page": section[0]["pdf_page"], "end_pdf_page": section[-1]["pdf_page"], **result})
        i = last + 1
    selection = {}
    for kind, found in candidates.items():
        good = [c for c in found if c["status"] == "SOURCE_DATED_OPERATING_STATEMENT"]
        selection[kind] = {"status": "UNIQUE_SOURCE_DATED_YTD_CANDIDATE" if len(good) == 1 else
                           "NO_SOURCE_DATED_YTD_CANDIDATE" if not good else "MULTIPLE_SOURCE_DATED_YTD_CANDIDATES",
                           "selected_candidate_index": good[0]["candidate_index"] if len(good) == 1 else None,
                           "candidates": found, "candidate_status_counts": dict(Counter(c["status"] for c in found))}
    return selection
