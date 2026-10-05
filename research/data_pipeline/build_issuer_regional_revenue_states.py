"""Join independently checked source numbers without releasing market exposure."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG=ROOT/"research/configs/issuer_half_regional_revenue_states_2019.json"
OUTPUT=ROOT/"research_outputs/issuer_half_regional_revenue_states_2019_v1.json"


def component_kind(label):
    if label in {"其他业务收入","其他收入"}:
        return "UNALLOCATED_OTHER_REVENUE_NOT_A_REGION"
    if any(t in label for t in ("母公司","子公司","合计","总部","抵销","其中")):
        return "ENTITY_SCOPE_SUBTOTAL_OR_ELIMINATION"
    return "ISSUER_REGION_LABEL_WITH_UNVERIFIED_GEOGRAPHIC_DEFINITION"


def merge_company(source,audit):
    if source["stock_code"]!=audit["stock_code"] or len(source["tables"])!=len(audit["tables"]):
        raise ValueError("regional source/audit issuer or table association differs")
    checked={r["table_index"]:r for r in audit["tables"]}
    if set(checked)!=set(range(len(source["tables"]))) or len(checked)!=len(audit["tables"]):
        raise ValueError("regional audit table identities differ")
    reader_ok=source["literal_reader_status"]=="PASS_LITERAL_PAGE_MAPS"
    tables=[]
    for i,table in enumerate(source["tables"]):
        parsed=table["parsed"]; independent=checked[i]
        accepted=reader_ok and not table["source_reader_qc_required"] and independent["status"]=="PASS_SOURCE_CELLS_AND_RECONCILIATION"
        unit=parsed.get("unit",{})
        cny=unit.get("currency")=="CNY_EXPLICIT" and unit.get("scale") is not None
        shares=parsed.get("reported_shares") if accepted else None
        ratio_by_label={r["region_label_as_printed"]:r["share_of_reported_table_total"] for r in shares or []}
        rows=[]
        for row in parsed["rows"]:
            value=row["current_revenue"]["reported_value"]
            rows.append({"label_as_printed":row["region_label_as_printed"],"component_kind":component_kind(row["region_label_normalized"]),
                         "source_cell":row["current_revenue"]["source_cell"],"source_label_cell":row["region_label_cell"],
                         "source_status":row["current_revenue"]["status"],"reported_current_revenue":value,
                         "verified_current_revenue_reported_units":value if accepted else None,
                         "verified_current_revenue_yuan":row.get("current_revenue_yuan_at_reported_unit") if accepted and cny else None,
                         "verified_share_of_reported_table_total":ratio_by_label.get(row["region_label_as_printed"]),
                         "source_number_independently_verified":accepted and value is not None,
                         "finer_geography_exposure":None,"stock_return_direction":"unknown"})
        full=accepted and parsed.get("issuer_total_revenue_coverage_candidate",False) and shares is not None
        tables.append({"table_index":i,"source_pages":sorted({f["pdf_page"] for f in table["frames"]}),
                       "table_scope":parsed.get("table_scope","UNKNOWN"),"candidate_status":parsed["status"],
                       "independent_status":independent["status"],"source_number_checks_accepted":accepted,"unit":unit,
                       "reported_total":parsed.get("total"),"rows":rows,"issues":parsed["issues"],
                       "complete_reported_revenue_table_verified":bool(full),
                       "complete_geographic_allocation_verified":False,
                       "unallocated_other_revenue_present":any(r["component_kind"]=="UNALLOCATED_OTHER_REVENUE_NOT_A_REGION" for r in rows),
                       "business_exposure_values_verified":False,"agent_signal_enabled":False})
    if source["status"]=="SOURCE_MISSING":
        status="SOURCE_MISSING_NOT_ZERO"
    elif not reader_ok:
        status="SOURCE_READER_QC_NOT_ADOPTED"
    elif any(r["source_number_independently_verified"] for t in tables for r in t["rows"]):
        status="LIMITED_REPORTED_REVENUE_FACTS_AVAILABLE"
    else:
        status="REVENUE_SCOPE_OR_TABLE_COVERAGE_UNRESOLVED"
    return {"stock_code":source["stock_code"],"historical_short_name":source["historical_short_name"],
            "status":status,"report_metadata":source["report_metadata"],"source_pdf_path":source["source_pdf_path"],
            "source_pdf_sha256":source["source_pdf_sha256"],"literal_reader_status":source["literal_reader_status"],
            "tables":tables,"finer_geography_exposure":None,"business_exposure_values_verified":False,"agent_signal_enabled":False}


def compute():
    cfg=json.loads(CONFIG.read_text(encoding="utf-8"))
    bindings={**cfg["inputs"],CONFIG.relative_to(ROOT).as_posix():digest(CONFIG)}
    validate_bindings(bindings)
    candidates=json.loads((ROOT/cfg["candidates_path"]).read_text(encoding="utf-8"))
    audit=json.loads((ROOT/cfg["audit_path"]).read_text(encoding="utf-8"))
    bindings.update(candidates["inputs"]);bindings.update(candidates["code_sha256"]);bindings.update(audit["inputs"])
    bindings["research/data_pipeline/audit_issuer_regional_revenue.py"]=audit["audit_code_sha256"]
    code={"research/data_pipeline/build_issuer_regional_revenue_states.py":digest(Path(__file__)),
          "research/data_pipeline/fetch_issuer_disclosures.py":digest(ROOT/"research/data_pipeline/fetch_issuer_disclosures.py")}
    checked={c["stock_code"]:c for c in audit["companies"]}
    if len(checked)!=len(audit["companies"]) or set(checked)!={c["stock_code"] for c in candidates["companies"]}:
        raise ValueError("regional states cohort identities differ")
    companies=[merge_company(c,checked[c["stock_code"]]) for c in candidates["companies"]]
    if len(companies)!=cfg["expected_companies"]:
        raise ValueError("regional states cohort coverage differs")
    validate_bindings({**bindings,**code})
    tables=[t for c in companies for t in c["tables"]]
    rows=[r for t in tables for r in t["rows"]]
    return {"pipeline_version":cfg["version"],"companies":companies,"company_status_counts":dict(Counter(c["status"] for c in companies)),
            "verified_current_revenue_cells":sum(r["source_number_independently_verified"] for r in rows),
            "verified_cny_current_revenue_cells":sum(r["verified_current_revenue_yuan"] is not None for r in rows),
            "verified_table_total_ratios":sum(r["verified_share_of_reported_table_total"] is not None for r in rows),
            "complete_reported_revenue_tables_verified":sum(t["complete_reported_revenue_table_verified"] for t in tables),
            "inputs":dict(sorted(bindings.items())),"code_sha256":code,"business_exposure_values_verified":False,"agent_signal_enabled":False,
            "interpretation":cfg["interpretation"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path,default=OUTPUT);parser.add_argument("--audit-existing",action="store_true")
    args=parser.parse_args();output=args.output.resolve()
    if output.parent!=(ROOT/"research_outputs").resolve():raise ValueError("regional state output must be directly under research_outputs")
    value=compute();raw=serialize(value)
    if args.audit_existing:
        if output.read_bytes()!=raw:raise ValueError("regional source states differ from frozen reconstruction")
        print("Regional source states rebuilt byte-identically.")
    else:
        with output.open("xb") as h:h.write(raw)
    print(json.dumps({k:value[k] for k in ("company_status_counts","verified_current_revenue_cells","verified_cny_current_revenue_cells","verified_table_total_ratios","complete_reported_revenue_tables_verified")},ensure_ascii=False))


if __name__=="__main__":
    main()
