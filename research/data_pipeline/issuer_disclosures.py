"""Date-conservative official issuer disclosure inventory; no financial values."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone

CHINA = timezone(timedelta(hours=8))


def identity_match(records: list[dict], code: str) -> dict:
    if not re.fullmatch(r"\d{6}", code) or not isinstance(records, list):
        raise ValueError("issuer identity requires a six-digit code and a response list")
    candidates = [row for row in records if row.get("code") == code and row.get("category") in {"A股", "B股"}]
    if len(candidates) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]+", str(candidates[0].get("orgId", ""))):
        raise ValueError("official current identity is absent, ambiguous or malformed")
    row = candidates[0]
    return {"stock_code": code, "org_id": row["orgId"], "current_short_name": row.get("zwjc"),
            "current_share_category": row["category"], "current_delisted_flag": row.get("delisted"),
            "role": "retrieval_identity_only_not_historical_feature"}


def report_candidate(row: dict, identity: dict, settings: dict) -> dict:
    if row.get("secCode") != identity["stock_code"] or row.get("orgId") != identity["org_id"]:
        raise ValueError("announcement belongs to a different security or issuer")
    identifier = str(row.get("announcementId", ""))
    if not re.fullmatch(r"\d+", identifier):
        raise ValueError("announcement ID is malformed")
    title = html.unescape(re.sub(r"<[^>]*>", "", str(row.get("announcementTitle", "")))).strip()
    if not title or "\ufffd" in title:
        raise ValueError("announcement title is empty or undecodable")
    path = row.get("adjunctUrl", "")
    matched = re.fullmatch(r"finalpage/(\d{4}-\d{2}-\d{2})/(\d+)\.[Pp][Dd][Ff]", path)
    if not matched or matched[2] != identifier:
        raise ValueError("announcement attachment is not an exact official PDF archive path")
    archive_day = date.fromisoformat(matched[1])
    stamp = row.get("announcementTime")
    if type(stamp) is not int or stamp <= 0:
        raise ValueError("announcementTime must be positive integer milliseconds")
    provider = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).astimezone(CHINA)
    if provider.date() != archive_day:
        raise ValueError("provider and official archive calendar dates disagree")
    available = datetime.combine(archive_day + timedelta(days=1), datetime.min.time(), CHINA)
    snapshot = datetime.fromisoformat(settings["snapshot_at"])
    if snapshot.utcoffset() != timedelta(hours=8):
        raise ValueError("disclosure snapshot must be timezone-aware China time")
    compact = re.sub(r"\s+", "", title)
    period, form, revised = None, None, False
    for name, definition in settings["report_periods"].items():
        token = definition["title_token"]
        if token not in compact:
            continue
        tail = compact.split(token, 1)[1]
        prefix = compact.split(token, 1)[0]
        pattern = r"(全文|正文)?(?:[（(]((?:修订|更新|更正)[^）)]*)[）)])?"
        report = re.fullmatch(pattern, tail)
        if report and not prefix.startswith("关于"):
            period, form = name, {"全文": "full_text", "正文": "report_body", None: "plain_report"}[report[1]]
            revised = report[2] is not None
        break
    in_range = settings["query_start_date"] <= archive_day.isoformat() <= settings["query_end_date"]
    eligible = period is not None and in_range and available <= snapshot
    reason = ("eligible_report_metadata" if eligible else "unsupported_report_title_or_form" if period is None
              else "outside_frozen_query_dates" if not in_range else "not_available_by_snapshot")
    return {"stock_code": identity["stock_code"], "org_id": identity["org_id"],
            "announcement_id": identifier, "title": title, "report_period_key": period,
            "report_end_date": settings["report_periods"][period]["report_end_date"] if period else None,
            "report_form": form, "revision_marked_in_title": revised,
            "provider_announcement_time_ms": stamp, "provider_announcement_timestamp": provider.isoformat(),
            "archive_date": archive_day.isoformat(), "available_at_proxy": available.isoformat(),
            "publication_precision_used": "date", "availability_basis": "agreement_of_provider_calendar_date_and_official_archive_date_next_midnight",
            "attachment_url": "https://" + settings["attachment_host"] + "/" + path,
            "eligible": eligible, "eligibility_reason": reason,
            "financial_values_verified": False, "agent_signal_enabled": False}


def inventory_company(cohort: dict, identity_records: list[dict], pages: list[dict], settings: dict) -> dict:
    code = cohort["stock_code"]
    identity = identity_match(identity_records, code)
    if not pages:
        raise ValueError("disclosure inventory lacks its query response")
    declared = pages[0].get("totalAnnouncement")
    if type(declared) is not int or declared < 0:
        raise ValueError("query response lacks a valid total announcement count")
    raw, candidates = [], {}
    for page in pages:
        if page.get("totalAnnouncement") != declared or type(page.get("hasMore")) is not bool:
            raise ValueError("paged disclosure totals changed or continuation is invalid")
        values = page.get("announcements")
        if values is None and declared == 0:
            values = []
        if not isinstance(values, list):
            raise ValueError("query response announcements are not a list")
        raw.extend(values)
    if len(raw) != declared or pages[-1]["hasMore"]:
        raise ValueError("disclosure query is incomplete or truncated")
    for row in raw:
        record = report_candidate(row, identity, settings)
        key = record["announcement_id"]
        if key in candidates and candidates[key] != record:
            raise ValueError("duplicate announcement IDs disagree")
        candidates[key] = record
    records = sorted(candidates.values(), key=lambda row: (row["archive_date"], row["announcement_id"]))
    selected = {}
    priority = {"full_text": 3, "plain_report": 2, "report_body": 1}
    for period in settings["report_periods"]:
        choices = [row for row in records if row["report_period_key"] == period and row["eligible"]]
        if not choices:
            selected[period] = {"status": "MISSING_IN_CURRENT_CATALOG", "selected": None, "competing_announcement_ids": []}
            continue
        form = max(priority[row["report_form"]] for row in choices)
        choices = [row for row in choices if priority[row["report_form"]] == form]
        latest = max(row["archive_date"] for row in choices)
        choices = [row for row in choices if row["archive_date"] == latest]
        if len(choices) != 1:
            selected[period] = {"status": "AMBIGUOUS_REPORT_METADATA", "selected": None,
                                "competing_announcement_ids": sorted(row["announcement_id"] for row in choices)}
        else:
            selected[period] = {"status": "AVAILABLE_METADATA_ONLY", "selected": choices[0], "competing_announcement_ids": []}
    return {"stock_code": code, "historical_short_name": cohort["historical_short_name"],
            "industry_code": cohort["industry_code"], "industry_available_at_proxy": cohort["available_at_proxy"],
            "retrieval_identity": identity, "raw_announcement_rows": len(raw), "unique_announcement_rows": len(records),
            "announcements": records, "reports": selected, "financial_values_verified": False,
            "agent_signal_enabled": False}
