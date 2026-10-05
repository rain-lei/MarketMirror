"""Re-run independent border audit against corrected source note parsing."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pdfplumber

from .audit_issuer_half_balance_sheets import run_companies, validate_associations
from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_balance_audit_2019_v2.json"
OUTPUT = ROOT / "research_outputs/issuer_half_balance_sheet_audit_2019_v2.json"


def compute():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    validate_bindings(cfg["inputs"])
    if cfg["agent_signal_enabled"] is not False or pdfplumber.__version__ != cfg["pdfplumber_version"]:
        raise ValueError("half independent v2 audit reader or gate differs")
    candidates = json.loads((ROOT / cfg["candidates_path"]).read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / cfg["report_manifest"]).read_text(encoding="utf-8"))
    literal = json.loads((ROOT / cfg["literal_audit_path"]).read_text(encoding="utf-8"))
    literals = {c["stock_code"]: c for c in literal["companies"]}
    if len(literals) != len(literal["companies"]):
        raise ValueError("half independent v2 audit duplicate literal issuer")
    validate_associations(candidates["companies"], manifest, literals, cfg)
    bindings = {**candidates["inputs"], **candidates["code_sha256"], **cfg["inputs"], CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG)}
    code = {f"research/data_pipeline/{name}.py": digest(ROOT / f"research/data_pipeline/{name}.py")
            for name in ("audit_issuer_half_balance_sheets_v2", "audit_issuer_half_balance_sheets", "audit_issuer_balance_sheets", "fetch_issuer_disclosures")}
    validate_bindings({**bindings, **code})
    reports = run_companies(candidates["companies"], cfg)
    validate_bindings({**bindings, **code})
    summary = {"companies": len(reports), "status_counts": dict(Counter(r["status"] for r in reports)),
               "financial_cells_checked": sum(r.get("cells_checked", 0) for r in reports),
               "scope_period_identities_checked": sum(len(r.get("independent_balance_checks", [])) for r in reports),
               "currency_declaration_checks": sum(len(r.get("independent_currency_declarations", [])) for r in reports),
               "universal_note_presentation_checks": sum(len(r.get("independent_note_presentation_declarations", [])) for r in reports)}
    return {"pipeline_version": cfg["version"], "companies": reports, "summary": summary,
            "status": "COMPLETED_WITH_SOURCE_QC" if any(r["status"] != "PASS" for r in reports) else "PASS_INDEPENDENT_SOURCE_CHECKS",
            "inputs": dict(sorted(bindings.items())), "code_sha256": code,
            "pdfplumber_version": pdfplumber.__version__, "agent_signal_enabled": False, "interpretation": cfg["interpretation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("half independent v2 output must be directly under research_outputs")
    value = compute()
    raw = serialize(value)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("half independent v2 reconstruction differs")
        print("Half-year independent balance v2 rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(value["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
