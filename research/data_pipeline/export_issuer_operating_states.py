"""Export source-dated operating states only after a full-cohort independent audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from . import issuer_operating_states as states

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = ROOT / "research_outputs/issuer_q3_operating_candidates_2019_v2.json"
AUDIT = ROOT / "research_outputs/issuer_q3_operating_audit_2019_v2.json"
BALANCES = ROOT / "research_outputs/issuer_financial_states_2019q3_v1.json"
OUTPUT = ROOT / "research_outputs/issuer_operating_states_2019q3_v1.json"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compute():
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    balances = json.loads(BALANCES.read_text(encoding="utf-8"))
    if audit["status"] != "PASS" or audit["agent_signal_enabled"] is not False:
        raise ValueError("operating state export requires a passing full-cohort independent original-PDF audit")
    bindings = {**audit["inputs"], str(AUDIT): digest(AUDIT), str(BALANCES): digest(BALANCES),
                str(ROOT / "research/data_pipeline/audit_issuer_operating_statements.py"): audit["code_sha256"],
                **balances["inputs"], **balances["code_sha256"]}

    def verify():
        for path, expected in bindings.items():
            if digest(path) != expected:
                raise ValueError("operating-state input/audit/producer changed: " + path)

    verify()
    reviewed = {r["stock_code"]: r for r in audit["reports"]}
    by_code = {c["stock_code"]: c for c in balances["companies"]}
    codes = [c["stock_code"] for c in candidates["companies"]]
    if len(codes) != len(set(codes)) or set(codes) != set(by_code) or set(codes) != set(reviewed) or len(reviewed) != len(audit["reports"]):
        raise ValueError("operating state audit/balance/cohort coverage changed")
    companies = [states.company_state(c, reviewed[c["stock_code"]], by_code[c["stock_code"]]) for c in candidates["companies"]]
    if any(states.state_asof(c, candidates["snapshot_at"]) is None for c in companies):
        raise ValueError("operating state unavailable at declared snapshot")
    checkpoints = [{"asof": moment, "available_companies": sum(states.state_asof(c, moment) is not None for c in companies)}
                   for moment in ("2019-10-01T09:15:00+08:00", "2019-11-01T09:15:00+08:00", "2019-12-06T09:15:00+08:00",
                                  "2019-12-08T00:00:00+08:00", "2020-01-22T23:59:59+08:00")]
    summary = {"companies": len(companies), "financial_institutions_without_generic_ratios": sum(not c["generic_nonfinancial_ratios_applicable"] for c in companies),
               "raw_current_cell_status_counts": dict(Counter(a["status"] for c in companies for part in ("profit", "cashflow", "additional_assets") for a in c[part]["amounts"].values())),
               "ratio_status_counts": {name: dict(Counter(c["ratios"][name]["status"] for c in companies)) for name in states.RATIOS},
               "negative_operating_net_cashflow_companies": sum(Decimal(c["cashflow"]["amounts"]["operating_net_cashflow"]["value_yuan"]) < 0 for c in companies),
               "negative_net_profit_companies": sum(Decimal(c["profit"]["amounts"]["net_profit"]["value_yuan"]) < 0 for c in companies),
               "point_in_time_proxy_checkpoints": checkpoints}
    verify()
    return {"pipeline_version": "independently-reread-source-dated-ytd-operating-states-v1", "companies": companies, "summary": summary,
            "inputs": dict(sorted(bindings.items())), "code_sha256": {str(Path(__file__).resolve()): digest(__file__), str(Path(states.__file__).resolve()): digest(states.__file__)},
            "snapshot_at": candidates["snapshot_at"], "agent_signal_enabled": False,
            "interpretation": "Current YTD profit/cashflow and additional exact assets, with signed ratios for nonfinancial consolidated issuers. Flow-to-ending-stock ratios are not standard/annualized ROA, financial assets are not presumed unrestricted cash. Conservative own-publication-date visibility is retained. No predictive increment or trading coefficient is adopted."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute()
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("operating states differ from frozen audited reconstruction")
        print("Source-dated operating states rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
