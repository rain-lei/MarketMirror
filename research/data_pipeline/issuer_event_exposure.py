"""Audit pre-event issuer exposure mappings against captured source documents."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "research/configs/issuer_exposure_asset_management_2018_pilot.json"
EXPECTED_CODES = {"000001", "000002", "600519"}
CODE_ALIASES = {"000001": ("平安银行",), "000002": ("万科",), "600519": ("贵州茅台",)}


def validate_catalog(catalog: Any) -> tuple[list[dict], dict[str, dict]]:
    fields = {"catalog_id", "data_kind", "event_id", "as_of_date", "policy_source_sha256", "sources", "issuers"}
    if (not isinstance(catalog, dict) or set(catalog) != fields
            or not isinstance(catalog["catalog_id"], str) or not catalog["catalog_id"].strip()
            or catalog["data_kind"] != "exploratory_pre_event_exposure_mapping"
            or catalog["event_id"] != "asset_management_guidance_2018"):
        raise ValueError("invalid issuer exposure catalog header")
    try:
        as_of = date.fromisoformat(catalog["as_of_date"])
    except (TypeError, ValueError) as error:
        raise ValueError("as-of date must be ISO date") from error
    if re.fullmatch(r"[a-f0-9]{64}", str(catalog["policy_source_sha256"])) is None:
        raise ValueError("invalid policy source hash")

    source_fields = {"source_id", "source_kind", "publisher", "url", "archive_path", "sha256",
                     "available_by", "date_basis", "role"}
    source_kinds = {"policy_source", "issuer_report", "issuer_product_disclosure", "provider_identity_crosswalk"}
    sources: dict[str, dict] = {}
    policy_hash = None
    for source in catalog["sources"] if isinstance(catalog["sources"], list) else ():
        if not isinstance(source, dict) or set(source) != source_fields:
            raise ValueError("source fields differ from issuer exposure contract")
        source_id = source["source_id"]
        if not isinstance(source_id, str) or not source_id or source_id in sources:
            raise ValueError("source IDs must be nonempty and unique")
        if (source["source_kind"] not in source_kinds
                or any(not isinstance(source[key], str) or not source[key].strip()
                       for key in ("publisher", "url", "archive_path"))
                or not isinstance(source["sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", source["sha256"]) is None):
            raise ValueError("invalid source hash")
        parsed_url = urlsplit(source["url"])
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("source URL must be HTTP(S)")
        archive_path = Path(source["archive_path"])
        if archive_path.is_absolute() or ".." in archive_path.parts:
            raise ValueError("source paths must remain project-relative")
        if source["role"] not in {"event_scope", "issuer_business", "issuer_identity"}:
            raise ValueError("invalid source role")
        if source["date_basis"] not in {"document_body", "official_archive_path", "provider_retrieval"}:
            raise ValueError("invalid source date basis")
        try:
            available_by = date.fromisoformat(source["available_by"])
        except (TypeError, ValueError) as error:
            raise ValueError("source availability date must be ISO date") from error
        if source["role"] != "issuer_identity" and available_by > as_of:
            raise ValueError("business/event evidence was not available by event date")
        if source["role"] == "issuer_identity" and source["date_basis"] != "provider_retrieval":
            raise ValueError("retrospective identity crosswalk must declare its retrieval basis")
        if source["source_kind"] == "policy_source":
            if source["role"] != "event_scope" or policy_hash is not None:
                raise ValueError("exactly one policy source is expected")
            policy_hash = source["sha256"]
        sources[source_id] = source
    if (not sources or policy_hash != catalog["policy_source_sha256"]
            or not any(source["role"] == "issuer_identity" for source in sources.values())):
        raise ValueError("policy hash or identity crosswalk is missing")

    if not isinstance(catalog["issuers"], list) or not catalog["issuers"]:
        raise ValueError("issuer records are required")
    issuer_fields = {"issuer_code", "issuer_name", "business_description", "industry_code",
                     "industry_code_status", "direct_scope_match", "rationale", "secondary_channels",
                     "evidence_source_ids", "impact_direction", "impact_magnitude", "agent_signal_enabled"}
    issuers, codes = [], set()
    for issuer in catalog["issuers"]:
        if not isinstance(issuer, dict) or set(issuer) != issuer_fields:
            raise ValueError("issuer fields differ from exposure contract")
        code = issuer["issuer_code"]
        if not isinstance(code, str) or re.fullmatch(r"[0-9]{6}", code) is None or code in codes:
            raise ValueError("issuer codes must be unique six-digit strings")
        codes.add(code)
        if not isinstance(issuer["issuer_name"], str) or not issuer["issuer_name"].strip():
            raise ValueError("issuer name is required")
        if issuer["industry_code"] is not None or issuer["industry_code_status"] != "source_described_no_standard_code":
            raise ValueError("this pilot does not claim a standardized industry taxonomy")
        if issuer["direct_scope_match"] not in {"yes", "no"}:
            raise ValueError("direct scope match must be explicit")
        if (not isinstance(issuer["secondary_channels"], list)
                or any(not isinstance(item, str) or not item.strip() for item in issuer["secondary_channels"])):
            raise ValueError("secondary channels must be described strings")
        if (not isinstance(issuer["evidence_source_ids"], list) or not issuer["evidence_source_ids"]
                or any(source_id not in sources for source_id in issuer["evidence_source_ids"])):
            raise ValueError("issuer refers to an unknown evidence source")
        if (issuer["impact_direction"] != "unknown" or issuer["impact_magnitude"] != "unknown"
                or issuer["agent_signal_enabled"] is not False):
            raise ValueError("exposure mapping cannot invent an economic signal")
        if issuer["direct_scope_match"] == "yes":
            evidence = [sources[source_id] for source_id in issuer["evidence_source_ids"]]
            if (not any(source["role"] == "event_scope" for source in evidence)
                    or not any(source["role"] == "issuer_business" for source in evidence)):
                raise ValueError("direct match requires policy-scope and pre-event issuer evidence")
        issuers.append(issuer)
    if codes != EXPECTED_CODES:
        raise ValueError("pilot must map the three declared 2018 event-study issuers")
    return issuers, sources


def audit_catalog(catalog_path: Path = DEFAULT_CATALOG, root: Path = ROOT) -> dict:
    catalog_path, root = catalog_path.resolve(), root.resolve()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    issuers, sources = validate_catalog(catalog)
    verified = []
    for source in sources.values():
        path = (root / source["archive_path"]).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"source archive is missing or outside project: {source['source_id']}")
        if file_sha256(path) != source["sha256"]:
            raise ValueError(f"source archive hash differs: {source['source_id']}")
        verified.append(source["source_id"])
    identity_source = next(source for source in sources.values() if source["role"] == "issuer_identity")
    identity_path = root / identity_source["archive_path"]
    crosswalk_source = sources.get("bao_code_name_crosswalk")
    manifest_source = sources.get("bao_download_manifest")
    if crosswalk_source is None or manifest_source is None:
        raise ValueError("BaoStock identity file and download manifest are both required")
    manifest_path = root / manifest_source["archive_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stock_basic_queries = [row for row in manifest.get("queries", [])
                           if row.get("function") == "query_stock_basic"]
    if (manifest.get("provider") != "BaoStock" or manifest.get("data_kind") != "observed"
            or manifest.get("retrieved_at", "")[:10] != manifest_source["available_by"]
            or manifest.get("artifacts", {}).get(identity_path.name, {}).get("sha256") != crosswalk_source["sha256"]
            or len(stock_basic_queries) != 1
            or stock_basic_queries[0].get("raw_file") != identity_path.name):
        raise ValueError("identity crosswalk differs from its observed provider manifest")
    found = {}
    with identity_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"code", "code_name", "type"} <= set(reader.fieldnames or []):
            raise ValueError("identity crosswalk lacks code/name columns")
        for row in reader:
            symbol = row["code"]
            if (row["type"] == "1" and (symbol in {f"sz.{code}" for code in EXPECTED_CODES}
                                          or symbol in {f"sh.{code}" for code in EXPECTED_CODES})):
                code = symbol.split(".")[1]
                if code in found:
                    raise ValueError("identity crosswalk duplicates issuer code")
                found[code] = row["code_name"]
    for code, aliases in CODE_ALIASES.items():
        if code not in found or not any(alias in found[code] for alias in aliases):
            raise ValueError(f"identity crosswalk name differs for {code}")
    policy = next(source for source in sources.values() if source["role"] == "event_scope")
    return {"catalog_id": catalog["catalog_id"], "event_id": catalog["event_id"],
            "as_of_date": catalog["as_of_date"], "issuers": len(issuers),
            "direct_scope_matches": sum(row["direct_scope_match"] == "yes" for row in issuers),
            "agent_signals_enabled": sum(row["agent_signal_enabled"] for row in issuers),
            "verified_source_archives": verified, "issuer_identity_crosswalk": found,
            "catalog_sha256": file_sha256(catalog_path), "policy_source_sha256": policy["sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()
    print(json.dumps(audit_catalog(args.catalog), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
