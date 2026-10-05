"""Archive every selected cohort Q3 report without asserting financial validity."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import fitz
import requests

from .fetch_issuer_disclosures import ROOT, atomic_write, digest, validate_bindings

CONFIG = ROOT / "research/configs/issuer_q3_report_sources_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_q3_reports_raw_2019_v1"


def official_attachment(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == "static.cninfo.com.cn"
            and parsed.username is None and parsed.password is None and parsed.port in {None, 443}
            and not parsed.query and not parsed.fragment
            and re.fullmatch(r"/finalpage/\d{4}-\d{2}-\d{2}/\d+\.[Pp][Dd][Ff]", parsed.path) is not None)


def container_facts(path: Path, code: str) -> dict:
    if not path.read_bytes()[:16].startswith(b"%PDF-"):
        raise ValueError("original report response is not a PDF")
    with fitz.open(path) as document:
        if document.needs_pass or document.page_count <= 0:
            raise ValueError("original report PDF is empty or password-protected")
        header_text = "\n".join(document[index].get_text() for index in range(min(document.page_count, 5)))
        compact = re.sub(r"\s+", "", header_text)
        return {"pdf_container_valid": True, "pdf_pages": document.page_count,
                "header_report_period_text_found": "2019年第三季度报告" in compact,
                "header_security_code_token_found": code in compact,
                "pdf_parser_version": fitz.VersionBind, "financial_values_verified": False,
                "interpretation": "Header text tokens and PDF container only; no audited financial value or complete issuer identity claim."}


def fetch_report(company: dict, report: dict, settings: dict, output: Path) -> dict:
    code = company["stock_code"]
    company_dir = output / code
    company_dir.mkdir(exist_ok=True)
    runs = [int(path.name[4:]) for path in company_dir.glob("run_*") if path.name[4:].isdigit()]
    run = company_dir / f"run_{max(runs, default=0) + 1}"
    run.mkdir()
    attempts = []
    url = report["attachment_url"]
    try:
        if (not official_attachment(url) or report["eligible"] is not True
                or report["stock_code"] != code or report["report_period_key"] != settings["report_period_key"]
                or datetime.fromisoformat(report["available_at_proxy"]) > datetime.fromisoformat(settings["snapshot_at"])):
            raise ValueError("selected report publisher, identity, period or availability is invalid")
        for number in range(1, settings["maximum_attempts_per_report"] + 1):
            try:
                with requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MarketMirrorResearch/0.1)"},
                                  timeout=(10, 30), stream=True) as response:
                    if (not official_attachment(response.url)
                            or urlsplit(response.url).path.lower() != urlsplit(url).path.lower()):
                        raise ValueError("original report redirect changed publisher or archive format")
                    path = run / f"attempt{number}.pdf"
                    length = 0
                    with path.open("xb") as handle:
                        for chunk in response.iter_content(chunk_size=65536):
                            length += len(chunk)
                            if length > settings["maximum_pdf_bytes"]:
                                raise ValueError("original report exceeds the frozen byte bound")
                            handle.write(chunk)
                    record = {"request_url": url, "final_url": response.url, "http_status": response.status_code,
                              "content_type": response.headers.get("Content-Type"),
                              "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                              "archive_path": path.relative_to(ROOT).as_posix(), "sha256": digest(path), "bytes": length}
                    attempts.append(record)
                    response.raise_for_status()
                    facts = container_facts(path, code)
                    return {"status": "FETCHED", "stock_code": code, "historical_short_name": company["historical_short_name"],
                            "industry_code": company["industry_code"], "report_metadata": report,
                            "attempts": attempts, "original_pdf": {**record, **facts}, "error": None,
                            "financial_values_verified": False, "agent_signal_enabled": False}
            except Exception as error:
                if number == settings["maximum_attempts_per_report"]:
                    raise ValueError(f"report failed after {number} attempts: {type(error).__name__}: {error}") from error
                time.sleep(number)
        raise AssertionError("unreachable report request state")
    except Exception as error:
        return {"status": "FAILED", "stock_code": code, "historical_short_name": company["historical_short_name"],
                "industry_code": company["industry_code"], "report_metadata": report, "attempts": attempts,
                "original_pdf": None, "error": f"{type(error).__name__}: {error}",
                "financial_values_verified": False, "agent_signal_enabled": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("original report archive must use a direct research_outputs directory")
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    inventory_path = ROOT / settings["inventory_path"]
    parent = json.loads((inventory_path.parent / "manifest.json").read_text(encoding="utf-8"))
    bindings = {CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG), **settings["inputs"],
                **parent["inputs"], **parent["code_sha256"]}
    for company in parent["companies"].values():
        if company["status"] != "COMPLETE":
            raise ValueError("original report acquisition requires the complete frozen metadata cohort")
        for item in company["responses"]:
            bindings[item["archive_path"]] = item["sha256"]
    code = {Path(__file__).resolve().relative_to(ROOT).as_posix(): digest(Path(__file__)),
            "research/data_pipeline/fetch_issuer_disclosures.py": digest(ROOT / "research/data_pipeline/fetch_issuer_disclosures.py")}
    validate_bindings({**bindings, **code})
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    selected = [(company, company["reports"][settings["report_period_key"]]["selected"]) for company in inventory["companies"]
                if company["reports"][settings["report_period_key"]]["status"] == "AVAILABLE_METADATA_ONLY"]
    if len(inventory["companies"]) != settings["expected_companies"] or len(selected) != settings["expected_selected_reports"]:
        raise ValueError("original report source selection differs from the frozen cohort")
    path = output / "manifest.json"
    if args.resume or args.audit_existing:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["inputs"] != bindings or manifest["code_sha256"] != code:
            raise ValueError("original report protocol or producer changed before resume")
        for row in manifest["reports"].values():
            for attempt in row.get("attempts", []):
                if digest(ROOT / attempt["archive_path"]) != attempt["sha256"]:
                    raise ValueError("original report response bytes changed")
    else:
        if output.exists():
            raise ValueError("original report archive exists; preserve it and use --resume or --audit-existing")
        output.mkdir()
        manifest = {"pipeline_version": settings["version"], "inputs": bindings, "code_sha256": code,
                    "reports": {company["stock_code"]: {"status": "PENDING", "stock_code": company["stock_code"]} for company, _ in selected},
                    "financial_values_verified": False, "agent_signal_enabled": False}
        atomic_write(path, manifest)
    if not args.audit_existing:
        pending = [(company, report) for company, report in selected if manifest["reports"][company["stock_code"]]["status"] != "FETCHED"]
        with ThreadPoolExecutor(max_workers=settings["maximum_parallel_requests"]) as pool:
            jobs = [pool.submit(fetch_report, company, report, settings, output) for company, report in pending]
            for future in as_completed(jobs):
                result = future.result()
                manifest["reports"][result["stock_code"]] = result
                atomic_write(path, manifest)
                print(json.dumps({"company": result["stock_code"], "status": result["status"],
                                  "counts": dict(Counter(row["status"] for row in manifest["reports"].values())),
                                  "error": result["error"]}, ensure_ascii=False), flush=True)
    for company, report in selected:
        saved = manifest["reports"][company["stock_code"]]
        if saved["status"] != "FETCHED":
            continue
        if saved["report_metadata"] != report:
            raise ValueError("archived report identity differs from frozen metadata")
        actual = container_facts(ROOT / saved["original_pdf"]["archive_path"], company["stock_code"])
        if any(saved["original_pdf"][key] != value for key, value in actual.items()):
            raise ValueError("archived report container facts differ")
    validate_bindings({**bindings, **code})
    counts = dict(Counter(row["status"] for row in manifest["reports"].values()))
    if args.audit_existing:
        print("Original report hashes, frozen selection and PDF container facts rechecked.")
    print(json.dumps({"report_status_counts": counts,
                      "pages": sum(row["original_pdf"]["pdf_pages"] for row in manifest["reports"].values() if row["status"] == "FETCHED"),
                      "bytes": sum(row["original_pdf"]["bytes"] for row in manifest["reports"].values() if row["status"] == "FETCHED")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
