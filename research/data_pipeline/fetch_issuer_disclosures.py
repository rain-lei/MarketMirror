"""Archive official pre-event report metadata for every frozen cohort company."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import issuer_disclosures

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_disclosures_pre_wuhan_2019_2020.json"
OUTPUT = ROOT / "research_outputs/issuer_disclosure_inventory_raw_2019_2020_v1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serialize(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def atomic_write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(serialize(value))
    os.replace(temporary, path)


def validate_bindings(bindings: dict) -> None:
    if any(digest(ROOT / name) != value for name, value in bindings.items()):
        raise ValueError("issuer inventory frozen source or producer changed")


def retrieve(cohort: dict, settings: dict, output: Path) -> dict:
    company_dir = output / cohort["stock_code"]
    company_dir.mkdir(exist_ok=True)
    runs = [int(path.name[4:]) for path in company_dir.glob("run_*") if path.name[4:].isdigit()]
    run = company_dir / f"run_{max(runs, default=0) + 1}"
    run.mkdir()
    responses = []
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MarketMirrorResearch/0.1)",
                            "Referer": "https://www.cninfo.com.cn/"})

    def request(name: str, url: str, form: dict) -> dict | list:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "www.cninfo.com.cn" or parsed.query or parsed.fragment:
            raise ValueError("issuer metadata endpoint is outside the official publisher")
        for attempt in range(1, settings["maximum_attempts_per_request"] + 1):
            try:
                response = session.post(url, data=form, timeout=(10, 30))
                final = urlsplit(response.url)
                if final.scheme != "https" or final.hostname != parsed.hostname or final.path != parsed.path:
                    raise ValueError("issuer metadata redirect changed publisher or endpoint")
                path = run / f"{name}_attempt{attempt}.json"
                with path.open("xb") as handle:
                    handle.write(response.content)
                record = {"request_kind": name, "request_url": url, "request_form": form,
                          "final_url": response.url, "http_status": response.status_code,
                          "accepted_json_response": False,
                          "content_type": response.headers.get("Content-Type"),
                          "archive_path": path.relative_to(ROOT).as_posix(), "sha256": digest(path),
                          "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}
                responses.append(record)
                response.raise_for_status()
                value = response.json()
                if name == "identity" and not isinstance(value, list) or name != "identity" and not isinstance(value, dict):
                    raise ValueError("issuer metadata response has an unexpected JSON shape")
                record["accepted_json_response"] = True
                return value
            except (requests.RequestException, ValueError) as error:
                if attempt == settings["maximum_attempts_per_request"]:
                    raise ValueError(f"{name} failed after {attempt} attempts: {type(error).__name__}: {error}") from error
                time.sleep(attempt)
        raise AssertionError("unreachable request state")

    try:
        identities = request("identity", settings["identity_url"], {"keyWord": cohort["stock_code"], "maxNum": "10"})
        identity = issuer_disclosures.identity_match(identities, cohort["stock_code"])
        pages, row_count, total = [], 0, None
        for number in range(1, settings["maximum_pages_per_company"] + 1):
            form = {"pageNum": str(number), "pageSize": str(settings["page_size"]),
                    "column": "szse" if cohort["stock_code"][0] in "023" else "sse", "tabName": "fulltext",
                    "stock": cohort["stock_code"] + "," + identity["org_id"],
                    "seDate": settings["query_start_date"] + "~" + settings["query_end_date"],
                    "category": ";".join(settings["query_categories"]), "isHLtitle": "false"}
            page = request(f"announcements_page{number}", settings["announcement_url"], form)
            declared = page.get("totalAnnouncement")
            if type(declared) is not int or declared < 0 or total is not None and declared != total:
                raise ValueError("issuer metadata query count is invalid or changed across pages")
            total = declared
            pages.append(page)
            row_count += len(page.get("announcements") or [])
            if row_count >= total and page.get("hasMore") is False:
                break
        inventory = issuer_disclosures.inventory_company(cohort, identities, pages, settings)
        return {"status": "COMPLETE", "stock_code": cohort["stock_code"], "responses": responses,
                "identity_response_path": next(item["archive_path"] for item in responses if item["request_kind"] == "identity" and item["accepted_json_response"]),
                "page_response_paths": [next(item["archive_path"] for item in responses if item["request_kind"] == f"announcements_page{number}" and item["accepted_json_response"])
                                        for number in range(1, len(pages) + 1)],
                "inventory": inventory, "error": None}
    except Exception as error:
        return {"status": "FAILED", "stock_code": cohort["stock_code"], "responses": responses,
                "inventory": None, "error": f"{type(error).__name__}: {error}"}
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("issuer inventory must use a direct research_outputs directory")
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    bindings = {CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG), **settings["inputs"]}
    code = {Path(__file__).resolve().relative_to(ROOT).as_posix(): digest(Path(__file__)),
            Path(issuer_disclosures.__file__).resolve().relative_to(ROOT).as_posix(): digest(Path(issuer_disclosures.__file__))}
    validate_bindings({**bindings, **code})
    cohort = json.loads((ROOT / settings["source_cohort"]).read_text(encoding="utf-8"))[settings["cohort_field"]]
    if len(cohort) != settings["expected_companies"] or len({row["stock_code"] for row in cohort}) != len(cohort):
        raise ValueError("issuer inventory cohort count or identity differs")
    cohort = sorted(cohort, key=lambda row: row["stock_code"])
    manifest_path, inventory_path = output / "manifest.json", output / "inventory.json"
    if args.audit_existing or args.resume:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["inputs"] != bindings or manifest["code_sha256"] != code:
            raise ValueError("issuer inventory cannot resume with different protocol or producer")
        for row in manifest["companies"].values():
            for response in row.get("responses", []):
                if digest(ROOT / response["archive_path"]) != response["sha256"]:
                    raise ValueError("issuer inventory archived API response changed")
    else:
        if output.exists():
            raise ValueError("issuer inventory exists; preserve it and use --resume or --audit-existing")
        output.mkdir()
        manifest = {"pipeline_version": settings["version"], "inputs": bindings, "code_sha256": code,
                    "companies": {row["stock_code"]: {"stock_code": row["stock_code"], "status": "PENDING"} for row in cohort},
                    "agent_signal_enabled": False}
        atomic_write(manifest_path, manifest)
    if not args.audit_existing:
        pending = [row for row in cohort if manifest["companies"][row["stock_code"]]["status"] != "COMPLETE"]
        with ThreadPoolExecutor(max_workers=settings["maximum_parallel_requests"]) as pool:
            jobs = {pool.submit(retrieve, row, settings, output): row["stock_code"] for row in pending}
            for future in as_completed(jobs):
                result = future.result()
                manifest["companies"][result["stock_code"]] = result
                atomic_write(manifest_path, manifest)
                counts = dict(Counter(item["status"] for item in manifest["companies"].values()))
                print(json.dumps({"company": result["stock_code"], "status": result["status"], "counts": counts,
                                  "error": result["error"]}, ensure_ascii=False), flush=True)
    rebuilt = []
    for row in cohort:
        saved = manifest["companies"][row["stock_code"]]
        if saved["status"] != "COMPLETE":
            continue
        identities = json.loads((ROOT / saved["identity_response_path"]).read_bytes())
        pages = [json.loads((ROOT / name).read_bytes()) for name in saved["page_response_paths"]]
        result = issuer_disclosures.inventory_company(row, identities, pages, settings)
        if result != saved["inventory"]:
            raise ValueError("issuer inventory rebuild differs from raw official responses")
        rebuilt.append(result)
    statuses = dict(Counter(row["status"] for row in manifest["companies"].values()))
    artifact = {"pipeline_version": settings["version"], "snapshot_at": settings["snapshot_at"], "company_status_counts": statuses,
                "companies": rebuilt, "agent_signal_enabled": False, "financial_values_verified": False,
                "limitations": settings["limitations"]}
    validate_bindings({**bindings, **code})
    if args.audit_existing:
        if inventory_path.read_bytes() != serialize(artifact):
            raise ValueError("issuer inventory artifact differs from the frozen raw reconstruction")
        print("Issuer disclosure inventory rebuilt byte-identically from official raw responses.")
    else:
        atomic_write(inventory_path, artifact)
        manifest["artifacts"] = {"inventory.json": {"sha256": digest(inventory_path)}}
        atomic_write(manifest_path, manifest)
    print(json.dumps({"company_status_counts": statuses, "report_status_counts": dict(Counter(report["status"] for company in rebuilt for report in company["reports"].values()))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
