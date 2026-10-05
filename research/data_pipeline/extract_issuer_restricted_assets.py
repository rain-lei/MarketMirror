"""Read frozen half-year restricted assets; retain every issuer and QC state."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import fitz

from . import restricted_assets_tables as reader
from .extract_issuer_regional_revenue_v2 import frame_for, contiguous
from .financial_balance_sheet import geometry_rows
from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_restricted_assets_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_restricted_assets_candidates_2019_v1.json"


def text_cell(words, page):
    if not words:
        return None
    return {"text": " ".join(w["text"] for w in words), "bbox": [min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words),
            max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words)], "pdf_page": page}


def unbordered_frame(context, anchor):
    """One explicit ending-value column, ending at a printed next note heading."""
    following = [r for r in context if r["pdf_page"] == anchor["pdf_page"] and r["bbox"][1] > anchor["bbox"][3]]
    header = next((r for r in following if any(reader.current_header(w["text"]) for w in r["words"])
                   and "受限原因" in reader.compact(r["text"])), None)
    if header is None:
        return None
    current = [w for w in header["words"] if reader.current_header(w["text"])]
    if len(current) != 1:
        return None
    left = current[0]["bbox"][0] - 5
    words = header["words"]
    reason = next(w for w in words if "受限原因" in reader.compact(w["text"]))
    rows = [[text_cell([w for w in words if w["bbox"][2] < left], anchor["pdf_page"]),
             text_cell(current, anchor["pdf_page"]), text_cell([reason], anchor["pdf_page"])]]
    for source in following:
        if source["bbox"][1] <= header["bbox"][3]:
            continue
        value = reader.compact(source["text"])
        if re.match(r"^(?:\(?[一二三四五六七八九十\d]+\)?[、.])", value) or value.startswith(("其他说明", "注1", "注：")):
            break
        money = [w for w in source["words"] if left <= (w["bbox"][0] + w["bbox"][2]) / 2 < reason["bbox"][0]
                 and reader.numeric({"text": w["text"]})["status"] in {"NUMERIC", "REPORTED_DASH"}]
        if len(money) > 1:
            return None
        labels = [w for w in source["words"] if w["bbox"][2] <= left]
        if not labels:
            continue
        selected = text_cell(money, anchor["pdf_page"])
        reason_words = [w for w in source["words"] if selected and w["bbox"][0] > selected["bbox"][2] + 1]
        rows.append([text_cell(labels, anchor["pdf_page"]), selected, text_cell(reason_words, anchor["pdf_page"])])
        if reader.compact(rows[-1][0]["text"]) in {"合计", "总计"}:
            break
    if len(rows) < 2:
        return None
    return {"pdf_page": anchor["pdf_page"], "page_height": anchor["page_height"], "bbox": [header["bbox"][0], header["bbox"][1],
            max(c["bbox"][2] for r in rows for c in r if c), max(c["bbox"][3] for r in rows for c in r if c)],
            "column_count": 3, "column_edges": [0, left, reason["bbox"][0], anchor["page_height"]], "rows": rows, "geometry_kind": "EXPLICIT_HEADER_WORD_GEOMETRY"}


def extract_company(source, audited, cfg):
    result = {k: source[k] for k in ("stock_code", "historical_short_name", "report_metadata", "source_pdf_path", "source_pdf_sha256")}
    result.update(literal_reader_status=audited["status"], tables=[], unmatched_anchors=[], agent_signal_enabled=False,
                  unrestricted_money_funds=None, financing_impact_coefficient=None)
    if source["source_pdf_path"] is None:
        return {**result, "status": "SOURCE_MISSING"}
    headings = [h for h in source["heading_candidates"] if h["kind"] == "ASSET_RESTRICTION_HEADING"]
    if not headings:
        return {**result, "status": "NO_RESTRICTION_HEADING_UNDER_FIXED_INDEX"}
    path = ROOT / source["source_pdf_path"]
    if digest(path) != source["source_pdf_sha256"]:
        raise ValueError("restricted assets source bytes differ")
    with fitz.open(path) as doc:
        pages = sorted({n for h in headings for n in range(max(1, h["pdf_page"] - 1), min(len(doc), h["pdf_page"] + 1) + 1)})
        contexts = [r for n in pages for r in geometry_rows(doc[n-1].get_text("words"), n, doc[n-1].rect.height)]
        scope_rows = []
        for n in range(1, max(h["pdf_page"] for h in headings) + 1):
            page = doc[n-1]
            if any(reader.notes_scope(line) for line in page.get_text().splitlines()):
                scope_rows.extend(r for r in geometry_rows(page.get_text("words"), n, page.rect.height) if reader.notes_scope(r["text"]))
        frames = []
        for n in pages:
            page = doc[n-1]
            frames.extend({**frame_for(t, n, page.rect.height), "geometry_kind": "BORDERED_PHYSICAL_CELLS"}
                          for t in sorted(page.find_tables().tables, key=lambda t: (t.bbox[1], t.bbox[0])))
        used = set()
        for h in headings:
            anchor = next((r for r in contexts if r["pdf_page"] == h["pdf_page"] and reader.heading(r["text"])), None)
            if anchor is None:
                result["unmatched_anchors"].append({"pdf_page": h["pdf_page"], "reason": "anchor_geometry_unresolved"})
                continue
            below = [f for f in frames if f["pdf_page"] == h["pdf_page"] and f["bbox"][1] >= anchor["bbox"][3] - 2]
            frame = next((f for f in below if reader.profile(f) is not None), None)
            # Do not cross another printed note to borrow an unrelated balance table.
            if frame and any(re.match(r"^\d+[、.]", reader.compact(r["text"])) and not reader.heading(r["text"])
                             for r in contexts if r["pdf_page"] == h["pdf_page"] and anchor["bbox"][3] < r["bbox"][1] < frame["bbox"][1]):
                frame = None
            if frame is None:
                frame = unbordered_frame(contexts, anchor)
            if frame is None:
                result["unmatched_anchors"].append({"pdf_page": h["pdf_page"], "reason": "supported_ending_value_table_not_detected"})
                continue
            key = (frame["pdf_page"], tuple(frame["bbox"]))
            if key in used:
                continue
            used.add(key)
            unit_rows = [r for r in contexts if r["pdf_page"] == anchor["pdf_page"] and r["bbox"][3] <= frame["bbox"][1] + 2
                         and (r["bbox"][1] >= anchor["bbox"][1] or r["bbox"][3] < r["page_height"] * .2)]
            monetary_unit = reader.unit(unit_rows)
            role = reader.profile(frame)
            scope = next((r for r in reversed(scope_rows) if r["pdf_page"] < anchor["pdf_page"]
                          or r["pdf_page"] == anchor["pdf_page"] and r["bbox"][3] <= anchor["bbox"][1]), None)
            parsed = reader.parse(frame, role, monetary_unit)
            block = {"frames": [frame], "anchor_evidence": anchor, "scope_evidence": scope,
                     "scope": reader.notes_scope(scope["text"]) if scope else "UNKNOWN", "parsed": parsed,
                     "source_reader_qc_required": audited["status"] != "PASS_LITERAL_PAGE_MAPS"}
            if frame["geometry_kind"] == "BORDERED_PHYSICAL_CELLS":
                pos = frames.index(frame)
                for next_frame in frames[pos+1:]:
                    previous = block["frames"][-1]
                    if not contiguous(previous, next_frame) or not reader.continuation_safe(previous, next_frame, contexts, anchor, monetary_unit):
                        break
                    next_role = reader.profile(next_frame) or role
                    if next_role["current_column"] != role["current_column"]:
                        break
                    block["frames"].append(next_frame)
                    parsed = reader.parse(next_frame, next_role, monetary_unit, parsed["rows"], parsed["total"], parsed["issues"])
                    block["parsed"] = parsed
            result["tables"].append(block)
    result["status"] = "RESTRICTED_ASSET_CANDIDATES_REQUIRE_INDEPENDENT_AUDIT" if result["tables"] else "RESTRICTION_TABLE_NOT_DETECTED_REVIEW_REQUIRED"
    return result


def compute():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if cfg["agent_signal_enabled"] is not False or cfg["source_period_end"] != "2019-06-30":
        raise ValueError("restricted assets protocol changed")
    index = json.loads((ROOT/cfg["index_path"]).read_text(encoding="utf-8"))
    audit = json.loads((ROOT/cfg["literal_audit_path"]).read_text(encoding="utf-8"))
    bindings = {**index["inputs"], **index["code_sha256"], **audit["inputs"], **cfg["inputs"],
                CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG),
                "research/data_pipeline/audit_issuer_business_evidence.py": audit["audit_code_sha256"]}
    names = ["extract_issuer_restricted_assets", "restricted_assets_tables", "extract_issuer_regional_revenue_v2",
             "regional_revenue_tables_v2", "regional_revenue_tables", "financial_balance_sheet", "fetch_issuer_disclosures"]
    code = {f"research/data_pipeline/{n}.py": digest(ROOT/f"research/data_pipeline/{n}.py") for n in names}
    validate_bindings(bindings)
    if fitz.VersionBind != cfg["pymupdf_version"]:
        raise ValueError("restricted assets PDF reader version differs")
    audited = {c["stock_code"]: c for c in audit["companies"]}
    companies = []
    for source in index["companies"]:
        try:
            company = extract_company(source, audited[source["stock_code"]], cfg)
        except Exception as error:
            company = {"stock_code": source["stock_code"], "historical_short_name": source["historical_short_name"],
                       "source_pdf_path": source["source_pdf_path"], "source_pdf_sha256": source["source_pdf_sha256"],
                       "report_metadata": source["report_metadata"], "literal_reader_status": audited[source["stock_code"]]["status"],
                       "status": "EXTRACTION_FAILED_REVIEW_REQUIRED", "error": f"{type(error).__name__}: {error}",
                       "tables": [], "agent_signal_enabled": False, "unrestricted_money_funds": None, "financing_impact_coefficient": None}
        companies.append(company)
        print(json.dumps({"company": company["stock_code"], "status": company["status"], "tables": len(company["tables"]), "completed": len(companies)}), flush=True)
    validate_bindings({**bindings, **code})
    if len(companies) != cfg["expected_companies"] or len({c["stock_code"] for c in companies}) != len(companies):
        raise ValueError("restricted asset cohort differs")
    tables = [t for c in companies for t in c["tables"]]
    return {"pipeline_version": cfg["version"], "companies": companies, "company_status_counts": dict(Counter(c["status"] for c in companies)),
            "table_count": len(tables), "numeric_current_asset_cells": sum(r["ending_value"]["reported_value"] is not None for t in tables for r in t["parsed"]["rows"]),
            "reconciliation_status_counts": dict(Counter(t["parsed"]["reconciliation"]["status"] for t in tables)),
            "inputs": dict(sorted(bindings.items())), "code_sha256": code, "pymupdf_version": fitz.VersionBind, "agent_signal_enabled": False,
            "interpretation": cfg["interpretation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT/"research_outputs").resolve():
        raise ValueError("restricted assets output must be directly under research_outputs")
    value = compute()
    raw = serialize(value)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("restricted assets reconstruction differs")
        print("Restricted asset candidates rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({k: value[k] for k in ("company_status_counts", "table_count", "numeric_current_asset_cells", "reconciliation_status_counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
