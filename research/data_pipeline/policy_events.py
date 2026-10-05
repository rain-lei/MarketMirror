"""Validate source-backed non-Q&A policy and market-calendar event records."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = ROOT / "research/configs/policy_event_catalog_2018_2020.json"
SCHEMA = ROOT / "research/data_contracts/policy_event.schema.json"
EVENT_CLASSES = {"financial_regulation", "regional_public_health", "market_calendar"}
SOURCE_KINDS = {"official_primary", "official_exchange_notice", "news_report_of_official_notice"}
TIME_STATUSES = {"observed_exact_timestamp", "date_only_conservative", "reported_time_unresolved"}
ANCHOR_RULES = {"source_timestamp_then_exchange_calendar", "publication_date_then_exchange_calendar"}


def _aware_timestamp(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an ISO timestamp") from error
    if parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")
    return parsed


def validate_catalog(catalog: Any) -> list[dict]:
    if (not isinstance(catalog, dict) or set(catalog) != {"catalog_id", "data_kind", "events"}
            or not isinstance(catalog["catalog_id"], str) or not catalog["catalog_id"].strip()
            or catalog["data_kind"] != "source_verified_context"
            or not isinstance(catalog["events"], list) or not catalog["events"]):
        raise ValueError("invalid policy event catalog header")
    seen: set[str] = set()
    accepted = []
    for event in catalog["events"]:
        required = {"event_id", "event_class", "title", "source", "publication", "visibility",
                    "effective_period", "scope", "use_policy"}
        if not isinstance(event, dict) or set(event) != required:
            raise ValueError("event fields differ from policy event contract")
        event_id = event["event_id"]
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            raise ValueError("event identifiers must be nonempty and unique")
        seen.add(event_id)
        if event["event_class"] not in EVENT_CLASSES or not isinstance(event["title"], str) or not event["title"]:
            raise ValueError("invalid event class or title")

        source = event["source"]
        if (not isinstance(source, dict)
                or set(source) != {"source_kind", "publisher", "url", "archive_path", "sha256"}
                or source["source_kind"] not in SOURCE_KINDS
                or any(not isinstance(source[key], str) or not source[key].strip()
                       for key in ("publisher", "url", "archive_path"))
                or not isinstance(source["sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", source["sha256"]) is None):
            raise ValueError("invalid event source provenance")
        parsed_url = urlsplit(source["url"])
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("event source URL must be HTTP(S)")
        archive_path = Path(source["archive_path"])
        if archive_path.is_absolute() or ".." in archive_path.parts:
            raise ValueError("source archive path must remain relative to the project")

        publication = event["publication"]
        if (not isinstance(publication, dict)
                or set(publication) != {"date", "precision", "timestamp"}):
            raise ValueError("invalid publication clock")
        try:
            if not isinstance(publication["date"], str):
                raise ValueError("publication date must be a string")
            published_date = date.fromisoformat(publication["date"])
        except (TypeError, ValueError) as error:
            raise ValueError("publication date must be ISO date") from error
        if publication["precision"] == "exact_timestamp":
            stamp = _aware_timestamp(publication["timestamp"], "publication timestamp")
            if stamp.date() != published_date:
                raise ValueError("publication timestamp and date disagree")
        elif publication["precision"] == "date_only":
            if publication["timestamp"] is not None:
                raise ValueError("date-only publication must not invent a time")
        else:
            raise ValueError("unsupported publication precision")

        visibility = event["visibility"]
        if (not isinstance(visibility, dict)
                or set(visibility) != {"time_status", "anchor_rule", "sensitivity_rules", "note"}
                or visibility["time_status"] not in TIME_STATUSES
                or visibility["anchor_rule"] not in ANCHOR_RULES
                or not isinstance(visibility["sensitivity_rules"], list)
                or any(rule != "effective_time_upper_bound_proxy" for rule in visibility["sensitivity_rules"])
                or not isinstance(visibility["note"], str) or not visibility["note"].strip()):
            raise ValueError("invalid information visibility policy")
        if (publication["precision"] == "exact_timestamp") != (
                visibility["time_status"] == "observed_exact_timestamp"):
            raise ValueError("publication precision and visibility status disagree")
        expected_anchor = ("source_timestamp_then_exchange_calendar"
                           if publication["precision"] == "exact_timestamp"
                           else "publication_date_then_exchange_calendar")
        if visibility["anchor_rule"] != expected_anchor:
            raise ValueError("visibility anchor does not match publication precision")

        effective = event["effective_period"]
        if (not isinstance(effective, dict) or set(effective) != {"from", "through", "note"}
                or not isinstance(effective["note"], str) or not effective["note"].strip()):
            raise ValueError("invalid effective period")
        parsed_period = {}
        for key in ("from", "through"):
            value = effective[key]
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"effective period {key} must be a date or timestamp")
                if "T" in value:
                    parsed_period[key] = _aware_timestamp(value, f"effective period {key}")
                else:
                    parsed_period[key] = date.fromisoformat(value)
        if parsed_period.get("from") is not None and parsed_period.get("through") is not None:
            if type(parsed_period["from"]) is not type(parsed_period["through"]):
                raise ValueError("effective period endpoints must use matching precision")
            if parsed_period["from"] > parsed_period["through"]:
                raise ValueError("effective period is reversed")
        if ("effective_time_upper_bound_proxy" in visibility["sensitivity_rules"]
                and not isinstance(parsed_period.get("from"), datetime)):
            raise ValueError("effective-time sensitivity requires a sourced timestamp")

        scope = event["scope"]
        if (not isinstance(scope, dict)
                or set(scope) != {"jurisdiction", "direct_subjects", "issuer_codes", "industry_codes", "mapping_status", "note"}
                or not isinstance(scope["jurisdiction"], str) or not scope["jurisdiction"].strip()
                or not isinstance(scope["direct_subjects"], list)
                or any(not isinstance(item, str) or not item.strip() for item in scope["direct_subjects"])
                or not isinstance(scope["issuer_codes"], list)
                or any(not isinstance(code, str) or re.fullmatch(r"[0-9]{6}", code) is None
                       for code in scope["issuer_codes"])
                or not isinstance(scope["industry_codes"], list)
                or any(not isinstance(item, str) or not item.strip() for item in scope["industry_codes"])
                or scope["mapping_status"] not in {"unmapped", "partial", "verified"}
                or not isinstance(scope["note"], str) or not scope["note"].strip()):
            raise ValueError("invalid event applicability scope")
        if not isinstance(event["use_policy"], dict) or event["use_policy"] != {
                "mode": "context_only", "agent_signal_enabled": False}:
            raise ValueError("unvalidated external events must remain context-only")
        accepted.append(event)
    return accepted


def audit_sources(catalog_path: Path = DEFAULT_CATALOG, root: Path = ROOT) -> dict:
    catalog_path, root = catalog_path.resolve(), root.resolve()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    events = validate_catalog(catalog)
    sources = []
    for event in events:
        source = event["source"]
        path = (root / source["archive_path"]).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"event source archive is missing or outside project: {event['event_id']}")
        digest = file_sha256(path)
        if digest != source["sha256"]:
            raise ValueError(f"event source hash differs: {event['event_id']}")
        sources.append({"event_id": event["event_id"], "source_kind": source["source_kind"],
                        "archive_sha256": digest})
    return {"catalog_id": catalog["catalog_id"], "events": len(events),
            "context_only_events": sum(row["use_policy"]["agent_signal_enabled"] is False for row in events),
            "source_archives_verified": len(sources), "sources": sources,
            "catalog_sha256": file_sha256(catalog_path), "schema_sha256": file_sha256(SCHEMA)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()
    print(json.dumps(audit_sources(args.catalog), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
