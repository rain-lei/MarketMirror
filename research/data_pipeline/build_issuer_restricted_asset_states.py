"""Adopt checked source asset facts without inventing spendable cash or ratios."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_restricted_asset_states_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_restricted_asset_states_2019_v1.json"


def construct_company(candidate, audit):
    if candidate["stock_code"] != audit["stock_code"]:
        raise ValueError("restricted audit issuer identity differs")
    checked = {t["table_index"]: t for t in audit["tables"]}
    if len(checked) != len(audit["tables"]):
        raise ValueError("duplicate restricted audit table association")
    expected = set(range(len(candidate["tables"])))
    incomplete = set(checked) != expected
    if set(checked) - expected:
        raise ValueError("restricted independent table not in candidate source")
    output = {k: candidate[k] for k in ("stock_code", "historical_short_name", "report_metadata", "source_pdf_path", "source_pdf_sha256", "literal_reader_status")}
    output.update(tables=[], agent_signal_enabled=False, financing_impact_coefficient=None, unrestricted_money_funds=None,
                  cash_to_debt_ratio=None, half_year_restrictions_not_combined_with_third_quarter_balance=True,
                  original_pdf_bytes_certified_unchanged_historically=False)
    for i, table in enumerate(candidate["tables"]):
        independent = checked.get(i, {"status": "INDEPENDENT_TABLE_NOT_COMPLETED"})
        accepted = (not incomplete and independent["status"] == "PASS_SOURCE_CELLS_AND_CALCULATION"
                    and candidate["literal_reader_status"] == "PASS_LITERAL_PAGE_MAPS" and not table["source_reader_qc_required"])
        parsed = table["parsed"]
        cny = parsed["unit"]["currency"] == "CNY_EXPLICIT"
        rows = []
        for source in parsed["rows"]:
            amount = source["ending_value"]["reported_value"]
            numeric = accepted and amount is not None
            rows.append({"label_as_printed": source["label_as_printed"], "component_kind": source["component_kind"],
                         "source_status": source["ending_value"]["status"], "reported_ending_value": amount,
                         "source_cell": source["ending_value"]["source_cell"], "label_cell": source["label_cell"],
                         "reason_as_printed": source["reason_as_printed"], "reason_cells": source["reason_cells"],
                         "source_number_independently_verified": numeric,
                         "verified_ending_value_reported_units": amount if numeric else None,
                         "verified_ending_value_yuan": source["value_at_reported_unit_scale"] if numeric and cny else None,
                         "cash_available_for_trading": None, "stock_return_direction": "unknown"})
        total = parsed["total"]
        output["tables"].append({"table_index": i, "source_pages": [f["pdf_page"] for f in table["frames"]],
                                 "scope": table["scope"], "scope_evidence": table["scope_evidence"], "unit": parsed["unit"],
                                 "independent_status": independent["status"], "source_number_checks_accepted": accepted,
                                 "rows": rows, "reported_total": total, "source_reconciliation": parsed["reconciliation"],
                                 "printed_total_number_independently_verified": accepted and total is not None and total["reported_value"] is not None,
                                 "reported_total_exactly_reconciled": accepted and parsed["reconciliation"]["status"] == "EXACT_SOURCE_TOTAL_MATCH",
                                 "issues": parsed["issues"], "all_company_restrictions_completely_disclosed_verified": False,
                                 "reason_causes_calibrated": False, "agent_signal_enabled": False})
    if candidate["source_pdf_path"] is None:
        status = "SOURCE_MISSING_NOT_ZERO"
    elif candidate["literal_reader_status"] != "PASS_LITERAL_PAGE_MAPS":
        status = "SOURCE_READER_QC_NOT_ADOPTED"
    elif any(r["source_number_independently_verified"] for t in output["tables"] for r in t["rows"]):
        status = "LIMITED_RESTRICTED_ASSET_FACTS_AVAILABLE"
    else:
        status = "RESTRICTED_ASSET_SOURCE_OR_TABLE_REVIEW_REQUIRED"
    output["status"] = status
    output["candidate_status"] = candidate["status"]
    output["independent_table_coverage_incomplete"] = incomplete
    return output


def compute():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    candidates = json.loads((ROOT/cfg["candidates_path"]).read_text(encoding="utf-8"))
    audit = json.loads((ROOT/cfg["audit_path"]).read_text(encoding="utf-8"))
    bindings = {**candidates["inputs"], **candidates["code_sha256"], **audit["inputs"], **audit["code_sha256"], **cfg["inputs"],
                CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG)}
    code = {f"research/data_pipeline/{n}.py": digest(ROOT/f"research/data_pipeline/{n}.py") for n in ("build_issuer_restricted_asset_states", "fetch_issuer_disclosures")}
    validate_bindings(bindings)
    audited = {c["stock_code"]: c for c in audit["companies"]}
    if len(audited) != len(audit["companies"]) or set(audited) != {c["stock_code"] for c in candidates["companies"]}:
        raise ValueError("restricted asset audit cohort association differs")
    if cfg["agent_signal_enabled"] is not False or len(audited) != cfg["expected_companies"]:
        raise ValueError("restricted asset state protocol differs")
    companies = [construct_company(c, audited[c["stock_code"]]) for c in candidates["companies"]]
    validate_bindings({**bindings, **code})
    tables = [t for c in companies for t in c["tables"]]
    rows = [r for t in tables for r in t["rows"]]
    return {"pipeline_version": cfg["version"], "companies": companies, "company_status_counts": dict(Counter(c["status"] for c in companies)),
            "verified_current_asset_cells": sum(r["source_number_independently_verified"] for r in rows),
            "verified_cny_current_asset_cells": sum(r["verified_ending_value_yuan"] is not None for r in rows),
            "verified_money_fund_or_cash_subcategory_cells": sum(r["source_number_independently_verified"] and r["component_kind"] in
                {"RESTRICTED_MONEY_FUNDS_AS_REPORTED", "RESTRICTED_CASH_SUBCATEGORY_AS_REPORTED"} for r in rows),
            "reported_totals_exactly_reconciled": sum(t["reported_total_exactly_reconciled"] for t in tables),
            "inputs": dict(sorted(bindings.items())), "code_sha256": code, "agent_signal_enabled": False, "interpretation": cfg["interpretation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT/"research_outputs").resolve():
        raise ValueError("restricted asset states output must be directly under research_outputs")
    value = compute()
    raw = serialize(value)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("restricted asset states reconstruction differs")
        print("Restricted asset states rebuilt byte-identically.")
    else:
        with output.open("xb") as f:
            f.write(raw)
    print(json.dumps({k: value[k] for k in ("company_status_counts", "verified_current_asset_cells", "verified_cny_current_asset_cells", "verified_money_fund_or_cash_subcategory_cells", "reported_totals_exactly_reconciled")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
