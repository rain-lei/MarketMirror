"""Revision preserving continuation QC and rejecting intervening sections.

The earlier unreviewed extractor remains immutable and reproducible.
"""

from decimal import Decimal

from .regional_revenue_tables import (
    TOTALS, REGION_MARKERS, decimal_text, money_cell, percentage, plain, role_profile,
    scope_heading, unit_from_context,
)


def continuation_context_is_safe(previous, current, contexts, previous_scope, current_scope, previous_unit, current_unit):
    unit_keys = ("status", "reported_unit", "scale", "currency")
    if previous_scope != current_scope or previous_unit is None or current_unit is None:
        return False
    if any(previous_unit.get(key) != current_unit.get(key) for key in unit_keys):
        return False
    if previous is None:
        return False
    for row in contexts:
        if previous["pdf_page"] == current["pdf_page"]:
            between = row["pdf_page"] == current["pdf_page"] and previous["bbox"][3]+2 < row["bbox"][1] and row["bbox"][3] < current["bbox"][1]-2
        else:
            between = ((row["pdf_page"] == previous["pdf_page"] and row["bbox"][1] > previous["bbox"][3]+2)
                       or (row["pdf_page"] == current["pdf_page"] and row["bbox"][3] < current["bbox"][1]-2))
        if not between:
            continue
        text = plain(row["text"])
        if not text or text.isdigit() or "半年度报告" in text or text.startswith("单位"):
            continue
        return False
    return True


def parse_region_block(frame, profile, start, kind, unit, carried_rows=None, inherited_total=None, carried_issues=None):
    if profile is None:
        return {"status": "CURRENT_COLUMN_REVIEW_REQUIRED", "rows": [], "total": None, "issues": ["current_money_column_not_proved"], "reported_shares": None}
    column = profile["current_money_column"]
    pct_column = profile["current_percent_column"]
    values = list(carried_rows or [])
    totals = []
    issues = list(carried_issues or [])
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
        if value["current_revenue"]["reported_value"] is None:
            issues.append("missing_or_unparsable_current_revenue")
        if any(token in value["region_label_normalized"] for token in ("母公司", "子公司", "合计", "其中")):
            issues.append("hierarchical_or_mixed_company_scope")
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
