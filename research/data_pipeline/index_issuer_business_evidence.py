"""Index all archived half-year pages for original business-exposure review."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import fitz

from . import business_exposure_evidence as evidence
from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_business_evidence_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_business_evidence_2019_v1.json"


def compute():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    validate_bindings(cfg["inputs"])
    manifest = json.loads((ROOT / cfg["raw_manifest_path"]).read_text(encoding="utf-8"))
    bindings = {CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG), **cfg["inputs"], **manifest["inputs"], **manifest["code_sha256"]}
    code = {p.relative_to(ROOT).as_posix(): digest(p) for p in (Path(__file__).resolve(), Path(evidence.__file__).resolve(),
                                                            ROOT / "research/data_pipeline/fetch_issuer_disclosures.py")}
    if len(manifest["reports"]) != cfg["expected_companies"] or manifest["agent_signal_enabled"] is not False:
        raise ValueError("business evidence archive cohort or disabled gate differs")
    companies, total_pages = [], 0
    for stock_code, row in sorted(manifest["reports"].items()):
        original = row.get("original_pdf")
        if row["status"] == "NO_ELIGIBLE_REPORT":
            result = {"stock_code": stock_code, "historical_short_name": row["historical_short_name"],
                      "industry_code": row["industry_code"], "review_queue_status": "SOURCE_MISSING_IN_FROZEN_CATALOG",
                      "report_metadata": None, "source_pdf_sha256": None, "source_pdf_path": None,
                      "heading_candidates": [], "region_literal_contexts": [], "literal_term_pages": None,
                      "business_exposure_values_verified": False, "quantified_exposure": None, "agent_signal_enabled": False}
        elif row["status"] in {"FETCHED", "FETCHED_HEADER_REVIEW_REQUIRED"} and original:
            path = ROOT / original["archive_path"]
            if digest(path) != original["sha256"]:
                raise ValueError("business evidence original PDF bytes changed")
            bindings[original["archive_path"]] = original["sha256"]
            with fitz.open(path) as document:
                texts = [p.get_text() for p in document]
            if len(texts) != original["pdf_pages"]:
                raise ValueError("business evidence original page count changed")
            total_pages += len(texts)
            result = {"stock_code": stock_code, "historical_short_name": row["historical_short_name"],
                      "industry_code": row["industry_code"], "report_metadata": row["report_metadata"],
                      "source_pdf_sha256": original["sha256"], "source_pdf_path": original["archive_path"],
                      "pdf_pages": len(texts), "raw_source_status": row["status"],
                      "page_text_sha256": [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts],
                      **evidence.locate_evidence(texts, cfg["literal_search_terms"])}
            if row["status"] == "FETCHED_HEADER_REVIEW_REQUIRED":
                result["review_queue_status"] = "SOURCE_HEADER_REVIEW_REQUIRED"
        else:
            raise ValueError("business evidence source retrieval is incomplete: " + stock_code)
        companies.append(result)
        print(json.dumps({"company": stock_code, "pages_scanned_so_far": total_pages}, ensure_ascii=False), flush=True)
    validate_bindings({**bindings, **code})
    counts = Counter(h["kind"] for c in companies for h in c["heading_candidates"])
    return {"pipeline_version": cfg["version"], "inputs": dict(sorted(bindings.items())), "code_sha256": code,
            "companies": companies, "companies_count": len(companies), "pdf_pages_scanned": total_pages,
            "heading_counts": dict(counts), "region_literal_context_count": sum(len(c["region_literal_contexts"]) for c in companies),
            "review_status_counts": dict(Counter(c["review_queue_status"] for c in companies)),
            "business_exposure_values_verified": False, "agent_signal_enabled": False,
            "limitations": cfg["limitations"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("business evidence output must be directly under research_outputs")
    result = compute()
    raw = serialize(result)
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("business evidence index differs from frozen reconstruction")
        print("Business evidence index rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({k: result[k] for k in ("companies_count", "pdf_pages_scanned", "heading_counts", "region_literal_context_count", "review_status_counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
