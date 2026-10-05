"""Primary PDF intervals and three-parameter source reconciliation.

Issuer-announced intervals are retrospective documentary evidence. A quote
must independently corroborate each resumption endpoint. Whole-request
failure and pending requests stay unknown, even inside a documented interval.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

from .audit_eastmoney_carrier import read_json, require, FIELDS
from .documented_trade_status import interval_index, normalized, strict_day, document_day
from .provenance import file_sha256
from .temporal_input_contract import quoted

PRESENT = "SOURCE_ROW_PRESENT_QC_PENDING"
ABSENT = "SOURCE_ROW_ABSENT_NOT_CERTIFIED_SUSPENSION"
FAILED = "SOURCE_REQUEST_FAILED"
PENDING = "SOURCE_REQUEST_NOT_TERMINAL_PENDING"
SUSPENDED = "SUSPENDED_FULL_SESSION"


def load_reviewed_pdf_intervals(root, review_paths, calendar, stocks):
    """Recheck complete primary PDF text, identity, clauses and signed date.

    The receipt records the assistant's visual review; substring validation is
    an additional consistency check, not a substitute for that review.
    """
    from pypdf import PdfReader
    root = Path(root).resolve()
    bindings, intervals, documents = {}, [], {}

    def bind(name, digest=None):
        path = (root / name).resolve()
        require(path.is_relative_to(root) and path.is_file(), "Reviewed PDF evidence escapes workspace or is missing")
        actual = file_sha256(path)
        require(digest is None or actual == digest, "Reviewed primary PDF artifact changed")
        bindings[path.relative_to(root).as_posix()] = actual
        return path

    for review_path in review_paths:
        path = bind(review_path)
        review = read_json(path)
        require(review["status"] == "PASS_ASSISTANT_COMPLETE_ISSUER_PDF_REVIEW_SOURCE_INTERVAL_ONLY"
            and review["point_in_time_feed_certified"] is False and review["public_time_of_day_certified"] is False
            and review["whole_request_failure_may_be_reclassified_as_suspension"] is False,
            "Unreviewed evidence or unverified availability certification")
        for name, digest in review["artifacts"].items():
            bind(name, digest)
        pdfs = [root / name for name in review["artifacts"] if name.lower().endswith(".pdf")]
        texts = [root / name for name in review["artifacts"] if name.endswith("extracted.txt")]
        captures = [root / name for name in review["artifacts"] if name.endswith(".json") and "_capture_" in Path(name).name]
        require(len(pdfs) == len(texts) == len(captures) == 1, "Primary review must bind one PDF, extracted text and capture")
        capture = read_json(captures[0])
        spec = capture.get("request", capture)
        code, announcement = review["stock_code"], review["announcement_id"]
        require(code in stocks and spec["stock_code"] == code and spec["announcement_id"] == announcement
            and spec.get("url") == review["source_url"]
            and re.fullmatch(r"https://static\.cninfo\.com\.cn/finalpage/\d{4}-\d{2}-\d{2}/\d+\.PDF", review["source_url"]),
            "Primary review/capture publisher or issuer identity differs")
        reader = PdfReader(pdfs[0])
        require(capture["actual_renderer_exit_code"] == 0 and capture["pages"] == len(reader.pages)
            and review["complete_pages_rendered_and_visually_inspected"] == list(range(1, len(reader.pages) + 1)),
            "Primary PDF pages were not completely rendered and reviewed")
        for i in range(1, len(reader.pages) + 1):
            require(sum(Path(name).name == f"page-{i}.png" for name in review["artifacts"]) == 1,
                "Reviewed PDF page image is missing or duplicated")
        page_texts = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
        text = normalized("\n".join(page_texts))
        expected_archive = "\n".join(f"PAGE {i}\n{page}\n\n" for i, page in enumerate(page_texts, 1))
        require(normalized(expected_archive) == normalized(texts[0].read_text(encoding="utf-8")),
            "Extracted text differs from complete primary PDF and explicit page markers")
        require(re.search(r"(?:证券代码|股票代码)[:：]" + code + r"(?!\d)", text)
            and re.search(r"公告编号[:：]" + re.escape(announcement) + r"(?!\d)", text),
            "Primary PDF stock/announcement header differs")
        signed = re.findall(r"[〇零一二三四五六七八九]{4}年[一二三四五六七八九十]+月[一二三四五六七八九十]+日", text)
        require(any(document_day(token) == review["published_date"] for token in signed),
            "Reviewed issuer signature date absent from complete PDF")
        start, resume = review["suspension_start_inclusive"], review["resumption_date_exclusive"]
        assertions = []
        for label, event, day in (("suspend", "SUSPEND", start), ("resume", "RESUME", resume)):
            clause = normalized(review["reviewed_literal_clauses_normalized_whitespace_only"][label])
            value = strict_day(day)
            require(clause and clause in text and "预计" not in clause and "预期" not in clause
                and re.search(rf"{value.year}年0?{value.month}月0?{value.day}日", clause),
                "Reviewed event literal/date absent or only expected")
            require(re.search(r"开市(?:时)?起(?:开始)?停牌" if label == "suspend" else r"开市(?:时)?起复牌", clause),
                "Reviewed event does not establish an opening boundary")
            assertions.append({"event": event, "date": day, "excerpt": clause})
        identity = f"{code}_{announcement}"
        require(identity not in documents, "Duplicate reviewed announcement")
        documents[identity] = {"stock_code": code, "assertions": assertions,
            "review_path": path.relative_to(root).as_posix(), "source_url": review["source_url"]}
        intervals.append({"interval_id": identity, "stock_code": code, "start_date": start,
            "resume_date": resume, "document_ids": [identity]})
        expected = [day for day in calendar if start <= day < resume]
        require(review["calendar_sessions_documented_suspended"] == expected,
            "Reviewed interval projection differs from registered source calendar")
    interval_index(calendar, stocks, intervals, documents)
    return {"intervals": intervals, "documents": documents, "bindings": bindings}


def reconcile_transport_positions(positions, calendar, stocks, evidence, require_endpoints=True):
    """Attach separate evidence while keeping all raw rows and failures intact."""
    index = interval_index(calendar, stocks, evidence["intervals"], evidence["documents"])
    expected = {(code, fqt, day) for code in stocks for fqt in (0, 1, 2) for day in calendar}
    seen, lookup, rows, counts = set(), {}, [], Counter()
    for source in positions:
        code = source.get("secid", "")[2:]
        key = code, source.get("fqt"), source.get("trade_date")
        require(source.get("kind") == "stock" and source["secid"] == ("1." if code.startswith(("5", "6", "9")) else "0.") + code
            and type(source.get("fqt")) is int and key in expected and key not in seen,
            "Three-parameter source grid identity differs, escapes or duplicates")
        seen.add(key)
        status = source["source_observation_status"]
        require(status in {PRESENT, ABSENT, FAILED, PENDING} and source["model_eligible"] is False
            and source["certified_trade_status"] is None and set(source["provider_fields"]) == set(FIELDS),
            "Raw source status or certification changed")
        if status == PRESENT:
            quoted(source, source["secid"], key[2], key[1], "stock")
        else:
            require(source["raw_line"] is None and all(v is None for v in source["provider_fields"].values()),
                "Missing, failed or pending source value was filled")
        interval = index.get((code, key[2]))
        require(not (interval and status == PRESENT), "Quoted row contradicts documented full-session suspension")
        row = deepcopy(source)
        row.update(documented_trade_status=SUSPENDED if interval and status == ABSENT else None,
            separate_documented_interval_id=interval,
            status_evidence_mode="RETROSPECTIVE_REVIEWED_ISSUER_PDF" if interval else None,
            historical_as_of_availability_certified=False, execution_quote_eligible=False)
        row["reconciliation_status"] = (
            "ABSENT_SOURCE_WITH_DOCUMENTED_SUSPENSION" if interval and status == ABSENT else
            "FAILED_REQUEST_WITH_SEPARATE_DOCUMENTED_INTERVAL" if interval and status == FAILED else
            "PENDING_REQUEST_WITH_SEPARATE_DOCUMENTED_INTERVAL" if interval and status == PENDING else
            "PRESENT_SOURCE_BASIS_QC_PENDING" if status == PRESENT else
            "FAILED_REQUEST_UNKNOWN_TRADE_STATUS" if status == FAILED else
            "PENDING_REQUEST_UNKNOWN_TRADE_STATUS" if status == PENDING else
            "ABSENT_SOURCE_UNKNOWN_TRADE_STATUS")
        counts[row["reconciliation_status"]] += 1
        rows.append(row)
        lookup[key] = row
    require(seen == expected, "Three-parameter source grid omitted company/parameter/session")
    endpoints = []
    for interval in evidence["intervals"]:
        for fqt in (0, 1, 2):
            row = lookup[interval["stock_code"], fqt, interval["resume_date"]]
            confirmed = row["source_observation_status"] == PRESENT and quoted(row, row["secid"], row["trade_date"], fqt, "stock")["f56"] > 0
            if require_endpoints:
                require(confirmed, "Issuer-announced resumption lacks a positive-volume source quote in each parameter")
            endpoints.append({"interval_id": interval["interval_id"], "fqt": fqt,
                "resume_date": interval["resume_date"], "source_status": row["source_observation_status"],
                "positive_volume_quote_corroborates_announced_endpoint": bool(confirmed)})
    return rows, {"status": "SOURCE_INTERVALS_RECONCILED_MODEL_QC_PENDING" if require_endpoints else "PARTIAL_INTERVAL_RECONCILIATION_ENDPOINTS_NOT_CERTIFIED",
        "source_positions": len(rows), "documented_company_sessions": len(index),
        "reconciliation_counts": dict(sorted(counts.items())), "resumption_endpoint_checks": endpoints,
        "all_endpoint_quotes_corroborated": all(item["positive_volume_quote_corroborates_announced_endpoint"] for item in endpoints),
        "source_values_filled": False, "source_failures_reclassified_as_absences": False,
        "point_in_time_feed_certified": False, "economic_total_return_certified": False,
        "model_eligible": False, "new_period_model_effects_evaluated": False}
