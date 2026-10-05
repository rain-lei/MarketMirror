"""Preserve bordered regional revenue cells and source-defined coverage."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import fitz

from . import regional_revenue_tables as reader
from .financial_balance_sheet import geometry_rows
from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_regional_revenue_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_regional_revenue_candidates_2019_v1.json"


def frame_for(table, number, height):
    values = table.extract()
    rows = []
    for texts, physical in zip(values, table.rows):
        rows.append([None if rect is None else {"text": text or "", "bbox": list(rect), "pdf_page": number}
                     for text, rect in zip(texts, physical.cells)])
    complete = next((row for row in rows if len(row) == table.col_count and all(c is not None for c in row)), None)
    if complete:
        edges = [complete[0]["bbox"][0]] + [c["bbox"][2] for c in complete]
    else:
        # Header-only continuation fragments have horizontally merged date cells.
        # Narrow owned rectangles recover each actual column without equal-width guesses.
        owners = []
        for j in range(table.col_count):
            candidates = [row[j] for row in rows if j < len(row) and row[j] is not None]
            owners.append(min(candidates, key=lambda c:c["bbox"][2]-c["bbox"][0]) if candidates else None)
        if all(owners) and all(abs(a["bbox"][2]-b["bbox"][0])<=2 for a,b in zip(owners,owners[1:])):
            edges = [owners[0]["bbox"][0]] + [c["bbox"][2] for c in owners]
        else:
            edges = []
    return {"pdf_page": number, "page_height": height, "bbox": list(table.bbox), "column_count": table.col_count,
            "column_edges": edges, "rows": rows}


def contiguous(previous, current):
    if previous is None or not previous["column_edges"] or not current["column_edges"]:
        return False
    if len(previous["column_edges"]) != len(current["column_edges"]) or max(abs(a-b) for a,b in zip(previous["column_edges"],current["column_edges"])) > 2:
        return False
    if previous["pdf_page"] == current["pdf_page"]:
        return 0 <= current["bbox"][1] - previous["bbox"][3] <= 15
    return (current["pdf_page"] == previous["pdf_page"]+1 and previous["bbox"][3] >= previous["page_height"]*.8
            and current["bbox"][1] <= current["page_height"]*.2)


def extract_company(source, source_audit, cfg):
    headings = [h for h in source["heading_candidates"] if h["kind"] == "GEOGRAPHIC_REVENUE_OR_SEGMENT_HEADING"]
    result = {"stock_code":source["stock_code"], "historical_short_name":source["historical_short_name"],
              "report_metadata":source["report_metadata"], "source_pdf_path":source["source_pdf_path"],
              "source_pdf_sha256":source["source_pdf_sha256"], "literal_reader_status":source_audit["status"],
              "original_geographic_anchor_count":len(headings), "tables":[], "unmatched_anchor_pages":[],
              "numeric_values_independently_verified":False, "business_exposure_values_verified":False,
              "agent_signal_enabled":False, "finer_geography_share":None}
    if source["source_pdf_path"] is None:
        result["status"]="SOURCE_MISSING"
        return result
    path = ROOT / source["source_pdf_path"]
    if digest(path) != source["source_pdf_sha256"]:
        raise ValueError("regional revenue source bytes differ")
    if not headings:
        result["status"]="NO_REGIONAL_ANCHOR_UNDER_FIXED_RULE"
        return result
    with fitz.open(path) as doc:
        pages=sorted({i for h in headings for i in range(max(1,h["pdf_page"]-cfg["adjacent_pages"]), min(len(doc),h["pdf_page"]+cfg["adjacent_pages"])+1)})
        result["geometry_pages_inspected"]=pages
        contexts=[]; frames=[]
        for number in pages:
            page=doc[number-1]
            contexts.extend(geometry_rows(page.get_text("words"),number,page.rect.height))
            for table in sorted(page.find_tables().tables,key=lambda t:(t.bbox[1],t.bbox[0])):
                frames.append(frame_for(table,number,page.rect.height))
        previous=None; previous_profile=None; previous_kind=None; previous_unit=None; previous_block=None
        for frame in frames:
            before=[r for r in contexts if (r["pdf_page"]==frame["pdf_page"] and r["bbox"][3]<=frame["bbox"][1]+2
                                          or frame["pdf_page"]-cfg["adjacent_pages"]<=r["pdf_page"]<frame["pdf_page"])]
            kind_evidence=next((r for r in reversed(before) if reader.scope_heading(r["text"])),None)
            kind=reader.scope_heading(kind_evidence["text"]) if kind_evidence else "TABLE_SCOPE_NOT_CONFIRMED"
            carry=contiguous(previous,frame) and kind == previous_kind
            unit=reader.unit_from_context(before)
            if carry and unit["scale"] is None:
                unit=previous_unit
            inherited=previous_profile if carry else None
            profile=reader.role_profile(frame,inherited)
            markers=[i for i,row in enumerate(frame["rows"]) if row and row[0] and reader.plain(row[0]["text"]) in reader.REGION_MARKERS]
            relevant_markers=[i for i in markers if reader.plain(frame["rows"][i][0]["text"]) in {"分地区","分区域"}
                              or (profile is not None and i < len(profile["header_rows"])+1)]
            is_continuation=carry and previous_block is not None and not relevant_markers
            # Nonstandard regional matrices retain their source table without guessing orientation.
            anchor_overlap=any(h["pdf_page"]==frame["pdf_page"] and any(
                frame["bbox"][1]-90 <= box.y1 <= frame["bbox"][3] for box in doc[frame["pdf_page"]-1].search_for(h["text"].strip())) for h in headings)
            if relevant_markers or is_continuation or (profile is None and anchor_overlap):
                if is_continuation:
                    starts=[0 if profile is inherited else len(profile["header_rows"]) if profile else 0]
                else:
                    starts=[i+1 for i in relevant_markers] or [0]
                for start in starts:
                    carry_values=previous_block["parsed"]["rows"] if is_continuation else None
                    carry_total=previous_block["parsed"].get("total") if is_continuation else None
                    parsed=reader.parse_region_block(frame,profile,start,kind,unit,carry_values,carry_total)
                    if is_continuation:
                        previous_block["frames"].append(frame)
                        previous_block["parsed"]=parsed
                    else:
                        previous_block={"frames":[frame],"scope_heading_evidence":kind_evidence,
                                        "region_start_row_index":start,"parsed":parsed,
                                        "source_reader_qc_required":source_audit["status"]!="PASS_LITERAL_PAGE_MAPS"}
                        result["tables"].append(previous_block)
            else:
                previous_block=None
            previous,previous_profile,previous_kind,previous_unit=frame,profile,kind,unit
        matched={f["pdf_page"] for t in result["tables"] for f in t["frames"]}
        result["unmatched_anchor_pages"]=sorted({h["pdf_page"] for h in headings}-matched)
    result["status"]="REGIONAL_CELL_CANDIDATES_REQUIRE_INDEPENDENT_AUDIT" if result["tables"] else "REGIONAL_TABLE_NOT_DETECTED_REVIEW_REQUIRED"
    return result


def compute():
    cfg=json.loads(CONFIG.read_text(encoding="utf-8"))
    if cfg["agent_signal_enabled"] is not False or cfg["source_period_end"] != "2019-06-30" or cfg["adjacent_pages"] != 2:
        raise ValueError("regional revenue protocol changed")
    bindings={**cfg["inputs"],CONFIG.relative_to(ROOT).as_posix():digest(CONFIG)}
    index=json.loads((ROOT/cfg["index_path"]).read_text(encoding="utf-8"))
    audit=json.loads((ROOT/cfg["literal_audit_path"]).read_text(encoding="utf-8"))
    bindings.update(index["inputs"]); bindings.update(index["code_sha256"])
    bindings.update(audit["inputs"])
    bindings["research/data_pipeline/audit_issuer_business_evidence.py"]=audit["audit_code_sha256"]
    code={name:digest(ROOT/name) for name in ("research/data_pipeline/extract_issuer_regional_revenue.py","research/data_pipeline/regional_revenue_tables.py","research/data_pipeline/financial_balance_sheet.py","research/data_pipeline/fetch_issuer_disclosures.py")}
    validate_bindings(bindings)
    if fitz.VersionBind != cfg["pymupdf_version"]:
        raise ValueError("regional revenue PDF reader version differs")
    audited={c["stock_code"]:c for c in audit["companies"]}
    companies=[]
    for source in index["companies"]:
        try:
            company=extract_company(source,audited[source["stock_code"]],cfg)
        except Exception as error:
            company={"stock_code":source["stock_code"],"historical_short_name":source["historical_short_name"],
                     "report_metadata":source["report_metadata"],"source_pdf_path":source["source_pdf_path"],
                     "source_pdf_sha256":source["source_pdf_sha256"],"status":"EXTRACTION_FAILED_REVIEW_REQUIRED",
                     "error":f"{type(error).__name__}: {error}","tables":[],"numeric_values_independently_verified":False,
                     "business_exposure_values_verified":False,"agent_signal_enabled":False,"finer_geography_share":None}
        companies.append(company)
        print(json.dumps({"company":company["stock_code"],"status":company["status"],"tables":len(company["tables"]),"completed":len(companies)},ensure_ascii=False),flush=True)
    validate_bindings({**bindings,**code})
    if len(companies)!=cfg["expected_companies"] or len({c["stock_code"] for c in companies})!=len(companies):
        raise ValueError("regional revenue cohort differs")
    parsed=[t["parsed"] for c in companies for t in c["tables"]]
    return {"pipeline_version":cfg["version"],"companies":companies,"company_status_counts":dict(Counter(c["status"] for c in companies)),
            "table_status_counts":dict(Counter(t["status"] for t in parsed)),"table_count":len(parsed),
            "numeric_regional_current_cells":sum(r["current_revenue"]["status"]=="NUMERIC" for t in parsed for r in t["rows"]),
            "inputs":dict(sorted(bindings.items())),"code_sha256":code,"pymupdf_version":fitz.VersionBind,
            "numeric_values_independently_verified":False,"business_exposure_values_verified":False,"agent_signal_enabled":False,
            "interpretation":cfg["interpretation"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=OUTPUT)
    parser.add_argument("--audit-existing",action="store_true")
    args=parser.parse_args(); output=args.output.resolve()
    if output.parent!=(ROOT/"research_outputs").resolve():
        raise ValueError("regional revenue output must be directly under research_outputs")
    value=compute(); raw=serialize(value)
    if args.audit_existing:
        if output.read_bytes()!=raw:
            raise ValueError("regional revenue candidates differ from frozen reconstruction")
        print("Regional revenue candidate cells rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({k:value[k] for k in ("company_status_counts","table_status_counts","table_count","numeric_regional_current_cells")},ensure_ascii=False))


if __name__ == "__main__":
    main()
