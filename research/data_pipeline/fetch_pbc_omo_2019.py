"""Archive original PBC responses located in frozen official OMO index snapshots."""

from __future__ import annotations

import argparse
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_monetary_operations_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_pbc_omo_raw_2019_v1"


def discover(config: dict) -> tuple[list[dict], dict]:
    records, bindings = {}, {str(CONFIG): file_sha256(CONFIG)}
    for source in config["discovery_indices"]:
        path = ROOT / source["path"]
        if file_sha256(path) != source["sha256"]:
            raise ValueError("frozen PBC discovery index changed")
        bindings[str(path)] = source["sha256"]
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved = saved.get("data", saved)
        raw = saved["rawHtml"]
        if "\ufffd" in raw:
            raise ValueError("discovery index contains corrupted characters")
        for match in re.finditer(r'<a\b[^>]*href=["\x27]([^"\x27]+)["\x27][^>]*>(.*?)</a>', raw, re.S | re.I):
            title = html.unescape(re.sub(r"<[^>]+>", "", match[2])).strip()
            number = re.fullmatch(r"公开市场业务交易公告 \[2019\]第(\d+)号", title)
            if not number:
                continue
            dates = re.findall(r"2019-\d\d-\d\d", raw[match.end():match.end() + 250])
            if not dates:
                raise ValueError("PBC index lacks an adjacent historical publication date")
            day = dates[0]
            if not config["archive_start_date"] <= day <= config["archive_end_date"]:
                continue
            url = urljoin(source["source_url"], html.unescape(match[1]))
            if urlsplit(url).hostname != "www.pbc.gov.cn":
                raise ValueError("PBC index points outside the official publisher")
            item = {"title": title, "publication_date": day, "url": url, "archive_name": f"pbc_2019_{number[1]}.html"}
            if title in records and records[title] != item:
                raise ValueError("PBC index duplicates disagree")
            records[title] = item
    if not records:
        raise ValueError("PBC discovery has no matching bulletins")
    return sorted(records.values(), key=lambda row: (row["publication_date"], row["title"])), bindings


def fetch(item: dict) -> tuple[dict, bytes | None]:
    error = None
    for attempt in range(1, 4):
        try:
            response = requests.get(item["url"], timeout=30)
            response.raise_for_status()
            raw = response.content
            text = raw.decode("utf-8")
            titles = re.findall(r"<title>(.*?)</title>", text, re.S | re.I)
            if len(titles) != 1 or html.unescape(titles[0]).strip() != item["title"]:
                raise ValueError("HTTP response is not the selected original bulletin")
            return {**item, "status": "FETCHED", "attempts": attempt,
                    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "http_status": response.status_code,
                    "final_url": response.url, "content_type": response.headers.get("Content-Type"), "byte_count": len(raw)}, raw
        except (requests.RequestException, UnicodeError, ValueError) as exception:
            error = f"{type(exception).__name__}: {exception}"
    return {**item, "status": "FAILED", "attempts": 3, "error": error}, None


def run(output: Path) -> dict:
    output = output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or output.exists():
        raise ValueError("PBC raw collection requires a fresh direct research_outputs directory")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["maximum_concurrent_requests"] != 2:
        raise ValueError("PBC collection uses exactly two bounded request workers")
    items, bindings = discover(config)
    output.mkdir()
    records = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch, item) for item in items]
        for future in as_completed(futures):
            record, raw = future.result()
            if raw is not None:
                path = output / record["archive_name"]
                with path.open("xb") as handle:
                    handle.write(raw)
                record["sha256"] = file_sha256(path)
            records.append(record)
            if len(records) % 10 == 0 or len(records) == len(items) or raw is None:
                print(f"PBC raw responses: {len(records)}/{len(items)}, failures {sum(row['status'] == 'FAILED' for row in records)}", flush=True)
    records.sort(key=lambda row: (row["publication_date"], row["title"]))
    manifest = {"pipeline_version": "pre-wuhan-pbc-original-http-archive-v1", "inputs": bindings,
                "code_sha256": {str(Path(__file__).resolve()): file_sha256(Path(__file__))}, "records": records,
                "interpretation": "Original HTTP response bytes retrieved now from official dated pages; not certified historical unrevised snapshots. Failures remain explicit; no record is replaced with a synthetic success."}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if any(file_sha256(Path(name)) != value for name, value in bindings.items()):
        raise ValueError("PBC collection input changed during acquisition")
    if any(row["status"] != "FETCHED" for row in records):
        raise ValueError("PBC collection has failed sources; inspect the preserved manifest")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    manifest = run(args.output_dir)
    print(json.dumps({"bulletins": len(manifest["records"]), "failures": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
