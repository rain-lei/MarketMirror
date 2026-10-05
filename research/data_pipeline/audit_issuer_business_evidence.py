"""Independently read all half-year pages with Poppler and check literal indexes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_half_business_poppler_audit_2019_v3.json"
OUTPUT = ROOT / "research_outputs/issuer_half_business_evidence_audit_2019_v3.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def plain(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


def text_pages(executable, path, reading_mode):
    if reading_mode not in {"raw", "layout"}:
        raise ValueError("independent PDF reading mode is not fixed")
    response = subprocess.run([str(executable), "-" + reading_mode, "-enc", "UTF-8", str(path), "-"],
                              capture_output=True, timeout=120,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    response.check_returncode()
    diagnostics = response.stderr.decode("utf-8", errors="strict").strip()
    pages = response.stdout.decode("utf-8").split("\f")
    if not pages[-1].strip():
        pages.pop()
    return pages, diagnostics


def check_literals(pages, record, terms):
    if len(pages) != record["pdf_pages"]:
        raise ValueError("independent original page count differs")
    normalized = [plain(page) for page in pages]
    rebuilt = {term: [i + 1 for i, page in enumerate(normalized) if plain(term) in page] for term in terms}
    if rebuilt != record["literal_term_pages"]:
        differences = {term: {"index": record["literal_term_pages"].get(term), "Poppler": rebuilt[term]}
                       for term in terms if record["literal_term_pages"].get(term) != rebuilt[term]}
        raise ValueError("independent literal page coverage differs: " + json.dumps(differences, ensure_ascii=False))
    anchor_differences = []
    for row in record["heading_candidates"] + record["region_literal_contexts"]:
        page = row["pdf_page"]
        if not 1 <= page <= len(pages) or row["quantified_exposure"] is not None:
            raise ValueError("business evidence candidate page or unknown quantity differs")
        if plain(row["text"]) not in normalized[page - 1]:
            anchor_differences.append({"pdf_page": page, "line_number": row["line_number"], "kind": row["kind"],
                                       "text": row["text"], "reason": "Exact anchor string differs under independent page reading order; do not adopt as verified context."})
    return rebuilt, anchor_differences


def check_company(record, raw_row, selected_report, cfg, terms):
    code = record["stock_code"]
    if record["business_exposure_values_verified"] is not False or record["quantified_exposure"] is not None or record["agent_signal_enabled"] is not False:
        raise ValueError("business evidence may not silently enable numeric exposure")
    if selected_report is None:
        if (record["review_queue_status"] != "SOURCE_MISSING_IN_FROZEN_CATALOG" or record["source_pdf_path"] is not None
                or record["report_metadata"] is not None or record["heading_candidates"] or record["region_literal_contexts"]
                or record["literal_term_pages"] is not None or raw_row["status"] != "NO_ELIGIBLE_REPORT"):
            raise ValueError("independent business source missingness differs")
        return {"stock_code": code, "status": "SOURCE_MISSING_AS_EXPECTED", "pdf_pages": 0, "literal_term_maps_checked": 0,
                "anchor_strings_checked": 0, "anchor_reading_order_differences": [], "reader_diagnostics": ""}
    original = raw_row["original_pdf"]
    if (record["source_pdf_sha256"] != original["sha256"] or record["source_pdf_path"] != original["archive_path"]
            or raw_row["report_metadata"] != selected_report or record["report_metadata"] != selected_report
            or selected_report["stock_code"] != code or selected_report["report_period_key"] != "2019_half"
            or selected_report["report_end_date"] != "2019-06-30"):
        raise ValueError("independent business source selection/identity differs")
    path = ROOT / record["source_pdf_path"]
    if sha(path) != original["sha256"] or path.stat().st_size != original["bytes"]:
        raise ValueError("independent business original bytes differ")
    pages, diagnostics = text_pages(cfg["pdftotext_path"], path, cfg["reading_mode"])
    if "2019年半年度报告" not in "".join(plain(p) for p in pages[:5]):
        raise ValueError("independent half-year header token is absent")
    rebuilt, differences = check_literals(pages, record, terms)
    status = "READER_DIAGNOSTIC_REVIEW_REQUIRED" if diagnostics else (
        "PASS_LITERAL_PAGE_MAPS" if not differences else "PASS_LITERAL_PAGES_CONTEXT_REVIEW_REQUIRED")
    return {"stock_code": code, "status": status, "reader_diagnostics": diagnostics,
            "pdf_pages": len(pages), "literal_term_maps_checked": len(terms),
            "anchor_strings_checked": len(record["heading_candidates"]) + len(record["region_literal_contexts"]),
            "anchor_reading_order_differences": differences,
            "literal_term_pages": rebuilt, "independent_page_text_sha256": [hashlib.sha256(p.encode("utf-8")).hexdigest() for p in pages]}


def compute():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    bindings = {**cfg["inputs"], CONFIG.relative_to(ROOT).as_posix(): sha(CONFIG)}
    for name, expected in cfg["inputs"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("independent business audit frozen input differs")
    index = json.loads((ROOT / cfg["index_path"]).read_text(encoding="utf-8"))
    raw = json.loads((ROOT / cfg["raw_manifest_path"]).read_text(encoding="utf-8"))
    inventory = json.loads((ROOT / cfg["inventory_path"]).read_text(encoding="utf-8"))
    settings = json.loads((ROOT / cfg["index_config_path"]).read_text(encoding="utf-8"))
    bindings.update(index["inputs"])
    bindings.update(index["code_sha256"])
    for name, expected in bindings.items():
        if sha(ROOT / name) != expected:
            raise ValueError("independent business audit transitive source differs")
    executable = Path(cfg["pdftotext_path"])
    if sha(executable) != cfg["pdftotext_sha256"]:
        raise ValueError("independent Poppler binary changed")
    version = subprocess.run([str(executable), "-v"], capture_output=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    version.check_returncode()
    if cfg["pdftotext_version"] not in (version.stdout + version.stderr).decode("utf-8", errors="strict"):
        raise ValueError("independent Poppler version differs")
    records = {c["stock_code"]: c for c in index["companies"]}
    companies = {c["stock_code"]: c for c in inventory["companies"]}
    if len(records) != len(index["companies"]) or len(companies) != 123 or set(records) != set(companies) or set(records) != set(raw["reports"]):
        raise ValueError("independent business cohort coverage differs")
    results = []
    with ThreadPoolExecutor(max_workers=cfg["maximum_parallel_reads"]) as pool:
        jobs = {pool.submit(check_company, record, raw["reports"][code], companies[code]["reports"]["2019_half"]["selected"],
                            cfg, settings["literal_search_terms"]): code for code, record in records.items()}
        for future in as_completed(jobs):
            try:
                result = future.result()
            except Exception as error:
                result = {"stock_code": jobs[future], "status": "EVIDENCE_REVIEW_REQUIRED", "pdf_pages": 0,
                          "literal_term_maps_checked": 0, "anchor_strings_checked": 0,
                          "anchor_reading_order_differences": [], "reader_diagnostics": "",
                          "error": f"{type(error).__name__}: {error}"}
            results.append(result)
            print(json.dumps({"company": result["stock_code"], "status": result["status"], "completed": len(results)}, ensure_ascii=False), flush=True)
    results.sort(key=lambda row: row["stock_code"])
    total_pages = sum(r["pdf_pages"] for r in results)
    incomplete = sum(r["status"] == "EVIDENCE_REVIEW_REQUIRED" for r in results)
    if (not incomplete and total_pages != index["pdf_pages_scanned"] or index["business_exposure_values_verified"] is not False
            or index["agent_signal_enabled"] is not False):
        raise ValueError("independent business source total or disabled gate differs")
    for name, expected in bindings.items():
        if sha(ROOT / name) != expected:
            raise ValueError("independent business audit source changed during reading")
    differences = sum(len(r["anchor_reading_order_differences"]) for r in results)
    diagnostic_issuers = sum(bool(r["reader_diagnostics"]) for r in results)
    return {"pipeline_version": "independent-Poppler-half-business-literal-page-audit-v3",
            "status": "REVIEW_REQUIRED" if differences or diagnostic_issuers or incomplete else "PASS_LITERAL_PAGE_MAPS",
            "companies": results, "company_status_counts": dict(Counter(r["status"] for r in results)),
            "pdf_pages_checked": total_pages, "literal_term_maps_checked": sum(r["literal_term_maps_checked"] for r in results),
            "anchor_strings_checked": sum(r["anchor_strings_checked"] for r in results), "anchor_reading_order_differences": differences,
            "reader_diagnostic_issuers": diagnostic_issuers, "incomplete_evidence_issuers": incomplete,
            "expected_source_pdf_pages": index["pdf_pages_scanned"],
            "inputs": dict(sorted(bindings.items())), "audit_code_sha256": sha(__file__),
            "pdftotext_path": str(executable), "pdftotext_sha256": cfg["pdftotext_sha256"], "pdftotext_version": cfg["pdftotext_version"],
            "reading_mode": cfg["reading_mode"],
            "business_exposure_values_verified": False, "agent_signal_enabled": False,
            "interpretation": "Independent full-cohort inspection; per-issuer reader diagnostics, anchor differences and failed comparisons remain explicit QC. Only passing literal maps are verified, and no diagnostic issuer is represented as a clean PDF review. This does not verify table numbers, economic relationships, complete revenue coverage, causal exposure or investor behavior."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("independent business audit output must be directly under research_outputs")
    result = compute()
    value = serialize(result)
    if args.audit_existing:
        if output.read_bytes() != value:
            raise ValueError("independent business audit differs from frozen reconstruction")
        print("Independent Poppler business audit rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(value)
    print(json.dumps({k: result[k] for k in ("status", "company_status_counts", "pdf_pages_checked", "literal_term_maps_checked", "anchor_strings_checked", "anchor_reading_order_differences")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
