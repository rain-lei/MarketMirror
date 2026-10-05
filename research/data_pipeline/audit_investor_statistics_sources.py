"""Independently check registered source facts with pdfminer/pdfplumber.

This module deliberately does not import the source producer's parsers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata

import pdfplumber


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "research/configs/investor_statistics_sources_2019_2020_v1.json"
OUTPUT = ROOT / "research_outputs/investor_statistics_independent_audit_20261004_v1.json"


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def numeric_lines(text: str, count: int) -> list[list[str]]:
    """Rebuild the tail columns from a different engine's line placement."""
    result = []
    for line in unicodedata.normalize("NFKC", text).splitlines():
        normal = re.sub(r"\s*\U001001b0\s*", ".", line)
        tokens = re.findall(r"(?<![0-9])[0-9]+(?:\.[0-9]+)?(?![0-9])", normal)
        if len(tokens) >= count:
            result.append([format(Decimal(value), "f") for value in tokens[-count:]])
    return result


def run() -> dict:
    if OUTPUT.exists():
        raise RuntimeError("Preserve the existing independent audit")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for relative, expected in manifest["bindings"].items():
        path = (ROOT / relative).resolve(strict=True)
        if not path.is_relative_to(ROOT) or sha(path) != expected:
            raise ValueError("Source binding mismatch: " + relative)
    visual = json.loads((ROOT / "research/configs/investor_statistics_visual_review_2019_v1.json").read_text(encoding="utf-8"))
    snapshot = json.loads((ROOT / "research_outputs/investor_statistics_source_snapshot_20261004_v1.json").read_text(encoding="utf-8"))
    pdf = ROOT / ".firecrawl/investor_calibration_sse_2020_v1/original.pdf"
    if pdf.read_bytes()[:5] != b"%PDF-":
        raise ValueError("Original file is not a PDF")
    with pdfplumber.open(pdf) as document:
        if len(document.pages) != 619:
            raise ValueError("Unexpected source scope")
        holdings_page = document.pages[575].extract_text()
        industry_page = document.pages[577].extract_text()
        # Different CJK and numeric font baselines split the date sentence at
        # the default tolerance. Use a larger tolerance for this prose page;
        # the independently read table columns keep their default placement.
        note_page = document.pages[7].extract_text(y_tolerance=8)
    normalized_holdings = re.sub(r"\s+", "", unicodedata.normalize("NFKC", holdings_page))
    normalized_industry = re.sub(r"\s+", "", unicodedata.normalize("NFKC", industry_page))
    normalized_notes = re.sub(r"\s+", "", unicodedata.normalize("NFKC", note_page))
    if ("ShareHoldofInvestorsby2019" not in normalized_holdings
            or "持股市值亿元" not in normalized_holdings
            or "持股账户数万户" not in normalized_holdings
            or "HoldDistributionby2019" not in normalized_industry
            or "持股市值单位为亿元" not in normalized_industry
            or "2019年1月1日至2019年12月31日" not in normalized_notes):
        raise ValueError("Independent source identity, units, or statistics clock mismatch")
    actual_holdings = numeric_lines(holdings_page, 4)
    actual_industry = numeric_lines(industry_page, 6)
    if actual_holdings != visual["holdings_table"]["numeric_fields"]:
        raise ValueError("Independent extraction differs from the entire holdings visual transcription")
    if actual_industry != [row[1:] for row in visual["industry_holdings_table"]["numeric_fields"]]:
        raise ValueError("Independent extraction differs from the entire industry visual transcription")
    if actual_holdings != [row["values"] for row in snapshot["sse_holdings"]["rows"]]:
        raise ValueError("Independent holdings values differ from producer snapshot")
    if actual_industry != [row["values"] for row in snapshot["sse_industry_holdings"]["rows"]]:
        raise ValueError("Independent industry values differ from producer snapshot")
    codes = re.findall(r"^([A-Z])\s", unicodedata.normalize("NFKC", industry_page), re.MULTILINE)
    if codes != [row[0] for row in visual["industry_holdings_table"]["numeric_fields"]]:
        raise ValueError("Independent industry codes differ")
    archive = json.loads((ROOT / ".firecrawl/investor_calibration_szse_2020_survey_https_primary_20261004_v2.json").read_text(encoding="utf-8"))
    original = archive["markdown"]
    survey = snapshot["szse_survey"]
    for key, excerpt in survey["source_excerpts"].items():
        raw = original[excerpt["start_in_markdown"]:excerpt["end_in_markdown"]]
        if raw != excerpt["source_text"] or survey["facts"][key] not in raw.replace(",", ""):
            raise ValueError("Survey fact or exact original span mismatch: " + key)
    if survey["facts"]["publisher_date"] != "2021-05-17":
        raise ValueError("Unexpected publisher date")
    clock_checks = 0
    for name, cutoffs in manifest["availability_by_cutoff"].items():
        clock = manifest["source_clocks"][name]
        if clock["first_publication_timestamp"] is not None or clock["original_public_version_verified"] is not False:
            raise ValueError("Unsupported point-in-time certification")
        for cutoff, outcomes in cutoffs.items():
            cutoff_day = cutoff[:10]
            expected_proxy = clock["publisher_date"] is not None and clock["publisher_date"] < cutoff_day
            if (outcomes["strict"]["eligible"] is not False
                    or outcomes["declared_date_proxy"]["eligible"] != expected_proxy
                    or any(outcome["point_in_time_certified"] is not False for outcome in outcomes.values())):
                raise ValueError("Independent future/unknown/date-proxy gate mismatch")
            clock_checks += 2
    for key in ["role_parameters_calibrated", "point_in_time_feed_certified", "cash_and_inventory_scale_identified",
                "agent_type_shares_identified", "transport_2022_numerical_protocol_changed", "model_adapter_enabled"]:
        if manifest[key] is not False:
            raise ValueError("Source observations were promoted to unsupported model claims: " + key)
    frozen = ROOT / "research/configs/temporal_transport_execution_2022_v1.json"
    if sha(frozen) != manifest["transport_2022_protocol_sha256"]:
        raise ValueError("Ongoing numerical protocol was altered")
    original_failure = json.loads((ROOT / "research_outputs/investor_calibration_sse_2020_capture_20261004_v1.json").read_text(encoding="utf-8"))
    if original_failure["status"] != "FAILED_SOURCE_NOT_MODEL_ELIGIBLE":
        raise ValueError("Original failed identity guard was overwritten")
    result = {
        "status": "PASS_INDEPENDENT_PDFMINER_FULL_TABLE_VALUES_SURVEY_SPANS_AND_CLOCKS",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(), "actual_audit_pid": os.getpid(),
        "manifest_sha256": sha(MANIFEST), "audit_code_sha256": sha(Path(__file__)),
        "verified_source_bindings": len(manifest["bindings"]),
        "independent_extractor": "pdfplumber/pdfminer; producer uses pypdf and manual full-page review",
        "pdf_pages": 619, "table_numeric_values_checked": 152,
        "independent_industry_codes_checked": len(codes), "survey_original_spans_checked": len(survey["source_excerpts"]),
        "future_unknown_date_proxy_checks": clock_checks,
        "ongoing_transport_numerical_protocol_unchanged": True,
        "source_publication_times_certified": False, "real_agent_parameters_calibrated": False,
        "goal_complete": False,
    }
    with OUTPUT.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False), flush=True)
