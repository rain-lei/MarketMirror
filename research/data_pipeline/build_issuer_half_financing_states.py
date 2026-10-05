"""Join verified June cash/assets/debt and restricted rows from identical PDFs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .half_year_financing_states import build_company, strict_issuer_map
from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_financing_states_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_financing_states_2019_v1.json"


def compute() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    validate_bindings(cfg["inputs"])
    if cfg["agent_signal_enabled"] is not False:
        raise ValueError("half financing state gate differs")
    candidates = json.loads((ROOT / cfg["candidates_path"]).read_text(encoding="utf-8"))
    audit = json.loads((ROOT / cfg["audit_path"]).read_text(encoding="utf-8"))
    restricted = json.loads((ROOT / cfg["restricted_states_path"]).read_text(encoding="utf-8"))
    source = strict_issuer_map(candidates["companies"])
    checked = strict_issuer_map(audit["companies"])
    restrictions = strict_issuer_map(restricted["companies"])
    if set(source) != set(checked) or set(source) != set(restrictions) or len(source) != cfg["expected_companies"]:
        raise ValueError("half financing source cohort association differs")
    bindings = {**candidates["inputs"], **candidates["code_sha256"], **audit["inputs"], **audit["code_sha256"],
                **restricted["inputs"], **restricted["code_sha256"], **cfg["inputs"],
                CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG)}
    code = {f"research/data_pipeline/{name}.py": digest(ROOT / f"research/data_pipeline/{name}.py")
            for name in ("build_issuer_half_financing_states", "half_year_financing_states", "fetch_issuer_disclosures")}
    validate_bindings({**bindings, **code})
    companies = [build_company(source[c], checked[c], restrictions[c]) for c in sorted(source)]
    validate_bindings({**bindings, **code})
    cells = [cell for c in companies for cell in c["balance_fields"].values()]
    rows = [row for c in companies for row in c["restricted_cash_rows"]]
    summary = {"companies": len(companies), "company_status_counts": dict(Counter(c["status"] for c in companies)),
               "verified_current_balance_cells": sum(c["source_number_independently_verified"] for c in cells),
               "verified_cny_current_balance_cells": sum(c["verified_value_yuan"] is not None for c in cells),
               "verified_same_statement_ratios": sum(r["value"] is not None for c in companies for r in c["balance_ratios"].values()),
               "restricted_cash_row_status_counts": dict(Counter(r["status"] for r in rows)),
               "verified_restricted_cash_row_relationships": sum(r["restricted_row_to_money_funds"] is not None for r in rows),
               "companies_with_verified_money_fund_row_fraction": sum(c["max_verified_restricted_money_fund_row_to_money_funds"] is not None for c in companies),
               "companies_with_verified_cash_subcategory_row_fraction": sum(c["max_verified_cash_subcategory_row_to_money_funds"] is not None for c in companies)}
    return {"pipeline_version": cfg["version"], "companies": companies, "summary": summary,
            "inputs": dict(sorted(bindings.items())), "code_sha256": code,
            "agent_signal_enabled": False, "interpretation": cfg["interpretation"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("half financing states output must be directly under research_outputs")
    result = compute()
    raw = serialize(result)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("half financing state reconstruction differs")
        print("Half-year financing states rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
