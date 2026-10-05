"""Archive the full frozen half-year cohort for business-exposure research."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import fitz
import requests

from .fetch_issuer_disclosures import ROOT, atomic_write, digest, validate_bindings
from .fetch_issuer_report_pdfs import official_attachment

CONFIG = ROOT / "research/configs/issuer_half_business_sources_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_reports_raw_2019_v1"
COMPLETE = {"FETCHED", "FETCHED_HEADER_REVIEW_REQUIRED", "NO_ELIGIBLE_REPORT"}


def select_cohort(inventory, settings):
    companies = inventory["companies"]
    codes = [c["stock_code"] for c in companies]
    if len(codes) != settings["expected_companies"] or len(set(codes)) != len(codes):
        raise ValueError("business-source cohort size or identities differ")
    selected, missing = [], []
    for company in companies:
        record = company["reports"][settings["report_period_key"]]
        if record["status"] == "AVAILABLE_METADATA_ONLY":
            report = record["selected"]
            if (report["stock_code"] != company["stock_code"] or report["eligible"] is not True
                    or report["report_period_key"] != settings["report_period_key"]
                    or report["report_end_date"] != settings["report_end_date"]
                    or datetime.fromisoformat(report["available_at_proxy"]) > datetime.fromisoformat(settings["snapshot_at"])
                    or not official_attachment(report["attachment_url"])):
                raise ValueError("business-source report identity, period, publisher or clock differs")
            selected.append((company, report))
        elif record["status"] == "MISSING_IN_CURRENT_CATALOG" and record["selected"] is None:
            missing.append(company)
        else:
            raise ValueError("ambiguous business-source selection must not be silently omitted")
    if len(selected) != settings["expected_selected_reports"]:
        raise ValueError("business-source selected report count differs")
    return selected, missing


def container_facts(path, code, token):
    with Path(path).open("rb") as handle:
        if not handle.read(16).startswith(b"%PDF-"):
            raise ValueError("business-source response is not a PDF")
    with fitz.open(path) as document:
        if document.needs_pass or document.page_count <= 0:
            raise ValueError("business-source PDF is empty or password protected")
        compact = "".join("".join(document[i].get_text().split()) for i in range(min(5, document.page_count)))
        return {"pdf_container_valid": True, "pdf_pages": document.page_count,
                "header_report_period_text_found": token in compact, "header_security_code_token_found": code in compact,
                "pdf_parser_version": fitz.VersionBind, "business_exposure_values_verified": False,
                "interpretation": "Container/header check only; code-token absence is not issuer mismatch. Business scope, region, period and values require independent review."}


def fetch_report(company, report, settings, output):
    code = company["stock_code"]
    company_dir = output / code
    company_dir.mkdir(exist_ok=True)
    runs = [int(p.name[4:]) for p in company_dir.glob("run_*") if p.name[4:].isdigit()]
    run = company_dir / f"run_{max(runs, default=0) + 1}"
    run.mkdir()
    attempts = []
    original = None
    status, error_message = "FAILED", None
    for number in range(1, settings["maximum_attempts_per_report"] + 1):
        record = {"request_url": report["attachment_url"], "final_url": None, "http_status": None,
                  "content_type": None, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                  "archive_path": None, "sha256": None, "bytes": 0, "error": None}
        path = run / f"attempt{number}.pdf"
        try:
            with requests.get(report["attachment_url"],
                              headers={"User-Agent": "Mozilla/5.0 (compatible; MarketMirrorResearch/0.1)"},
                              timeout=(10, 30), stream=True) as response:
                record.update(final_url=response.url, http_status=response.status_code,
                              content_type=response.headers.get("Content-Type"))
                if (not official_attachment(response.url)
                        or urlsplit(response.url).path.lower() != urlsplit(report["attachment_url"]).path.lower()):
                    raise ValueError("business-source redirect changed publisher or archive identity")
                with path.open("xb") as handle:
                    for chunk in response.iter_content(chunk_size=65536):
                        record["bytes"] += len(chunk)
                        if record["bytes"] > settings["maximum_pdf_bytes"]:
                            raise ValueError("business-source PDF exceeds the frozen byte bound")
                        handle.write(chunk)
                response.raise_for_status()
            facts = container_facts(path, code, settings["header_report_token"])
            original = facts
            status = "FETCHED" if facts["header_report_period_text_found"] else "FETCHED_HEADER_REVIEW_REQUIRED"
            error_message = None
        except Exception as error:
            error_message = f"{type(error).__name__}: {error}"
            record["error"] = error_message
        finally:
            if path.is_file():
                record.update(archive_path=path.relative_to(ROOT).as_posix(), sha256=digest(path), bytes=path.stat().st_size)
            attempts.append(record)
        if original is not None:
            original = {**record, **original}
            break
        if number < settings["maximum_attempts_per_report"]:
            time.sleep(number)
    return {"status": status, "stock_code": code, "historical_short_name": company["historical_short_name"],
            "industry_code": company["industry_code"], "report_metadata": report, "attempts": attempts,
            "original_pdf": original, "error": error_message, "business_exposure_values_verified": False,
            "financial_values_verified": False, "agent_signal_enabled": False}


def verify_reports(manifest, selected, missing, settings):
    expected_codes = {c["stock_code"] for c, _ in selected} | {c["stock_code"] for c in missing}
    if set(manifest["reports"]) != expected_codes:
        raise ValueError("business-source archive omits cohort identities")
    for company in missing:
        row = manifest["reports"][company["stock_code"]]
        if row["status"] != "NO_ELIGIBLE_REPORT" or row["original_pdf"] is not None or row["report_metadata"] is not None:
            raise ValueError("missing business-source report was imputed")
    for company, report in selected:
        row = manifest["reports"][company["stock_code"]]
        if row["stock_code"] != company["stock_code"]:
            raise ValueError("business-source archive issuer differs")
        for attempt in row.get("attempts", []):
            if attempt["archive_path"] and (digest(ROOT / attempt["archive_path"]) != attempt["sha256"]
                                             or (ROOT / attempt["archive_path"]).stat().st_size != attempt["bytes"]):
                raise ValueError("business-source archived attempt bytes differ")
        if row["status"] not in {"FETCHED", "FETCHED_HEADER_REVIEW_REQUIRED"}:
            continue
        if row["report_metadata"] != report:
            raise ValueError("business-source archived report differs from fixed selection")
        actual = container_facts(ROOT / row["original_pdf"]["archive_path"], company["stock_code"], settings["header_report_token"])
        if any(row["original_pdf"][k] != value for k, value in actual.items()):
            raise ValueError("business-source container facts differ")
        expected_status = "FETCHED" if actual["header_report_period_text_found"] else "FETCHED_HEADER_REVIEW_REQUIRED"
        if row["status"] != expected_status or row["agent_signal_enabled"] is not False:
            raise ValueError("business-source header QC or signal gate differs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or args.resume and args.audit_existing:
        raise ValueError("business-source archive path or mode is invalid")
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    inventory_path = ROOT / settings["inventory_path"]
    parent = json.loads((inventory_path.parent / "manifest.json").read_text(encoding="utf-8"))
    bindings = {CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG), **settings["inputs"], **parent["inputs"], **parent["code_sha256"]}
    for company in parent["companies"].values():
        for response in company["responses"]:
            bindings[response["archive_path"]] = response["sha256"]
    code = {p.relative_to(ROOT).as_posix(): digest(p) for p in (Path(__file__).resolve(),
            ROOT / "research/data_pipeline/fetch_issuer_disclosures.py", ROOT / "research/data_pipeline/fetch_issuer_report_pdfs.py")}
    validate_bindings({**bindings, **code})
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    selected, missing = select_cohort(inventory, settings)
    path = output / "manifest.json"
    if args.resume or args.audit_existing:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["inputs"] != bindings or manifest["code_sha256"] != code:
            raise ValueError("business-source protocol or producer changed before resume")
        verify_reports(manifest, selected, missing, settings)
    else:
        output.mkdir(exist_ok=False)
        rows = {c["stock_code"]: {"status": "PENDING", "stock_code": c["stock_code"]} for c, _ in selected}
        rows.update({c["stock_code"]: {"status": "NO_ELIGIBLE_REPORT", "stock_code": c["stock_code"],
                    "historical_short_name": c["historical_short_name"], "industry_code": c["industry_code"],
                    "report_metadata": None, "original_pdf": None, "attempts": [],
                    "business_exposure_values_verified": False, "agent_signal_enabled": False,
                    "error": "No eligible half-year report in the frozen official metadata catalog; do not substitute later reports."} for c in missing})
        manifest = {"pipeline_version": settings["version"], "inputs": bindings, "code_sha256": code,
                    "reports": rows, "business_exposure_values_verified": False, "agent_signal_enabled": False}
        atomic_write(path, manifest)
    if not args.audit_existing:
        pending = [(c, r) for c, r in selected if manifest["reports"][c["stock_code"]]["status"] not in COMPLETE]
        with ThreadPoolExecutor(max_workers=settings["maximum_parallel_requests"]) as pool:
            jobs = {pool.submit(fetch_report, c, r, settings, output): (c, r) for c, r in pending}
            for future in as_completed(jobs):
                company, report = jobs[future]
                try:
                    result = future.result()
                except Exception as error:
                    result = {"status": "FAILED", "stock_code": company["stock_code"],
                              "historical_short_name": company["historical_short_name"], "industry_code": company["industry_code"],
                              "report_metadata": report, "attempts": [], "original_pdf": None,
                              "error": f"Worker exception: {type(error).__name__}: {error}",
                              "business_exposure_values_verified": False, "financial_values_verified": False,
                              "agent_signal_enabled": False}
                previous = manifest["reports"][result["stock_code"]]
                result["attempts"] = previous.get("attempts", []) + result["attempts"]
                manifest["reports"][result["stock_code"]] = result
                atomic_write(path, manifest)
                print(json.dumps({"company": result["stock_code"], "status": result["status"],
                                  "counts": dict(Counter(r["status"] for r in manifest["reports"].values())),
                                  "error": result["error"]}, ensure_ascii=False), flush=True)
    verify_reports(manifest, selected, missing, settings)
    validate_bindings({**bindings, **code})
    originals = [r["original_pdf"] for r in manifest["reports"].values() if r.get("original_pdf")]
    print(json.dumps({"status_counts": dict(Counter(r["status"] for r in manifest["reports"].values())),
                      "pdf_pages": sum(r["pdf_pages"] for r in originals), "bytes": sum(r["bytes"] for r in originals)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
