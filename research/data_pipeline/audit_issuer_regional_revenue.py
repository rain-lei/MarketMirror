"""Independent pdfplumber border-cell, header and arithmetic verification.

No production table parser, role selector or reconciliation function is called.
Unmatched geometry is explicit QC, never a value-matching fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pdfplumber

ROOT=Path(__file__).resolve().parents[2]
CONFIG=ROOT/"research/configs/issuer_half_regional_revenue_audit_2019.json"
OUTPUT=ROOT/"research_outputs/issuer_half_regional_revenue_audit_2019_v1.json"
FACTORS={"元":1,"千元":1000,"万元":10000,"百万元":1000000,"亿元":100000000}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def serialize(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+"\n").encode("utf-8")


def normalized(text):
    return "".join(unicodedata.normalize("NFKC",text or "").split())


def number(text):
    value=normalized(text).replace("−","-")
    if value.startswith("(") and value.endswith(")"):
        value="-"+value[1:-1]
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?",value):
        return None
    return Decimal(value.replace(",",""))


def match_cell(cells,evidence,tolerance=3):
    if evidence is None:
        return None
    wanted=evidence["bbox"]
    candidates=[c for c in cells if max(abs(a-b) for a,b in zip(c["bbox"],wanted))<=tolerance]
    distinct={(tuple(round(x,3) for x in c["bbox"]),normalized(c["text"])):c for c in candidates}
    if len(distinct)!=1:
        raise ValueError("independent border cell is absent or ambiguous")
    actual=next(iter(distinct.values()))
    if normalized(actual["text"])!=normalized(evidence["text"]):
        raise ValueError("independent border-cell text differs")
    return actual


def cells_for(page):
    cells=[]
    for table in page.find_tables():
        for row, physical in zip(table.extract(),table.rows):
            for text,box in zip(row,physical.cells):
                if box is not None:
                    cells.append({"text":text or "","bbox":list(box)})
    return cells


def verify_free_text(page,evidence):
    box=evidence["bbox"]
    words=page.extract_words()
    selected=[w for w in words if box[0]-3 <= (w["x0"]+w["x1"])/2 <= box[2]+3
              and box[1]-3 <= (w["top"]+w["bottom"])/2 <= box[3]+3]
    text="".join(w["text"] for w in sorted(selected,key=lambda w:(round(w["top"]/3),w["x0"])))
    if normalized(text)!=normalized(evidence["text"]):
        raise ValueError("independent unit/scope context differs at source coordinates")
    return text


def verify_roles(profile,border_cells):
    column=profile["current_money_column"]
    evidence=[row[column] for row in profile["header_rows"] if column<len(row) and row[column]]
    actual=[match_cell(border_cells[c["pdf_page"]],c) for c in evidence]
    combined="".join(normalized(c["text"]) for c in actual)
    if profile["basis"]=="explicit_current_operating_revenue_header_in_half_year_management_table":
        if combined not in {"营业收入","营业收入(元)","营业收入(万元)"}:
            raise ValueError("independent current revenue role differs")
    elif profile["basis"]=="explicit_current_period_amount_header_not_comparison":
        if "金额" not in combined or not any(t in combined for t in ("本报告期","本期","2019年上半年","2019年1-6月","2019年1至6月")) or "上年同期" in combined:
            raise ValueError("independent current period amount role differs")
    else:
        raise ValueError("unregistered current column role")
    return len(actual)


def verify_reconciliation(parsed,amounts,total):
    shares=parsed.get("reported_shares")
    if shares is None:
        return 0
    if total is None or total<=0 or any(a is None for a in amounts) or sum(amounts,Decimal(0))!=total:
        raise ValueError("independent current amounts do not close to explicit total")
    labels=[r["region_label_normalized"] for r in parsed["rows"]]
    if len(set(labels))!=len(labels) or any(any(t in label for t in ("母公司","子公司","合计","其中")) for label in labels):
        raise ValueError("hierarchical or duplicate regions cannot be normalized")
    if parsed["issues"] or len(shares)!=len(amounts):
        raise ValueError("unresolved region issues cannot have derived shares")
    for row,amount,share in zip(parsed["rows"],amounts,shares):
        if normalized(share["region_label_as_printed"])!=row["region_label_normalized"] or Decimal(share["share_of_reported_table_total"])!=amount/total:
            raise ValueError("independent regional ratio differs")
        printed=row["printed_current_share"]
        if printed is not None:
            text=normalized(row["printed_current_share_cell"]["text"]).rstrip("%")
            precision=len(text.partition(".")[2])
            tolerance=Decimal("0.5")/(100*(Decimal(10)**precision))
            if abs(Decimal(printed)-amount/total)>tolerance:
                raise ValueError("independent printed share rounding differs")
    return len(shares)


def check_table(table,pages,border_cells):
    parsed=table["parsed"]
    profile=parsed.get("current_column_profile")
    if profile is None:
        return {"status":"CURRENT_COLUMN_UNVERIFIED_AS_EXPECTED","numeric_cells_checked":0,"header_cells_checked":0,"ratios_checked":0}
    header_checked=verify_roles(profile,border_cells)
    unit=parsed["unit"]
    if unit["evidence"]:
        text=verify_free_text(pages[unit["evidence"]["pdf_page"]],unit["evidence"])
        match=re.search(r"单位(?:[:]|为)(?:人民币)?\(?(百万元|千元|万元|亿元|元)",normalized(text))
        if not match or unit["scale"]!=FACTORS[match[1]] or unit["reported_unit"]!=match[1]:
            raise ValueError("independent source monetary unit differs")
    elif unit["scale"] is not None:
        raise ValueError("unit factor without printed unit evidence")
    scope=table["scope_heading_evidence"]
    if scope is not None:
        verify_free_text(pages[scope["pdf_page"]],scope)
    amounts=[];checked=0
    edges=profile["column_edges"]
    left,right=edges[profile["current_money_column"]:profile["current_money_column"]+2]
    for row in parsed["rows"]:
        match_cell(border_cells[row["region_label_cell"]["pdf_page"]],row["region_label_cell"])
        current=row["current_revenue"]
        source=current["source_cell"]
        cell=match_cell(border_cells[source["pdf_page"]],source) if source else None
        amount=number(cell["text"]) if cell else None
        expected=Decimal(current["reported_value"]) if current["reported_value"] is not None else None
        if amount!=expected:
            raise ValueError("independent current regional amount differs or missingness changed")
        if source and (abs(source["bbox"][0]-left)>3 or abs(source["bbox"][2]-right)>3):
            raise ValueError("regional currency cell is not in header-proved current column")
        amounts.append(amount)
        checked+=amount is not None
        converted=Decimal(row["current_revenue_yuan_at_reported_unit"]) if row["current_revenue_yuan_at_reported_unit"] is not None else None
        if converted != (amount*unit["scale"] if amount is not None and unit["scale"] is not None else None):
            raise ValueError("independent monetary unit conversion differs")
        pct=row["printed_current_share_cell"]
        if pct:
            observed=match_cell(border_cells[pct["pdf_page"]],pct)
            expected_pct=row["printed_current_share"]
            actual_pct=number(normalized(observed["text"]).rstrip("%"))/100 if normalized(observed["text"]).endswith("%") and number(normalized(observed["text"]).rstrip("%")) is not None else None
            if actual_pct != (Decimal(expected_pct) if expected_pct is not None else None):
                raise ValueError("independent current printed percentage differs")
    total=None
    if parsed["total"]:
        cell=parsed["total"]["source_cell"]
        total=number(match_cell(border_cells[cell["pdf_page"]],cell)["text"])
        if total!=Decimal(parsed["total"]["reported_value"]):
            raise ValueError("independent reported denominator differs")
        checked+=1
    ratio_count=verify_reconciliation(parsed,amounts,total)
    return {"status":"PASS_SOURCE_CELLS_AND_RECONCILIATION","numeric_cells_checked":checked,"header_cells_checked":header_checked,
            "ratios_checked":ratio_count,"source_reader_qc_required":table["source_reader_qc_required"],
            "table_scope":parsed["table_scope"],"issuer_total_revenue_coverage_candidate":parsed["issuer_total_revenue_coverage_candidate"]}


def compute():
    cfg=json.loads(CONFIG.read_text(encoding="utf-8"))
    for name,expected in cfg["inputs"].items():
        if digest(ROOT/name)!=expected:
            raise ValueError("independent regional audit frozen input changed")
    source=json.loads((ROOT/cfg["candidates_path"]).read_text(encoding="utf-8"))
    bindings={**source["inputs"],**source["code_sha256"],**cfg["inputs"],CONFIG.relative_to(ROOT).as_posix():digest(CONFIG)}
    for name,expected in bindings.items():
        if digest(ROOT/name)!=expected:
            raise ValueError("independent regional audit transitive input changed")
    if pdfplumber.__version__!=cfg["pdfplumber_version"]:
        raise ValueError("independent regional reader version differs")
    companies=[]
    for issuer in source["companies"]:
        audited=[]
        if issuer["tables"]:
            try:
                with pdfplumber.open(ROOT/issuer["source_pdf_path"]) as pdf:
                    needed={c["pdf_page"] for table in issuer["tables"] for frame in table["frames"] for row in frame["rows"] for c in row if c}
                    for table in issuer["tables"]:
                        parsed=table["parsed"]
                        if parsed.get("current_column_profile"):
                            needed.update(c["pdf_page"] for row in parsed["current_column_profile"]["header_rows"] for c in row if c)
                            if parsed["unit"]["evidence"]:needed.add(parsed["unit"]["evidence"]["pdf_page"])
                        if table["scope_heading_evidence"]:needed.add(table["scope_heading_evidence"]["pdf_page"])
                    pages={n:pdf.pages[n-1] for n in needed}
                    borders={n:cells_for(p) for n,p in pages.items()}
                    for i,table in enumerate(issuer["tables"]):
                        try:
                            result=check_table(table,pages,borders)
                        except Exception as error:
                            result={"status":"INDEPENDENT_CELL_REVIEW_REQUIRED","numeric_cells_checked":0,"header_cells_checked":0,"ratios_checked":0,"error":f"{type(error).__name__}: {error}"}
                        audited.append({"table_index":i,**result})
            except Exception as error:
                audited=[{"table_index":i,"status":"INDEPENDENT_READER_FAILED_REVIEW_REQUIRED","numeric_cells_checked":0,"header_cells_checked":0,"ratios_checked":0,"error":f"{type(error).__name__}: {error}"} for i in range(len(issuer["tables"]))]
        companies.append({"stock_code":issuer["stock_code"],"candidate_status":issuer["status"],"tables":audited,
                          "business_exposure_values_verified":False,"agent_signal_enabled":False})
        print(json.dumps({"company":issuer["stock_code"],"tables":len(audited),"completed":len(companies)},ensure_ascii=False),flush=True)
    for name,expected in bindings.items():
        if digest(ROOT/name)!=expected:
            raise ValueError("independent regional sources changed during reading")
    tables=[t for c in companies for t in c["tables"]]
    return {"pipeline_version":cfg["version"],"companies":companies,"table_status_counts":dict(Counter(t["status"] for t in tables)),
            "numeric_cells_checked":sum(t["numeric_cells_checked"] for t in tables),"header_cells_checked":sum(t["header_cells_checked"] for t in tables),
            "ratios_checked":sum(t["ratios_checked"] for t in tables),"inputs":dict(sorted(bindings.items())),"audit_code_sha256":digest(__file__),
            "pdfplumber_version":pdfplumber.__version__,"business_exposure_values_verified":False,"agent_signal_enabled":False,
            "interpretation":"Independent positioned source-cell/header/unit and Decimal arithmetic checks. Reader diagnostics, unproved roles and table scope remain QC; this does not validate Wuhan fractions, causal coefficients, revenue geographic definition or predictive gain."}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=OUTPUT);parser.add_argument("--audit-existing",action="store_true")
    args=parser.parse_args(); output=args.output.resolve()
    if output.parent!=(ROOT/"research_outputs").resolve():raise ValueError("regional audit output must be directly under research_outputs")
    value=compute(); raw=serialize(value)
    if args.audit_existing:
        if output.read_bytes()!=raw:raise ValueError("regional independent audit differs from frozen reconstruction")
        print("Independent regional source-cell audit rebuilt byte-identically.")
    else:
        with output.open("xb") as h:h.write(raw)
    print(json.dumps({k:value[k] for k in ("table_status_counts","numeric_cells_checked","header_cells_checked","ratios_checked")},ensure_ascii=False))


if __name__=="__main__":
    main()
