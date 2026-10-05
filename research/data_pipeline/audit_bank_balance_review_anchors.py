"""Re-read reviewed balance totals with an independent PDF text extractor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_q3_bank_balance_review_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_q3_bank_balance_review_audit_2019_v1.json"
MULTIPLIERS = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute() -> dict:
    protocol = json.loads(CONFIG.read_text(encoding="utf-8"))
    bindings = {str(CONFIG): digest(CONFIG), **{str(ROOT / path): value for path, value in protocol["inputs"].items()}}
    for source in protocol["sources"]:
        bindings[str(ROOT / source["source_pdf_path"])] = source["source_pdf_sha256"]

    def verify() -> None:
        if any(digest(Path(name)) != value for name, value in bindings.items()):
            raise ValueError("reviewed bank source, original PDF or manual anchor changed")

    verify()
    if protocol["agent_signal_enabled"] is not False:
        raise ValueError("review anchors cannot enable an economic signal")
    results, rows_read, values_read = [], 0, 0
    for source in protocol["sources"]:
        if datetime.fromisoformat(source["report_metadata"]["available_at_proxy"]) > datetime.fromisoformat(protocol["snapshot_at"]):
            raise ValueError("reviewed bank statement was not available by the snapshot")
        values, evidence = {}, {}
        with pdfplumber.open(ROOT / source["source_pdf_path"]) as document:
            for field, label in protocol["raw_field_labels"].items():
                page = source["field_pages"][field]
                text = document.pages[page - 1].extract_text(layout=False)
                lines = [line for line in text.splitlines() if line.startswith(label + " ")]
                if len(lines) != 1 or "人民币" not in text:
                    raise ValueError("independent bank row label or currency is missing/ambiguous")
                units = re.findall(r"(?:货币|金额)?单位(?:[:：]|为)(?:人民币)?(百万元|千元|万元|亿元|元)", re.sub(r"\s+", "", text))
                if set(units) != {source["reported_unit"]}:
                    raise ValueError("independent bank reported unit differs")
                raw = lines[0][len(label):].split()
                if len(raw) != len(source["columns_reviewed_left_to_right"]):
                    raise ValueError("independent bank value columns differ from visual mapping")
                if any(re.fullmatch(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", token) is None for token in raw):
                    raise ValueError("independent bank total is not an explicit numeric value")
                parsed = [Decimal(token.replace(",", "")) for token in raw]
                if parsed != [Decimal(token) for token in source["reported_values_reviewed"][field]]:
                    raise ValueError("independent PDF values differ from the complete-page visual review")
                values[field] = parsed
                evidence[field] = {"pdf_page": page, "label": label, "independent_extracted_line": lines[0],
                                   "reported_unit": source["reported_unit"]}
                rows_read += 1
                values_read += len(parsed)
        for column, mapping in enumerate(source["columns_reviewed_left_to_right"]):
            amounts = {field: values[field][column] for field in values}
            if amounts["assets"] != amounts["liabilities"] + amounts["equity"]:
                raise ValueError("reviewed scope/period does not satisfy the reported balance equation")
            factor = MULTIPLIERS[source["reported_unit"]]
            results.append({"stock_code": source["stock_code"], **mapping, "reported_unit": source["reported_unit"],
                            "currency": source["currency"], "multiplier_to_yuan": factor,
                            "reported_totals": {field: str(value) for field, value in amounts.items()},
                            "totals_yuan": {field: str(value * factor) for field, value in amounts.items()},
                            "balance_identity_exact": True, "column_mapping_basis": source["review_basis"],
                            "available_at_proxy": source["report_metadata"]["available_at_proxy"], "row_evidence": evidence,
                            "financial_values_verified_in_this_anchor_only": True, "agent_signal_enabled": False})
    verify()
    return {"pipeline_version": "independent-pdfplumber-reviewed-bank-balance-anchor-audit-v1",
            "reports_checked": len(protocol["sources"]), "independent_label_rows_checked": rows_read,
            "reported_totals_checked": values_read, "scope_period_balance_identities_checked": len(results),
            "records": results, "inputs": dict(sorted(bindings.items())), "code_sha256": digest(Path(__file__)),
            "pdfplumber_version": pdfplumber.__version__, "agent_signal_enabled": False,
            "limitations": protocol["limitations"],
            "interpretation": "Two visually reviewed complete bank balance statements only. Independent PDF row extraction, declared units, exact Decimal normalization and balance identities. Scope/period column order is the explicit visual mapping; no automatic full-cohort feature extraction or Agent adoption."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("reviewed bank balance audit differs from frozen result")
        print("Reviewed bank balance anchors independently rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({key: result[key] for key in ("reports_checked", "independent_label_rows_checked", "reported_totals_checked", "scope_period_balance_identities_checked")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
