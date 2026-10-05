"""Source-only restricted asset rows, explicit ending columns and missingness."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

SCALES = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}
DASHES = {"-", "--", "—", "——", "–", "/"}


def compact(text):
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def heading(text):
    value = re.sub(r"^(?:\(?[一二三四五六七八九十\d]+\)?[、.])", "", compact(text))
    return value in {"所有权或使用权受到限制的资产", "所有权或使用权受限制的资产", "所有权或使用权受限的资产"}


def notes_scope(text):
    value = re.sub(r"^(?:\(?[一二三四五六七八九十\d]+\)?[、.])", "", compact(text)).removesuffix("(续)")
    for prefix, scope in [("合并", "CONSOLIDATED_EXPLICIT"), ("母公司", "PARENT_EXPLICIT")]:
        if re.fullmatch(prefix + r"财务报表(?:主要)?(?:项目)?(?:注释|附注)", value):
            return scope
    return None


def numeric(cell):
    value = compact(cell["text"]) if cell else ""
    status = "REPORTED_BLANK" if not value else "REPORTED_DASH" if value in DASHES else "UNPARSABLE_CELL"
    cleaned = value.replace("−", "-")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    if re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", cleaned):
        return {"status": "NUMERIC", "reported_value": str(Decimal(cleaned.replace(",", ""))), "source_cell": cell}
    return {"status": status, "reported_value": None, "source_cell": cell}


def current_header(text):
    value = compact(text)
    if any(t in value for t in ("期初", "年初", "本期增加", "本期减少", "上期", "2018")):
        return False
    return value in {"期末账面价值", "期末余额", "期末数", "2019年6月30日", "2019.6.30", "2019-06-30"}


def profile(frame):
    first = next((i for i, row in enumerate(frame["rows"])
                  if i > 0 and row and row[0] and compact(row[0]["text"])
                  and compact(row[0]["text"]) not in {"项目", "资产项目", "类别", "受限原因"}
                  and not current_header(row[0]["text"]) and not re.match(r"20\d{2}[年月.\-]", compact(row[0]["text"]))), len(frame["rows"]))
    headers = frame["rows"][:first]
    labels = ["".join(compact(r[j]["text"]) for r in headers if j < len(r) and r[j]) for j in range(frame["column_count"])]
    choices = [i for i, text in enumerate(labels) if current_header(text)]
    if len(choices) != 1 or not frame["column_edges"]:
        return None
    reasons = [i for i, text in enumerate(labels) if text in {"受限原因", "使用受限原因", "权利受限原因"}]
    return {"current_column": choices[0], "reason_column": reasons[0] if len(reasons) == 1 else None,
            "header_rows": headers, "column_edges": frame["column_edges"], "basis": "explicit_report_ending_value_header"}


def unit(rows):
    for row in reversed(rows):
        value = compact(row["text"])
        match = re.search(r"(?:单位[:为](?:人民币)?|人民币)(百万元|千元|万元|亿元|元)", value)
        if match:
            other = any(t in value for t in ("美元", "港元", "欧元"))
            return {"status": "OTHER_CURRENCY_REVIEW_REQUIRED" if other else "REPORTED_UNIT",
                    "reported_unit": match[1], "scale": None if other else SCALES[match[1]],
                    "currency": "OTHER_OR_MIXED" if other else "CNY_EXPLICIT" if "人民币" in value else "CURRENCY_UNSPECIFIED",
                    "evidence": row}
    return {"status": "UNIT_NOT_FOUND", "reported_unit": None, "scale": None, "currency": "UNKNOWN", "evidence": None}


def component(label):
    value = compact(label)
    if value == "货币资金":
        return "RESTRICTED_MONEY_FUNDS_AS_REPORTED"
    if value in {"其他货币资金", "银行存款", "现金及现金等价物"}:
        return "RESTRICTED_CASH_SUBCATEGORY_AS_REPORTED"
    return "RESTRICTED_NONCASH_OR_OTHER_ASSET_AS_REPORTED"


def reconcile(rows, total, issues):
    if total is None:
        return {"status": "NO_EXPLICIT_TOTAL", "sum_as_reported": None}
    if total["reported_value"] is None or any(r["ending_value"]["reported_value"] is None for r in rows):
        return {"status": "NOT_CHECKABLE_MISSING_SOURCE_VALUE", "sum_as_reported": None}
    if issues or not rows:
        return {"status": "UNRESOLVED_TABLE_CONTEXT", "sum_as_reported": None}
    summed = sum((Decimal(r["ending_value"]["reported_value"]) for r in rows), Decimal(0))
    return {"status": "EXACT_SOURCE_TOTAL_MATCH" if summed == Decimal(total["reported_value"]) else "SOURCE_TOTAL_MISMATCH",
            "sum_as_reported": str(summed)}


def parse(frame, column_profile, monetary_unit, carried_rows=None, carried_total=None, carried_issues=None):
    if column_profile is None:
        return {"status": "CURRENT_COLUMN_UNPROVED", "current_column_profile": None, "rows": [], "total": None,
                "unit": monetary_unit, "issues": ["ending_value_header_not_proved"], "reconciliation": {"status": "NOT_CHECKABLE", "sum_as_reported": None}}
    values = list(carried_rows or [])
    issues = list(carried_issues or [])
    total = carried_total
    current = column_profile["current_column"]
    reason = column_profile["reason_column"]
    unique_reasons = {}
    if reason is not None:
        for row in frame["rows"]:
            if reason < len(row) and (cell := row[reason]) is not None:
                unique_reasons[tuple(cell["bbox"])] = cell
    for row in frame["rows"]:
        label_cell = row[0] if row else None
        label = compact(label_cell["text"]) if label_cell else ""
        amount = numeric(row[current] if current < len(row) else None)
        if label in {"项目", "资产项目", "类别"} or current_header(amount["source_cell"]["text"] if amount["source_cell"] else ""):
            continue
        if not label:
            if amount["reported_value"] is not None:
                issues.append("numeric_row_without_printed_asset_label")
            continue
        if label in {"合计", "总计"}:
            if total is not None:
                issues.append("multiple_printed_totals")
            total = {"label_cell": label_cell, **amount}
            continue
        if any(t in label for t in ("其中", "小计", "用于担保的资产", "母公司", "子公司")):
            issues.append("hierarchical_or_mixed_scope_asset_rows")
        if amount["status"] == "UNPARSABLE_CELL":
            issues.append("unparsable_ending_value")
        if amount["reported_value"] is not None and Decimal(amount["reported_value"]) < 0:
            issues.append("negative_ending_book_value_requires_review")
        sources = []
        if reason is not None and label_cell:
            low, high = label_cell["bbox"][1], label_cell["bbox"][3]
            sources = [c for c in unique_reasons.values() if min(high, c["bbox"][3]) - max(low, c["bbox"][1]) > 1]
            sources.sort(key=lambda c: c["bbox"][1])
        values.append({"label_as_printed": label_cell["text"], "label_cell": label_cell, "component_kind": component(label),
                       "ending_value": amount, "reason_cells": sources,
                       "reason_as_printed": "\n".join(c["text"] for c in sources),
                       "value_at_reported_unit_scale": str(Decimal(amount["reported_value"]) * monetary_unit["scale"])
                       if amount["reported_value"] is not None and monetary_unit["scale"] is not None else None})
    return {"status": "ENDING_ASSET_ROWS_REQUIRE_INDEPENDENT_CHECK", "current_column_profile": column_profile,
            "rows": values, "total": total, "unit": monetary_unit, "issues": sorted(set(issues)),
            "reconciliation": reconcile(values, total, issues)}


def continuation_safe(previous, current, contexts, anchor, expected_unit=None):
    between = [r for r in contexts if (r["pdf_page"] > previous["pdf_page"] or r["bbox"][1] >= previous["bbox"][3] - 2)
               and (r["pdf_page"] < current["pdf_page"] or r["bbox"][3] <= current["bbox"][1] + 2)
               and previous["pdf_page"] <= r["pdf_page"] <= current["pdf_page"]]
    for row in between:
        value = compact(row["text"])
        if not value or re.fullmatch(r"\d+(?:[/／]\d+)?", value):
            continue
        if heading(value) and row["pdf_page"] == anchor["pdf_page"]:
            continue
        if "半年度报告" in value and len(value) < 90:
            continue
        next_unit = unit([row])
        if next_unit["status"] != "UNIT_NOT_FOUND":
            if expected_unit is None or any(next_unit[k] != expected_unit[k] for k in ("status", "reported_unit", "scale", "currency")):
                return False
            continue
        return False
    return True
