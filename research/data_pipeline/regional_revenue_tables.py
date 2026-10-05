"""Read source-defined regional tables without inventing finer geography.

Inputs are physical bordered-table cells, not a flattened sequence of numbers.
Complete composition, main-business subsets and hierarchical scope stay distinct.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

SCALES = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}
MONEY = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
DASHES = {"-", "--", "—", "——", "–"}
REGION_MARKERS = {"分地区", "分区域", "地区", "区域"}
TOTALS = {"合计", "总计", "营业收入合计"}


def plain(text):
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def decimal_text(text):
    value = plain(text).replace("−", "-")
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    return str(Decimal(value.replace(",", ""))) if MONEY.fullmatch(value) else None


def percentage(text):
    value = plain(text)
    if not value.endswith("%") or (amount := decimal_text(value[:-1])) is None:
        return None
    return str(Decimal(amount) / 100)


def scope_heading(text):
    value = plain(text).rstrip(":")
    value = re.sub(r"^(?:\(?[一二三四五六七八九十\d]+\)?[、.．]?)", "", value)
    if value in {"营业收入构成", "营业收入构成及变动情况"}:
        return "FULL_REVENUE_COMPOSITION"
    if value in {"主营业务构成情况", "主营业务分行业、分产品、分地区情况", "主营业务分地区情况", "主营业务分行业情况", "主营业务分产品情况"}:
        return "MAIN_BUSINESS_SCOPE_ONLY"
    if value.startswith("占公司营业收入或营业利润") and "10%以上" in value:
        return "MATERIAL_SEGMENTS_ONLY"
    return None


def unit_from_context(rows):
    for row in reversed(rows):
        value = plain(row["text"])
        match = re.search(r"(?:金额)?单位(?:[:]|为)(?:人民币)?(?:\()?" r"(百万元|千元|万元|亿元|元)", value)
        if match:
            currency = "CNY_EXPLICIT" if "人民币" in value else "CURRENCY_NOT_EXPLICIT_IN_UNIT_LABEL"
            if any(token in value for token in ("美元", "港元", "欧元")):
                return {"status": "NON_CNY_UNIT_REVIEW_REQUIRED", "reported_unit": None, "scale": None, "currency": "OTHER_OR_MIXED", "evidence": row}
            return {"status": "REPORTED_UNIT", "reported_unit": match[1], "scale": SCALES[match[1]], "currency": currency, "evidence": row}
    return {"status": "UNIT_NOT_FOUND", "reported_unit": None, "scale": None, "currency": "UNKNOWN", "evidence": None}


def role_profile(frame, inherited=None):
    """Prove a current money column from headers, never from largest amount."""
    rows = frame["rows"]
    width = frame["column_count"]
    first_data = next((i for i, row in enumerate(rows) if sum(decimal_text(c["text"]) is not None for c in row if c) >= 1
                       and not all(plain(c["text"]) in {"2019", "2018", "2019年", "2018年"} for c in row if c and decimal_text(c["text"]) is not None)), len(rows))
    header = rows[:first_data]
    joined = ["".join(plain(row[j]["text"]) for row in header if j < len(row) and row[j]) for j in range(width)]
    direct = [j for j, text in enumerate(joined) if text in {"营业收入", "营业收入(元)", "营业收入(万元)"}]
    role = None
    if len(direct) == 1:
        role = {"current_money_column": direct[0], "current_percent_column": None, "basis": "explicit_current_operating_revenue_header_in_half_year_management_table"}
    else:
        amount_columns = [j for j, text in enumerate(joined) if "金额" in text and "占" not in text]
        current = []
        for column in amount_columns:
            owners = [row[column] for row in header if column < len(row) and row[column]]
            if any(any(t in plain(c["text"]) for t in ("本报告期", "本期", "2019年上半年", "2019年1-6月", "2019年1至6月")) for c in owners):
                current.append(column)
        if len(current) == 1:
            column = current[0]
            pct = column + 1 if column + 1 < width and "占营业收入比重" in joined[column + 1] else None
            role = {"current_money_column": column, "current_percent_column": pct, "basis": "explicit_current_period_amount_header_not_comparison"}
    if role:
        role.update({"header_rows": header, "column_count": width, "column_edges": frame["column_edges"], "header_page": frame["pdf_page"]})
        return role
    if inherited is not None and inherited["column_count"] == width and len(inherited["column_edges"]) == len(frame["column_edges"]):
        if max(abs(a-b) for a,b in zip(inherited["column_edges"], frame["column_edges"])) <= 2:
            return inherited
    return None


def money_cell(cell):
    if cell is None or not plain(cell["text"]):
        return {"status": "REPORTED_BLANK", "reported_value": None, "source_cell": cell}
    if plain(cell["text"]) in DASHES:
        return {"status": "REPORTED_DASH", "reported_value": None, "source_cell": cell}
    value = decimal_text(cell["text"])
    return {"status": "NUMERIC" if value is not None else "UNPARSABLE_CELL", "reported_value": value, "source_cell": cell}


def parse_region_block(frame, profile, start, kind, unit, carried_rows=None, inherited_total=None):
    if profile is None:
        return {"status": "CURRENT_COLUMN_REVIEW_REQUIRED", "rows": [], "total": None, "issues": ["current_money_column_not_proved"], "reported_shares": None}
    column = profile["current_money_column"]
    pct_column = profile["current_percent_column"]
    values = list(carried_rows or [])
    totals = []
    issues = []
    if inherited_total:
        totals.append(inherited_total)
    # A full-composition total can precede the regional subtable.
    if kind == "FULL_REVENUE_COMPOSITION":
        for row in frame["rows"][:start]:
            if row and row[0] and plain(row[0]["text"]) == "营业收入合计":
                totals.append({"label": "营业收入合计", **money_cell(row[column] if column < len(row) else None)})
    for row in frame["rows"][start:]:
        if not any(c and plain(c["text"]) for c in row):
            continue
        label_cell = row[0] if row else None
        label = plain(label_cell["text"]) if label_cell else ""
        if label in {"分行业", "分产品", "分业务"}:
            break
        if label in REGION_MARKERS or label in {"项目", "类别"}:
            continue
        selected = money_cell(row[column] if column < len(row) else None)
        if label in TOTALS:
            totals.append({"label": label, **selected})
            continue
        if len(label) > 50 or label.startswith(("注", "说明", "公司主营业务数据", "其他说明", "√", "□")):
            break
        if not label:
            if selected["status"] == "NUMERIC":
                issues.append("numeric_row_without_region_label")
            continue
        if any(t in label for t in ("母公司", "子公司", "合计", "其中")):
            issues.append("hierarchical_or_mixed_company_scope")
        printed_percent = (row[pct_column] if pct_column is not None and pct_column < len(row) else None)
        if selected["status"] != "NUMERIC":
            issues.append("missing_or_unparsable_current_revenue")
        values.append({"region_label_as_printed": label_cell["text"], "region_label_normalized": label,
                       "region_label_cell": label_cell, "current_revenue": selected,
                       "printed_current_share_cell": printed_percent,
                       "printed_current_share": percentage(printed_percent["text"]) if printed_percent else None,
                       "finer_geography_share": None, "shock_direction": "unknown"})
    if len({v["region_label_normalized"] for v in values}) != len(values):
        issues.append("duplicate_region_labels")
    numeric_totals = [t for t in totals if t["status"] == "NUMERIC"]
    if len({t["reported_value"] for t in numeric_totals}) > 1:
        issues.append("conflicting_reported_totals")
    total = numeric_totals[0] if numeric_totals else None
    if unit["scale"] is None:
        issues.append("unverified_unit")
    for value in values:
        amount = value["current_revenue"]["reported_value"]
        value["current_revenue_yuan_at_reported_unit"] = str(Decimal(amount) * unit["scale"]) if amount is not None and unit["scale"] is not None else None
    amount_sum = sum((Decimal(v["current_revenue"]["reported_value"]) for v in values if v["current_revenue"]["reported_value"] is not None), Decimal(0))
    difference = str(amount_sum - Decimal(total["reported_value"])) if total else None
    closed = bool(values and total and Decimal(total["reported_value"]) > 0 and Decimal(difference) == 0 and not issues)
    shares = None
    if closed:
        shares = [{"region_label_as_printed": v["region_label_as_printed"], "share_of_reported_table_total": str(Decimal(v["current_revenue"]["reported_value"]) / Decimal(total["reported_value"]))} for v in values]
        for v, derived in zip(values, shares):
            if v["printed_current_share"] is not None:
                decimals = len(plain(v["printed_current_share_cell"]["text"]).removesuffix("%").partition(".")[2])
                tolerance = Decimal(5) / (Decimal(10) ** (decimals + 3))
                if abs(Decimal(v["printed_current_share"]) - Decimal(derived["share_of_reported_table_total"])) > tolerance:
                    issues.append("printed_share_not_reconciled_to_current_total")
        if issues:
            closed, shares = False, None
    status = "REPORTED_REGIONS_RECONCILED_TO_EXPLICIT_TOTAL" if closed else (
        "NUMERIC_REGIONS_WITHOUT_COMPLETE_DENOMINATOR" if values and not issues else "REGIONAL_TABLE_REVIEW_REQUIRED")
    return {"status": status, "rows": values, "total": total, "sum_of_numeric_regional_values": str(amount_sum),
            "regional_sum_minus_explicit_total": difference, "issues": sorted(set(issues)), "reported_shares": shares,
            "current_column_profile": profile, "unit": unit, "table_scope": kind,
            "issuer_total_revenue_coverage_candidate": closed and kind == "FULL_REVENUE_COMPOSITION",
            "geographic_classification_basis": "issuer_label_as_stated_not_inferred_customer_or_production_location",
            "business_exposure_values_verified": False, "agent_signal_enabled": False}
