"""Join reviewed issuer suspension evidence without inventing missing quotes.

This is retrospective source reconciliation. It does not certify point-in-time
availability, corporate actions, return conventions, or model eligibility.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

VERSION = "issuer-documented-suspension-reconciliation-v1"
SOURCE_STATUSES = {
    "SOURCE_ROW_PRESENT_QC_PENDING",
    "SOURCE_ROW_ABSENT_NOT_CERTIFIED_SUSPENSION",
    "SOURCE_REQUEST_FAILED",
}


def require(value, message):
    if not value:
        raise ValueError(message)


def strict_day(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value), "invalid evidence date")
    return date.fromisoformat(value)


def normalized(text):
    """Only ignore layout whitespace, emphasis markers, and Unicode width."""
    require(isinstance(text, str), "evidence text must be a string")
    return re.sub(r"[\s*]", "", unicodedata.normalize("NFKC", text))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate evidence JSON key")
        result[key] = value
    return result


def document_day(text):
    value = normalized(text)
    matched = re.fullmatch(r"([0-9〇零一二三四五六七八九]{4})年([0-9一二三四五六七八九十]+)月([0-9一二三四五六七八九十]+)日", value)
    require(matched is not None, "signature date must be a complete literal date")
    digits = dict(zip("〇零一二三四五六七八九", [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))
    def ordinal(token):
        if token.isascii() and token.isdigit():
            return int(token)
        if "十" in token:
            parts = token.split("十")
            require(len(parts) == 2 and all(len(p) <= 1 for p in parts), "invalid signature ordinal")
            return (digits[parts[0]] if parts[0] else 1) * 10 + (digits[parts[1]] if parts[1] else 0)
        require(len(token) == 1 and token in digits, "invalid signature ordinal")
        return digits[token]
    year = matched[1]
    require(year.isascii() or all(c in digits for c in year), "mixed signature year encoding")
    year_value = int(year) if year.isascii() else int("".join(str(digits[c]) for c in year))
    return date(year_value, ordinal(matched[2]), ordinal(matched[3])).isoformat()


def read_evidence(root, documents):
    """Verify literal assertions already reviewed in their complete documents.

    The caller supplies the reviewed event date and exact excerpt. This does not
    replace semantic review with automatic substring matching. An expected
    duration or an expected resumption cannot support a confirmed interval.
    """
    root = Path(root).resolve()
    verified = {}
    for doc in documents:
        required = {"document_id", "stock_code", "path", "sha256", "url", "document_date", "document_date_excerpt", "assertions"}
        require(isinstance(doc, dict) and set(doc) == required, "evidence document contract differs")
        code, identity = doc["stock_code"], doc["document_id"]
        require(isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code), "evidence company identity invalid")
        require(isinstance(identity, str) and identity and identity not in verified, "duplicate or empty evidence document identity")
        strict_day(doc["document_date"])
        location = Path(doc["path"])
        require(not location.is_absolute() and ".." not in location.parts, "evidence source path escapes root")
        path = (root / location).resolve()
        require(path.is_relative_to(root), "resolved evidence source path escapes root")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == doc["sha256"], "evidence document changed")
        carrier = json.loads(raw, object_pairs_hook=unique_object)
        body = carrier.get("data", carrier)
        require(isinstance(body, dict) and isinstance(body.get("markdown"), str), "evidence carrier has no document text")
        url = urlsplit(doc["url"])
        require(url.scheme in {"http", "https"} and url.netloc and not url.username, "evidence source URL invalid")
        metadata = body.get("metadata", {})
        require(metadata.get("sourceURL", metadata.get("url")) == doc["url"], "evidence source URL differs from archived carrier")
        text = normalized(body["markdown"])
        require(normalized(doc["document_date_excerpt"]) in text
                and document_day(doc["document_date_excerpt"]) == doc["document_date"], "document signature date differs from source")
        require(re.search(r"(?:证券代码|股票代码)[:：]" + code + r"(?![0-9])", text), "issuer stock code absent from document")
        assertions = doc["assertions"]
        require(isinstance(assertions, list) and assertions, "document has no reviewed assertions")
        for assertion in assertions:
            require(isinstance(assertion, dict) and set(assertion) == {"event", "date", "excerpt"}, "assertion contract differs")
            day = strict_day(assertion["date"])
            excerpt = normalized(assertion["excerpt"])
            require(0 < len(excerpt) <= 2000 and excerpt in text, "reviewed exact assertion absent from source")
            require("预计" not in excerpt and "预期" not in excerpt, "expected event is not confirmed evidence")
            dated = rf"{day.year}年0?{day.month}月0?{day.day}日"
            require(re.search(dated, excerpt), "asserted full event date absent from excerpt")
            event = assertion["event"]
            require(event in {"SUSPEND", "CONTINUE_SUSPENSION", "RESUME"}, "unknown reviewed stock event")
            event_phrase = r"开市起(?:开始)?停牌" if event == "SUSPEND" else r"继续停牌" if event == "CONTINUE_SUSPENSION" else r"(?:开市起|将于[\s\S]*?)复牌"
            require(re.search(event_phrase, excerpt), "reviewed stock event phrase absent from excerpt")
            require("股票" in excerpt or event == "CONTINUE_SUSPENSION", "assertion must identify the company stock")
        verified[identity] = deepcopy(doc)
    return verified


def interval_index(calendar, stock_codes, intervals, documents):
    """Use start-inclusive / resumption-exclusive reviewed suspension intervals."""
    require(isinstance(calendar, list) and calendar and calendar == sorted(set(calendar)), "source calendar duplicated or unordered")
    for day in calendar:
        strict_day(day)
    require(isinstance(stock_codes, list) and stock_codes and len(stock_codes) == len(set(stock_codes)), "company cohort duplicated or empty")
    require(all(isinstance(code, str) and re.fullmatch(r"[0-9]{6}", code) for code in stock_codes), "invalid company cohort")
    index, identities = {}, set()
    for interval in intervals:
        require(isinstance(interval, dict) and set(interval) == {"interval_id", "stock_code", "start_date", "resume_date", "document_ids"}, "interval contract differs")
        identity, code = interval["interval_id"], interval["stock_code"]
        require(isinstance(identity, str) and identity and identity not in identities, "duplicate or empty interval identity")
        identities.add(identity)
        start, resume = interval["start_date"], interval["resume_date"]
        require(strict_day(start) < strict_day(resume) and code in stock_codes, "interval date or company escapes scope")
        require(start in calendar and resume in calendar, "interval endpoints require source calendar sessions")
        names = interval["document_ids"]
        require(isinstance(names, list) and names and len(names) == len(set(names)), "interval sources duplicated or empty")
        assertions = []
        for name in names:
            require(name in documents and documents[name]["stock_code"] == code, "interval uses another company's evidence")
            assertions.extend(documents[name]["assertions"])
        require(any(a["event"] == "SUSPEND" and a["date"] == start for a in assertions), "interval lacks confirmed start")
        require(any(a["event"] == "RESUME" and a["date"] == resume for a in assertions), "interval lacks confirmed resumption")
        require(not any(a["event"] == "RESUME" and start < a["date"] < resume for a in assertions), "earlier confirmed resumption conflicts with interval")
        for day in calendar:
            if start <= day < resume:
                require((code, day) not in index, "overlapping company suspension evidence")
                index[code, day] = identity
    return index


def reconcile_positions(positions, calendar, stock_codes, intervals, documents, parameters=(0, 1)):
    """Retain every source position and raw null; attach a separate status.

    Whole-request failure retains its failure even when a date is independently
    documented as suspended. A quoted row inside a suspension is a conflict,
    never silently overwritten. Neither a zero volume nor an absent row supplies
    documentary evidence. All model eligibility remains closed.
    """
    require(isinstance(parameters, (tuple, list)) and parameters and len(parameters) == len(set(parameters)), "invalid parameter grid")
    require(all(type(p) is int and p in {0, 1} for p in parameters), "unsupported parameter identity")
    index = interval_index(calendar, stock_codes, intervals, documents)
    expected = {(code, parameter, day) for code in stock_codes for parameter in parameters for day in calendar}
    seen, counts, output, lookup = set(), Counter(), [], {}
    for source in positions:
        code = source.get("secid", "")[2:]
        require(source.get("kind") == "stock", "non-stock position entered stock reconciliation")
        require(source.get("secid") == ("1." if code.startswith("6") else "0.") + code, "source exchange identity invalid")
        key = code, source.get("fqt"), source.get("trade_date")
        require(type(source.get("fqt")) is int, "source parameter must be an integer, not bool")
        require(key in expected and key not in seen, "source position duplicated or outside complete scope")
        seen.add(key)
        status = source.get("source_observation_status")
        require(status in SOURCE_STATUSES and source.get("model_eligible") is False, "source status or eligibility invalid")
        require(source.get("certified_trade_status") is None, "original source status was already changed")
        fields = source.get("provider_fields")
        require(isinstance(fields, dict) and set(fields) == {f"f{n}" for n in range(51, 62)}, "raw field contract differs")
        raw_line = source.get("raw_line")
        if status == "SOURCE_ROW_PRESENT_QC_PENDING":
            require(isinstance(raw_line, str) and len(raw_line.split(",")) == 11
                    and dict(zip([f"f{n}" for n in range(51, 62)], raw_line.split(","))) == fields
                    and fields["f51"] == key[2], "present source raw row differs")
        else:
            require(raw_line is None and all(value is None for value in fields.values()), "missing source value was filled")
        interval = index.get((code, key[2]))
        require(not (interval and status == "SOURCE_ROW_PRESENT_QC_PENDING"), "quoted row conflicts with documented full-session suspension")
        row = deepcopy(source)
        row["documented_trade_status"] = "SUSPENDED_FULL_SESSION" if interval else None
        row["suspension_interval_id"] = interval
        row["status_evidence_mode"] = "RETROSPECTIVE_ISSUER_ANNOUNCEMENTS" if interval else None
        row["historical_as_of_availability_certified"] = False
        row["reconciliation_status"] = (
            "FAILED_REQUEST_WITH_DOCUMENTED_SUSPENSION" if interval and status == "SOURCE_REQUEST_FAILED" else
            "ABSENT_SOURCE_WITH_DOCUMENTED_SUSPENSION" if interval else
            "PRESENT_SOURCE_BASIS_QC_PENDING" if status == "SOURCE_ROW_PRESENT_QC_PENDING" else
            "FAILED_REQUEST_UNKNOWN_TRADE_STATUS" if status == "SOURCE_REQUEST_FAILED" else
            "ABSENT_SOURCE_UNKNOWN_TRADE_STATUS")
        counts[row["reconciliation_status"]] += 1
        output.append(row)
        lookup[key] = row
    require(seen == expected, "source grid omitted a company, parameter or calendar day")
    for interval in intervals:
        for parameter in parameters:
            endpoint = lookup[interval["stock_code"], parameter, interval["resume_date"]]
            require(endpoint["source_observation_status"] == "SOURCE_ROW_PRESENT_QC_PENDING", "announced resumption lacks its source quote")
    return output, {
        "pipeline_version": VERSION, "positions": len(output), "companies_retained": len(stock_codes),
        "documented_suspension_company_days": len(index), "reconciliation_counts": dict(counts),
        "source_status_counts": dict(Counter(r["source_observation_status"] for r in output)),
        "missing_source_values_filled": False, "point_in_time_status_feed_certified": False,
        "economic_return_basis_certified": False, "model_eligible": False,
    }
