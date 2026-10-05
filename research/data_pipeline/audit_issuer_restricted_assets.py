"""Independent source cells, ending headers, note scope and restricted totals."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pdfplumber

from .audit_issuer_regional_revenue import cells_for, digest, match_cell, normalized, number, serialize, verify_free_text

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_half_restricted_assets_audit_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_restricted_assets_audit_2019_v1.json"
SCALES = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}


def source_cell(cell, kind, pages, borders):
    if cell is None:
        return None
    if kind == "BORDERED_PHYSICAL_CELLS":
        return match_cell(borders[cell["pdf_page"]], cell)
    if kind == "EXPLICIT_HEADER_WORD_GEOMETRY":
        return {"text": verify_free_text(pages[cell["pdf_page"]], cell), "bbox": cell["bbox"]}
    raise ValueError("unregistered restricted asset geometry")


def check_current(profile, kind, pages, borders):
    column = profile["current_column"]
    cells = [r[column] for r in profile["header_rows"] if column < len(r) and r[column] is not None]
    texts = [source_cell(c, kind, pages, borders)["text"] for c in cells]
    combined = "".join(normalized(t) for t in texts)
    if combined not in {"期末账面价值", "期末余额", "期末数", "2019年6月30日", "2019.6.30", "2019-06-30"}:
        raise ValueError("independent ending-value header is not the current period")
    return len(cells)


def check_reconciliation(parsed, amounts, total):
    observed = parsed["reconciliation"]
    if total is None:
        expected = {"status": "NO_EXPLICIT_TOTAL", "sum_as_reported": None}
    elif any(v is None for v in amounts):
        expected = {"status": "NOT_CHECKABLE_MISSING_SOURCE_VALUE", "sum_as_reported": None}
    elif parsed["issues"] or not amounts:
        expected = {"status": "UNRESOLVED_TABLE_CONTEXT", "sum_as_reported": None}
    else:
        actual = sum(amounts, Decimal(0))
        expected = {"status": "EXACT_SOURCE_TOTAL_MATCH" if actual == total else "SOURCE_TOTAL_MISMATCH", "sum_as_reported": str(actual)}
    # A printed blank total is distinct from an absent printed total.
    if parsed["total"] is not None and parsed["total"]["reported_value"] is None:
        expected = {"status": "NOT_CHECKABLE_MISSING_SOURCE_VALUE", "sum_as_reported": None}
    if observed != expected:
        raise ValueError("independent restricted total or missingness differs")
    return expected["status"]


def check_table(table, pages, borders):
    parsed = table["parsed"]
    role = parsed["current_column_profile"]
    if role is None:
        return {"status": "CURRENT_COLUMN_UNPROVED_AS_EXPECTED", "numeric_cells_checked": 0, "reason_cells_checked": 0, "header_cells_checked": 0}
    kind = table["frames"][0]["geometry_kind"]
    headers = check_current(role, kind, pages, borders)
    anchor = verify_free_text(pages[table["anchor_evidence"]["pdf_page"]], table["anchor_evidence"])
    if not any(t in normalized(anchor) for t in ("所有权或使用权受到限制的资产", "所有权或使用权受限制的资产", "所有权或使用权受限的资产")):
        raise ValueError("independent restriction heading differs")
    if table["scope_evidence"] is not None:
        scope_text = normalized(verify_free_text(pages[table["scope_evidence"]["pdf_page"]], table["scope_evidence"]))
        prefix = "合并" if table["scope"] == "CONSOLIDATED_EXPLICIT" else "母公司" if table["scope"] == "PARENT_EXPLICIT" else None
        if prefix is None or prefix + "财务报表" not in scope_text or not any(t in scope_text for t in ("注释", "附注")):
            raise ValueError("independent note scope differs")
    elif table["scope"] != "UNKNOWN":
        raise ValueError("note scope without printed evidence")
    unit = parsed["unit"]
    if unit["evidence"]:
        text = normalized(verify_free_text(pages[unit["evidence"]["pdf_page"]], unit["evidence"]))
        match = re.search(r"(?:单位[:为](?:人民币)?|人民币)(百万元|千元|万元|亿元|元)", text)
        other = any(t in text for t in ("美元", "港元", "欧元"))
        currency = "OTHER_OR_MIXED" if other else "CNY_EXPLICIT" if "人民币" in text else "CURRENCY_UNSPECIFIED"
        if not match or unit["reported_unit"] != match[1] or unit["currency"] != currency or unit["scale"] != (None if other else SCALES[match[1]]):
            raise ValueError("independent restricted value unit differs")
    elif unit["scale"] is not None or unit["currency"] != "UNKNOWN":
        raise ValueError("unit without original evidence")
    checked = 0
    reason_checked = 0
    amounts = []
    left, right = role["column_edges"][role["current_column"]:role["current_column"]+2]
    for row in parsed["rows"]:
        label = source_cell(row["label_cell"], kind, pages, borders)
        if normalized(label["text"]) != normalized(row["label_as_printed"]):
            raise ValueError("restricted asset label differs")
        value = row["ending_value"]
        cell = source_cell(value["source_cell"], kind, pages, borders)
        actual = number(cell["text"]) if cell else None
        expected = Decimal(value["reported_value"]) if value["reported_value"] is not None else None
        if actual != expected:
            raise ValueError("restricted source value or missingness differs")
        if cell:
            center = (cell["bbox"][0]+cell["bbox"][2])/2
            if not left-3 <= center <= right+3:
                raise ValueError("value outside header-proved ending column")
        converted = Decimal(row["value_at_reported_unit_scale"]) if row["value_at_reported_unit_scale"] is not None else None
        if converted != (actual*unit["scale"] if actual is not None and unit["scale"] is not None else None):
            raise ValueError("restricted source unit conversion differs")
        reasons = []
        for source in row["reason_cells"]:
            reason_cell = source_cell(source, kind, pages, borders)
            if kind == "BORDERED_PHYSICAL_CELLS" and min(source["bbox"][3], row["label_cell"]["bbox"][3])-max(source["bbox"][1], row["label_cell"]["bbox"][1]) <= 1:
                raise ValueError("reason does not overlap its printed asset row")
            reasons.append(reason_cell["text"])
            reason_checked += 1
        if normalized("\n".join(reasons)) != normalized(row["reason_as_printed"]):
            raise ValueError("restricted reason text differs")
        amounts.append(actual)
        checked += actual is not None
    total = None
    if parsed["total"] is not None:
        source_cell(parsed["total"]["label_cell"], kind, pages, borders)
        cell = source_cell(parsed["total"]["source_cell"], kind, pages, borders)
        total = number(cell["text"]) if cell else None
        expected = Decimal(parsed["total"]["reported_value"]) if parsed["total"]["reported_value"] is not None else None
        if total != expected:
            raise ValueError("restricted printed total differs")
        checked += total is not None
    reconciliation = check_reconciliation(parsed, amounts, total)
    return {"status": "PASS_SOURCE_CELLS_AND_CALCULATION", "numeric_cells_checked": checked, "reason_cells_checked": reason_checked,
            "header_cells_checked": headers, "reconciliation_status": reconciliation, "source_reader_qc_required": table["source_reader_qc_required"],
            "scope": table["scope"], "geometry_kind": kind}


def compute():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    source = json.loads((ROOT/cfg["candidates_path"]).read_text(encoding="utf-8"))
    bindings = {**source["inputs"], **source["code_sha256"], **cfg["inputs"], CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG)}
    code = {f"research/data_pipeline/{n}.py": digest(ROOT/f"research/data_pipeline/{n}.py") for n in ("audit_issuer_restricted_assets", "audit_issuer_regional_revenue")}
    if pdfplumber.__version__ != cfg["pdfplumber_version"] or cfg["agent_signal_enabled"] is not False or cfg["border_coordinate_tolerance_points"] != 3:
        raise ValueError("restricted independent reader protocol differs")
    if len(source["companies"]) != cfg["expected_companies"] or len({c["stock_code"] for c in source["companies"]}) != cfg["expected_companies"]:
        raise ValueError("restricted independent cohort differs")
    for path, expected in {**bindings, **code}.items():
        if digest(ROOT/path) != expected:
            raise ValueError("restricted audit source binding differs: " + path)
    companies = []
    for company in source["companies"]:
        result = {"stock_code": company["stock_code"], "candidate_status": company["status"], "tables": []}
        try:
            if company["tables"]:
                with pdfplumber.open(ROOT/company["source_pdf_path"]) as doc:
                    nums = {f["pdf_page"] for t in company["tables"] for f in t["frames"]}
                    nums.update(e["pdf_page"] for t in company["tables"] for e in (t["anchor_evidence"], t["scope_evidence"], t["parsed"]["unit"]["evidence"]) if e)
                    pages = {n: doc.pages[n-1] for n in nums}
                    borders = {n: cells_for(p) for n, p in pages.items()}
                    for i, table in enumerate(company["tables"]):
                        try:
                            checked = check_table(table, pages, borders)
                        except Exception as error:
                            checked = {"status": "INDEPENDENT_REVIEW_REQUIRED", "error": f"{type(error).__name__}: {error}", "numeric_cells_checked": 0, "reason_cells_checked": 0, "header_cells_checked": 0}
                        result["tables"].append({"table_index": i, **checked})
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
            result["status"] = "INDEPENDENT_ISSUER_FAILED_REVIEW_REQUIRED"
        companies.append(result)
        print(json.dumps({"company": result["stock_code"], "tables": len(result["tables"]), "completed": len(companies)}), flush=True)
    for path, expected in {**bindings, **code}.items():
        if digest(ROOT/path) != expected:
            raise ValueError("restricted source changed during independent reading")
    tables = [t for c in companies for t in c["tables"]]
    return {"pipeline_version": cfg["version"], "companies": companies, "table_status_counts": dict(Counter(t["status"] for t in tables)),
            "numeric_cells_checked": sum(t["numeric_cells_checked"] for t in tables), "reason_cells_checked": sum(t["reason_cells_checked"] for t in tables),
            "header_cells_checked": sum(t["header_cells_checked"] for t in tables), "inputs": dict(sorted(bindings.items())), "code_sha256": code,
            "pdfplumber_version": pdfplumber.__version__, "agent_signal_enabled": False, "interpretation": "Only independently checked selected source cells and arithmetic; source QC, missingness, scope and cash subcategories are not overridden."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT/"research_outputs").resolve():
        raise ValueError("restricted audit output must be directly under research_outputs")
    value = compute()
    raw = serialize(value)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("restricted independent reconstruction differs")
        print("Restricted asset independent audit rebuilt byte-identically.")
    else:
        with output.open("xb") as f:
            f.write(raw)
    print(json.dumps({k: value[k] for k in ("table_status_counts", "numeric_cells_checked", "reason_cells_checked", "header_cells_checked")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
