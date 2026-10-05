"""Export independently checked financial states, not economic Agent signals."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from . import issuer_financial_states as states

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "research_outputs/issuer_q3_balance_sheet_candidates_2019_v1.json"
AUDIT = ROOT / "research_outputs/issuer_q3_balance_sheet_audit_2019_v1.json"
OUTPUT = ROOT / "research_outputs/issuer_financial_states_2019q3_v1.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute() -> dict:
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if audit["status"] != "PASS" or audit["agent_signal_enabled"] is not False:
        raise ValueError("all-cohort financial state export requires a passing independent re-read")
    bindings = {**audit["inputs"], str(AUDIT): digest(AUDIT), str(CANDIDATES): digest(CANDIDATES),
                str(ROOT / "research/data_pipeline/audit_issuer_balance_sheets.py"): audit["code_sha256"]}

    def verify():
        if any(digest(Path(path)) != expected for path, expected in bindings.items()):
            raise ValueError("financial-state source, audit or producer changed")

    verify()
    reviewed = {row["stock_code"]: row for row in audit["reports"]}
    companies = [states.company_state(company, reviewed[company["stock_code"]]) for company in candidates["companies"]]
    if len(reviewed) != len(companies) or len({r["stock_code"] for r in companies}) != len(companies):
        raise ValueError("financial-state audit coverage or issuer uniqueness changed")
    if any(states.state_asof(company, candidates["snapshot_at"]) is None for company in companies):
        raise ValueError("financial state is unavailable at its declared snapshot")
    checkpoints = []
    for moment in ("2019-10-01T09:15:00+08:00", "2019-11-01T09:15:00+08:00", "2019-12-06T09:15:00+08:00",
                   "2019-12-08T00:00:00+08:00", "2020-01-22T23:59:59+08:00"):
        eligible = [company for company in companies if states.state_asof(company, moment) is not None]
        checkpoints.append({"asof": moment, "available_companies": len(eligible),
                            "generic_nonfinancial_ratio_companies": sum(r["generic_nonfinancial_ratios_applicable"] for r in eligible),
                            "unavailable_stock_codes": sorted({r["stock_code"] for r in companies} - {r["stock_code"] for r in eligible})})
    summary = {"companies": len(companies), "financial_institutions_excluded_from_generic_ratios": sum(not c["generic_nonfinancial_ratios_applicable"] for c in companies),
               "ratio_status_counts": {name: dict(Counter(company["ratios"][name]["status"] for company in companies)) for name in states.RATIOS},
               "point_in_time_proxy_checkpoints": checkpoints,
               "current_amount_status_counts": dict(Counter(amount["status"] for company in companies for amount in company["amounts"].values()))}
    verify()
    return {"pipeline_version": "independently-reread-source-dated-current-company-financial-states-v1",
            "companies": companies, "summary": summary, "snapshot_at": candidates["snapshot_at"],
            "inputs": dict(sorted(bindings.items())),
            "code_sha256": {str(Path(__file__).resolve()): digest(Path(__file__)), str(Path(states.__file__).resolve()): digest(Path(states.__file__))},
            "agent_signal_enabled": False,
            "interpretation": "Current balances and explicitly named nonfinancial ratios under a conservative publication-date proxy. Four financial institutions retain source-specific raw amounts but no generic nonfinancial ratios. Blanks/dashes stay null, no total debt or free cash is invented, comparison/restated columns are not reused as old snapshots. No economic effect or simulation adoption is claimed."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("financial states differ from frozen independently audited reconstruction")
        print("Source-dated financial states rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
