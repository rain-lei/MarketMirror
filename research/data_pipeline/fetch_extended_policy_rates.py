"""Archive a frozen cross-year PBC/CFETS rate source selection."""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/extended_policy_rate_sources_2019_2020.json"
OUTPUT = ROOT / "research_outputs/extended_policy_rates_raw_2019_2020_v1"
HOSTS = {"www.pbc.gov.cn", "www.chinamoney.com.cn"}


def official_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (parsed.scheme == "https" and parsed.hostname in HOSTS
            and parsed.port in {None, 443} and parsed.username is None and parsed.password is None)


def index_records(raw: str, source_url: str, start: str, end: str) -> list[dict]:
    """Locate dated bulletin anchors, keeping the year in each source identity."""
    if "\ufffd" in raw or not official_url(source_url):
        raise ValueError("rate index encoding or publisher is invalid")
    result = []
    for match in re.finditer(r'<a\b[^>]*href=["\x27]([^"\x27]+)["\x27][^>]*>(.*?)</a>', raw, re.S | re.I):
        title = html.unescape(re.sub(r"<[^>]+>", "", match[2])).strip()
        number = re.fullmatch(r"公开市场业务交易公告 \[(\d{4})\]第(\d+)号", title)
        if not number:
            continue
        adjacent = raw[match.end():match.end() + 250]
        next_link = re.search(r"<a\b", adjacent, re.I)
        if next_link:
            adjacent = adjacent[:next_link.start()]
        days = re.findall(r"\d{4}-\d\d-\d\d", adjacent)
        if len(days) != 1 or date.fromisoformat(days[0]).year != int(number[1]):
            raise ValueError("rate index bulletin lacks one adjacent same-year date")
        day = days[0]
        if not start <= day <= end:
            continue
        url = urljoin(source_url, html.unescape(match[1]))
        if not official_url(url) or urlsplit(url).hostname != "www.pbc.gov.cn":
            raise ValueError("rate index bulletin points outside PBC")
        identity = f"pbc_{number[1]}_{number[2]}"
        result.append({"source_id": identity, "kind": "omo", "title": title,
                       "expected_html_title": title, "publication_date": day,
                       "url": url, "archive_name": identity + ".html"})
    return result


def select_sources(settings: dict, config_path: Path) -> tuple[list[dict], dict]:
    selected, bindings = {}, {str(config_path): file_sha256(config_path)}
    for index in settings["discovery_indices"]:
        path = ROOT / index["path"]
        if file_sha256(path) != index["sha256"]:
            raise ValueError("frozen rate index changed")
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved = saved.get("data", saved)
        if saved["metadata"]["statusCode"] != 200 or saved["metadata"]["sourceURL"] != index["source_url"]:
            raise ValueError("frozen rate index identity is invalid")
        bindings[str(path)] = index["sha256"]
        for row in index_records(saved["rawHtml"], index["source_url"], settings["source_start_date"], settings["source_end_date"]):
            if row["source_id"] in selected and selected[row["source_id"]] != row:
                raise ValueError("rate index duplicates disagree")
            selected[row["source_id"]] = row
    for row in settings["lpr_sources"]:
        snapshot = row["discovery_snapshot"]
        path = ROOT / snapshot["path"]
        if file_sha256(path) != snapshot["sha256"]:
            raise ValueError("frozen LPR discovery changed")
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved = saved.get("data", saved)
        metadata = saved["metadata"]
        if (metadata["statusCode"] != 200 or metadata["sourceURL"] != row["url"]
                or metadata["title"] != row["expected_html_title"] or not official_url(row["url"])
                or urlsplit(row["url"]).hostname != "www.chinamoney.com.cn"):
            raise ValueError("LPR discovery does not establish page identity")
        item = {key: value for key, value in row.items() if key != "discovery_snapshot"}
        if item["source_id"] in selected:
            raise ValueError("LPR source identity duplicated")
        selected[item["source_id"]] = item
        bindings[str(path)] = snapshot["sha256"]
    items = sorted(selected.values(), key=lambda row: (row["publication_date"], row["source_id"]))
    if (Counter(row["kind"] for row in items) != settings["expected_source_counts"]
            or len({row["archive_name"] for row in items}) != len(items)
            or any(Path(row["archive_name"]).name != row["archive_name"] for row in items)):
        raise ValueError("rate source coverage or archive names differ from frozen selection")
    return items, bindings


