"""Archive earlier OMO, original monthly LPR and documented RRR sources."""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import fetch_pbc_omo_2019
from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_policy_sources_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_policy_raw_2019_v2"
HOSTS = {"www.pbc.gov.cn", "www.chinamoney.com.cn", "www.gdjr.gov.cn", "jinan.pbc.gov.cn"}


def official_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname in HOSTS and parsed.port in {None, 443}
            and parsed.username is None and parsed.password is None)


def select_sources(settings: dict) -> tuple[list[dict], dict]:
    earlier, bindings = fetch_pbc_omo_2019.discover(settings["earlier_omo"])
    items = [{**row, "kind": "omo", "source_id": Path(row["archive_name"]).stem,
              "expected_html_title": row["title"]} for row in earlier]
    bindings[str(CONFIG)] = file_sha256(CONFIG)
    for page in settings["additional_sources"]:
        if not official_url(page["url"]):
            raise ValueError("policy source URL is not a selected official publisher")
        snapshot = page.get("discovery_snapshot")
        if snapshot:
            path = ROOT / snapshot["path"]
            if file_sha256(path) != snapshot["sha256"]:
                raise ValueError("policy discovery snapshot changed")
            saved = json.loads(path.read_text(encoding="utf-8"))
            metadata = saved["metadata"]
            if metadata["statusCode"] != 200 or metadata["sourceURL"] != page["url"] or metadata["title"] != page["expected_html_title"]:
                raise ValueError("policy discovery does not establish selected page identity")
            bindings[str(path)] = snapshot["sha256"]
        item = {key: value for key, value in page.items() if key != "discovery_snapshot"}
        if snapshot:
            # The observed mobile RRR URL serves a different HTML title; the
            # frozen scraper metadata records the canonical desktop URL.
            canonical = metadata["url"]
            if not official_url(canonical) or urlsplit(canonical).hostname != urlsplit(page["url"]).hostname:
                raise ValueError("policy canonical discovery URL changes publisher")
            item["request_url"] = canonical
        items.append(item)
    if len({row["source_id"] for row in items}) != len(items) or len({row["archive_name"] for row in items}) != len(items):
        raise ValueError("policy source identities are duplicated")
    if Counter(row["kind"] for row in items) != settings["expected_source_counts"]:
        raise ValueError("policy source selection differs from frozen coverage")
    return sorted(items, key=lambda row: (row.get("publication_date") or "", row["source_id"])), bindings


def fetch(item: dict) -> tuple[dict, bytes | None]:
    error = None
    for attempt in range(1, 4):
        try:
            response = requests.get(item.get("request_url", item["url"]), timeout=30)
            response.raise_for_status()
            if not official_url(response.url):
                raise ValueError("policy response redirected outside selected official publishers")
            raw = response.content
            if item["kind"] == "rrr_legal_pdf":
                if not raw.startswith(b"%PDF-"):
                    raise ValueError("official legal-document response is not a PDF")
            else:
                decoded = raw.decode("utf-8")
                titles = re.findall(r"<title\b[^>]*>(.*?)</title>", decoded, re.S | re.I)
                if len(titles) != 1 or html.unescape(titles[0]).strip() != item["expected_html_title"] or "\ufffd" in decoded:
                    raise ValueError("policy response is not the frozen selected HTML source")
            return {**item, "status": "FETCHED", "attempts": attempt,
                    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "http_status": response.status_code,
                    "final_url": response.url, "content_type": response.headers.get("Content-Type"), "byte_count": len(raw)}, raw
        except (requests.RequestException, UnicodeError, ValueError) as exception:
            error = f"{type(exception).__name__}: {exception}"
    return {**item, "status": "FAILED", "attempts": 3, "error": error}, None


def run(output: Path, reuse_from: Path | None = None) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or output.exists():
        raise ValueError("policy acquisition requires a fresh direct research_outputs directory")
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    if settings["maximum_concurrent_requests"] != 2:
        raise ValueError("policy acquisition uses two bounded request workers")
    items, bindings = select_sources(settings)
    code = {str(Path(__file__).resolve()): file_sha256(Path(__file__)),
            str(Path(fetch_pbc_omo_2019.__file__).resolve()): file_sha256(Path(fetch_pbc_omo_2019.__file__))}
    reused, remaining = [], items
    if reuse_from is not None:
        parent = reuse_from.resolve()
        if parent.parent != output.parent or parent == output:
            raise ValueError("policy response reuse requires a separate archived acquisition")
        parent_path = parent / "manifest.json"
        prior = json.loads(parent_path.read_text(encoding="utf-8"))
        if prior["inputs"] != bindings:
            raise ValueError("policy reused acquisition has different frozen selection inputs")
        by_id = {row["source_id"]: row for row in prior["records"]}
        if set(by_id) != {row["source_id"] for row in items} or len(by_id) != len(prior["records"]):
            raise ValueError("policy reused acquisition has missing or duplicated identities")
        bindings[str(parent_path)] = file_sha256(parent_path)
        remaining = []
        for item in items:
            old = by_id[item["source_id"]]
            if any(old.get(key) != value for key, value in item.items() if key != "request_url"):
                raise ValueError("policy reused response differs from selected source identity")
            if old["status"] != "FETCHED":
                remaining.append(item)
                continue
            if old.get("request_url", old["url"]) != item.get("request_url", item["url"]):
                raise ValueError("policy reused response was fetched from a different canonical URL")
            path = parent / old["archive_name"]
            if path.parent != parent or file_sha256(path) != old["sha256"]:
                raise ValueError("policy reused original bytes changed")
            bindings[str(path)] = old["sha256"]
            reused.append(({**old, **item, "reused_from": str(path.relative_to(ROOT)).replace("\\", "/")}, path))
    output.mkdir()
    records = []
    for record, path in reused:
        with (output / record["archive_name"]).open("xb") as handle:
            handle.write(path.read_bytes())
        records.append(record)
    if reused:
        print(f"Reused {len(reused)} verified original responses; retrieving {len(remaining)} failed sources", flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch, item) for item in remaining]
        for future in as_completed(futures):
            record, raw = future.result()
            if raw is not None:
                path = output / record["archive_name"]
                if path.parent != output:
                    raise ValueError("policy archive name escapes the output directory")
                with path.open("xb") as handle:
                    handle.write(raw)
                record["sha256"] = file_sha256(path)
            records.append(record)
            if len(records) % 5 == 0 or len(records) == len(items) or raw is None:
                print(f"Policy source responses: {len(records)}/{len(items)}, failures {sum(row['status'] == 'FAILED' for row in records)}", flush=True)
    records.sort(key=lambda row: (row.get("publication_date") or "", row["source_id"]))
    manifest = {"pipeline_version": "pre-wuhan-expanded-policy-original-responses-v1", "inputs": bindings,
                "code_sha256": code, "records": records,
                "interpretation": "Current original official HTTP bytes, not certified unrevised historical snapshots. RRR government republication establishes its own clock, not the first publication by PBC. Legal PDF is corroborating scope evidence, not a publication timestamp. No issuer effect or net liquidity is assigned."}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if any(file_sha256(Path(name)) != value for name, value in {**bindings, **code}.items()):
        raise ValueError("policy source selection or acquisition code changed during retrieval")
    if any(row["status"] != "FETCHED" for row in records):
        raise ValueError("policy collection has explicit failures; inspect the preserved manifest")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--reuse-from", type=Path)
    args = parser.parse_args()
    manifest = run(args.output_dir, args.reuse_from)
    print(json.dumps({"sources": len(manifest["records"]), "by_kind": dict(Counter(row["kind"] for row in manifest["records"])), "failures": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
