"""Extract the frozen cohort's YTD operating tables from original reports."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import fitz

from .financial_balance_sheet import geometry_rows
from . import financial_operating_statements as reader

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_q3_operating_statements_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_q3_operating_candidates_2019_v2.json"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compute():
    protocol = json.loads(CONFIG.read_text(encoding="utf-8"))
    for kind, labels in (("profit", reader.PROFIT_FIELDS), ("cashflow", reader.CASH_FIELDS), ("additional_assets", reader.ASSET_FIELDS)):
        if protocol["field_labels"][kind] != {k: list(v) for k, v in labels.items()}:
            raise ValueError("operating field definitions differ from frozen protocol")
    if protocol["agent_signal_enabled"] is not False:
        raise ValueError("operating source extraction cannot activate a trading signal")
    balances = json.loads((ROOT / protocol["balance_candidates"]).read_text(encoding="utf-8"))
    audit = json.loads((ROOT / protocol["balance_audit"]).read_text(encoding="utf-8"))
    if audit["status"] != "PASS" or audit["summary"]["companies"] != protocol["expected_companies"]:
        raise ValueError("full-cohort balance source audit is not available")
    bindings = {str(CONFIG): digest(CONFIG), **{str(ROOT / k): v for k, v in protocol["inputs"].items()},
                **balances["inputs"], **balances["code_sha256"]}

    def verify():
        for path, expected in bindings.items():
            if digest(path) != expected:
                raise ValueError("operating source, upstream producer or PDF changed: " + path)

    verify()
    codes = [c["stock_code"] for c in balances["companies"]]
    if len(codes) != len(set(codes)) or len(codes) != protocol["expected_companies"]:
        raise ValueError("operating cohort changed")
    companies = []
    for n, original in enumerate(balances["companies"], 1):
        meta = original["report_metadata"]
        if (original["status"] != "UNIQUE_RECONCILED_CANDIDATE" or
                meta["stock_code"] != original["stock_code"] or meta["report_end_date"] != protocol["report_end_date"] or
                datetime.fromisoformat(meta["available_at_proxy"]) > datetime.fromisoformat(protocol["snapshot_at"])):
            raise ValueError("operating source issuer, report period, balance selection or availability changed")
        balance = original["candidates"][original["selected_candidate_index"]]
        with fitz.open(ROOT / original["source_pdf_path"]) as pdf:
            rows = [r for page_number, page in enumerate(pdf)
                    for r in geometry_rows(page.get_text("words"), page_number + 1, page.rect.height)]
        flows = reader.extract_flows(rows, protocol["report_year"], balance["start_pdf_page"])
        companies.append({k: original[k] for k in ("stock_code", "historical_short_name", "industry_code", "report_metadata",
                                                   "source_pdf_path", "source_pdf_sha256")} |
                         {"balance_selected_candidate_index": original["selected_candidate_index"], "flows": flows,
                          "additional_assets": reader.extract_additional_assets(rows, balance)})
        if n % 30 == 0:
            print(f"Read operating statements {n}/{len(codes)}.", flush=True)
    verify()
    summary = {"companies": len(companies), "flows": {}}
    for kind in ("利润表", "现金流量表"):
        selected = [c["flows"][kind]["candidates"][c["flows"][kind]["selected_candidate_index"]] for c in companies
                    if c["flows"][kind]["selected_candidate_index"] is not None]
        summary["flows"][kind] = {
            "company_status_counts": dict(Counter(c["flows"][kind]["status"] for c in companies)),
            "selected_unit_counts": dict(Counter(c["reported_unit"] for c in selected)),
            "selected_mapping_basis_counts": dict(Counter(c["column_mapping"]["basis"] for c in selected)),
            "all_column_identity_counts": dict(Counter(check["status"] for c in selected for check in c["identity_checks"])),
            "current_cell_status_counts": dict(Counter(f["cells"][c["current_ytd_column_index"]]["status"] for c in selected for f in c["fields"].values()))}
    summary["additional_asset_current_cell_status_counts"] = dict(Counter(
        f["cells"][c["additional_assets"]["current_column_index"]]["status"] for c in companies for f in c["additional_assets"]["fields"].values()))
    return {"pipeline_version": protocol["version"], "companies": companies, "summary": summary,
            "inputs": dict(sorted(bindings.items())), "code_sha256": {str(Path(__file__).resolve()): digest(__file__), str(Path(reader.__file__).resolve()): digest(reader.__file__)},
            "pdf_parser_version": fitz.VersionBind, "snapshot_at": protocol["snapshot_at"],
            "financial_values_independently_audited": False, "agent_signal_enabled": False,
            "interpretation": "Source-dated YTD profit/cash-flow candidates and additional exact balance rows. Source signs, units, consolidation and blank/dash/absent cells are retained. Undeclared comparative dates remain null. No quarter conversion, free-cash claim, future return or trading response is inferred."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("operating candidates differ from frozen original-PDF reconstruction")
        print("Operating candidates rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