def fetch(item: dict) -> tuple[dict, bytes | None]:
    error = None
    for attempt in range(1, 4):
        try:
            response = requests.get(item["url"], timeout=25)
            response.raise_for_status()
            if not official_url(response.url) or urlsplit(response.url).hostname != urlsplit(item["url"]).hostname:
                raise ValueError("rate response changed publisher")
            raw = response.content
            text = raw.decode("utf-8")
            titles = re.findall(r"<title\b[^>]*>(.*?)</title>", text, re.S | re.I)
            if len(titles) != 1 or html.unescape(titles[0]).strip() != item["expected_html_title"] or "\ufffd" in text:
                raise ValueError("rate response is not the selected original page")
            return {**item, "status": "FETCHED", "attempts": attempt,
                    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "http_status": response.status_code,
                    "final_url": response.url, "content_type": response.headers.get("Content-Type"),
                    "byte_count": len(raw)}, raw
        except (requests.RequestException, UnicodeError, ValueError) as exception:
            error = f"{type(exception).__name__}: {exception}"
    return {**item, "status": "FAILED", "attempts": 3, "error": error}, None


def run(output: Path, config_path: Path = CONFIG, extra_reuse: list[Path] | None = None) -> dict:
    output, config_path = output.resolve(), config_path.resolve()
    if output.parent != (ROOT / "research_outputs").resolve() or output.exists():
        raise ValueError("rate acquisition requires a fresh direct research_outputs directory")
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    if settings["maximum_concurrent_requests"] != 2:
        raise ValueError("rate acquisition uses two bounded request workers")
    items, bindings = select_sources(settings, config_path)
    code = {str(Path(__file__).resolve()): file_sha256(Path(__file__))}
    prior_by_id = {}
    parents = [(ROOT / item["path"], item["sha256"]) for item in settings["reuse_manifests"]]
    parents.extend((path.resolve(), None) for path in extra_reuse or [])
    for manifest_path, expected_hash in parents:
        actual_hash = file_sha256(manifest_path)
        if expected_hash is not None and actual_hash != expected_hash:
            raise ValueError("reused rate manifest changed")
        bindings[str(manifest_path)] = actual_hash
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if expected_hash is None and prior["inputs"].get(str(config_path)) != bindings[str(config_path)]:
            raise ValueError("retry acquisition has a different source selection")
        for old in prior["records"]:
            if old["status"] != "FETCHED":
                continue
            identity = old.get("source_id", Path(old["archive_name"]).stem)
            if identity not in {item["source_id"] for item in items}:
                continue
            source = manifest_path.parent / old["archive_name"]
            if source.parent != manifest_path.parent or file_sha256(source) != old["sha256"]:
                raise ValueError("reused rate original bytes changed")
            if identity in prior_by_id and prior_by_id[identity][0]["sha256"] != old["sha256"]:
                raise ValueError("reused rate source byte versions disagree")
            prior_by_id[identity] = (old, source)
            bindings[str(source)] = old["sha256"]
    records, remaining = {}, []
    output.mkdir()
    for item in items:
        if item["source_id"] not in prior_by_id:
            records[item["source_id"]] = {**item, "status": "PENDING"}
            remaining.append(item)
            continue
        old, source = prior_by_id[item["source_id"]]
        if any(old.get(key) != item[key] for key in ("title", "publication_date", "url", "archive_name")):
            raise ValueError("reused rate source identity changed")
        with (output / item["archive_name"]).open("xb") as handle:
            handle.write(source.read_bytes())
        records[item["source_id"]] = {**old, **item, "reused_from": str(source.relative_to(ROOT)).replace("\\", "/")}

    def save() -> dict:
        manifest = {"pipeline_version": "extended-policy-original-rate-responses-v1", "inputs": dict(sorted(bindings.items())),
                    "code_sha256": code, "records": [records[item["source_id"]] for item in items],
                    "interpretation": settings["interpretation"]}
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return manifest

    save()
    print(f"Rate source selection: {len(items)}; reused {len(items) - len(remaining)}; retrieving {len(remaining)}", flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch, item): item for item in remaining}
        for count, future in enumerate(as_completed(futures), 1):
            record, raw = future.result()
            if raw is not None:
                path = output / record["archive_name"]
                with path.open("xb") as handle:
                    handle.write(raw)
                record["sha256"] = file_sha256(path)
            records[record["source_id"]] = record
            save()
            if count % 10 == 0 or count == len(remaining) or raw is None:
                counts = Counter(row["status"] for row in records.values())
                print(f"Rate sources: {dict(counts)}", flush=True)
    manifest = save()
    if any(file_sha256(Path(name)) != value for name, value in {**bindings, **code}.items()):
        raise ValueError("rate source selection, reused bytes or producer changed during acquisition")
    if any(row["status"] != "FETCHED" for row in manifest["records"]):
        raise ValueError("rate source acquisition contains explicit failures; preserved manifest is not ready")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--reuse-manifest", type=Path, action="append")
    args = parser.parse_args()
    manifest = run(args.output_dir, args.config, args.reuse_manifest)
    print(json.dumps({"sources": len(manifest["records"]), "failures": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
