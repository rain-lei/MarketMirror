"""Independently reconstruct report identities and date eligibility from raw APIs."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/issuer_disclosure_inventory_raw_2019_2020_v1"
OUTPUT = ROOT / "research_outputs/issuer_disclosure_inventory_audit_2019_2020_v1.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute() -> dict:
    config_path = ROOT / "research/configs/issuer_disclosures_pre_wuhan_2019_2020.json"
    protocol = json.loads(config_path.read_text(encoding="utf-8"))
    manifest_path, inventory_path = SOURCE / "manifest.json", SOURCE / "inventory.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    bindings = {str(manifest_path): digest(manifest_path), str(inventory_path): digest(inventory_path),
                **{str(ROOT / name): value for name, value in {**manifest["inputs"], **manifest["code_sha256"]}.items()}}
    for saved in manifest["companies"].values():
        for response in saved.get("responses", []):
            bindings[str(ROOT / response["archive_path"])] = response["sha256"]
    if manifest["artifacts"]["inventory.json"]["sha256"] != digest(inventory_path):
        raise ValueError("independent inventory artifact hash differs")

    def verify() -> None:
        if any(digest(Path(name)) != value for name, value in bindings.items()):
            raise ValueError("independent inventory source, producer or raw response changed")

    verify()
    cohort = json.loads((ROOT / protocol["source_cohort"]).read_text(encoding="utf-8"))[protocol["cohort_field"]]
    expected = {row["stock_code"]: row for row in cohort}
    actual = {row["stock_code"]: row for row in inventory["companies"]}
    if (len(expected) != 123 or len(actual) != 123 or set(actual) != set(expected)
            or set(manifest["companies"]) != set(expected)
            or inventory["company_status_counts"] != {"COMPLETE": 123}
            or inventory["financial_values_verified"] is not False or inventory["agent_signal_enabled"] is not False):
        raise ValueError("independent full-cohort coverage or financial adoption differs")
    china = timezone(timedelta(hours=8))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    tokens = {row["title_token"]: name for name, row in protocol["report_periods"].items()}
    counts, missing, revisions, time_precision = Counter(), [], [], Counter()
    for code in sorted(expected):
        saved, company = manifest["companies"][code], actual[code]
        if saved["status"] != "COMPLETE" or saved["error"] is not None or saved["inventory"] != company:
            raise ValueError("independent company completeness or reconstruction differs")
        for key in ("historical_short_name", "industry_code"):
            if company[key] != expected[code][key]:
                raise ValueError("independent cohort historical identity or industry changed")
        for response in saved["responses"]:
            if (response["http_status"] != 200 or response["accepted_json_response"] is not True
                    or response["request_url"] != response["final_url"]):
                raise ValueError("independent inventory cannot treat failed responses as accepted sources")
            form = response["request_form"]
            if response["request_kind"] == "identity":
                if response["request_url"] != protocol["identity_url"] or form != {"keyWord": code, "maxNum": "10"}:
                    raise ValueError("independent identity query request differs")
            else:
                if (response["request_url"] != protocol["announcement_url"]
                        or form["seDate"] != protocol["query_start_date"] + "~" + protocol["query_end_date"]
                        or form["category"].split(";") != protocol["query_categories"]
                        or form["pageSize"] != str(protocol["page_size"])):
                    raise ValueError("independent report query range or categories differ")
        ids = json.loads((ROOT / saved["identity_response_path"]).read_bytes())
        matching = [row for row in ids if row.get("code") == code and row.get("category") in ("A股", "B股")]
        if len(matching) != 1 or matching[0]["orgId"] != company["retrieval_identity"]["org_id"]:
            raise ValueError("independent current identity is ambiguous or changed")
        org = matching[0]["orgId"]
        pages = [json.loads((ROOT / name).read_bytes()) for name in saved["page_response_paths"]]
        total = pages[0]["totalAnnouncement"]
        rows = [row for page in pages for row in page["announcements"] or []]
        if len(rows) != total or any(page["totalAnnouncement"] != total for page in pages) or pages[-1]["hasMore"]:
            raise ValueError("independent raw report pagination is incomplete")
        if len(rows) != company["raw_announcement_rows"]:
            raise ValueError("independent raw report count differs")
        indexed = {row["announcement_id"]: row for row in company["announcements"]}
        if len(indexed) != company["unique_announcement_rows"] or len(indexed) != len({row["announcementId"] for row in rows}):
            raise ValueError("independent report deduplication count differs")
        rebuilt = {}
        for row in rows:
            if row["secCode"] != code or row["orgId"] != org:
                raise ValueError("independent report cross-issuer leakage")
            identifier = str(row["announcementId"])
            title = html.unescape(re.sub(r"<[^>]+>", "", row["announcementTitle"])).strip()
            archive = re.fullmatch(r"finalpage/(\d{4}-\d\d-\d\d)/(\d+)\.[Pp][Dd][Ff]", row["adjunctUrl"])
            if not archive or archive[2] != identifier:
                raise ValueError("independent official archive identity differs")
            day = date.fromisoformat(archive[1])
            stamp = (epoch + timedelta(milliseconds=row["announcementTime"])).astimezone(china)
            available = datetime(day.year, day.month, day.day, tzinfo=china) + timedelta(days=1)
            if stamp.date() != day:
                raise ValueError("independent provider/archive dates disagree")
            time_precision["provider_midnight" if stamp.time().isoformat() == "00:00:00" else "provider_nonmidnight"] += 1
            compact = re.sub(r"\s+", "", title)
            found = re.fullmatch(r"(?!关于)(.*?)(2018年年度报告|2019年半年度报告|2019年第三季度报告)(全文|正文)?(?:[（(]((?:修订|更新|更正)[^）)]*)[）)])?", compact)
            period = tokens[found[2]] if found else None
            form = {"全文": "full_text", "正文": "report_body", None: "plain_report"}[found[3]] if found else None
            eligible = (period is not None and protocol["query_start_date"] <= archive[1] <= protocol["query_end_date"]
                        and available <= datetime.fromisoformat(protocol["snapshot_at"]))
            old = indexed[identifier]
            fields = {"stock_code": code, "org_id": org, "title": title, "archive_date": archive[1],
                      "attachment_url": "https://static.cninfo.com.cn/" + row["adjunctUrl"],
                      "provider_announcement_time_ms": row["announcementTime"], "provider_announcement_timestamp": stamp.isoformat(),
                      "available_at_proxy": available.isoformat(), "publication_precision_used": "date",
                      "report_period_key": period, "report_form": form, "revision_marked_in_title": bool(found and found[4]),
                      "report_end_date": protocol["report_periods"][period]["report_end_date"] if period else None,
                      "eligible": eligible, "financial_values_verified": False, "agent_signal_enabled": False}
            if any(old[key] != value for key, value in fields.items()):
                raise ValueError(f"independent report fields differ: {code}/{identifier}")
            rebuilt[identifier] = {**fields, "announcement_id": identifier}
            counts["announcements"] += 1
        for period, selected in company["reports"].items():
            choices = [row for row in rebuilt.values() if row["eligible"] and row["report_period_key"] == period]
            if choices:
                priority = {"full_text": 3, "plain_report": 2, "report_body": 1}
                highest = max(priority[row["report_form"]] for row in choices)
                choices = [row for row in choices if priority[row["report_form"]] == highest]
                latest = max(row["archive_date"] for row in choices)
                choices = [row for row in choices if row["archive_date"] == latest]
            status = "MISSING_IN_CURRENT_CATALOG" if not choices else "AMBIGUOUS_REPORT_METADATA" if len(choices) != 1 else "AVAILABLE_METADATA_ONLY"
            if selected["status"] != status:
                raise ValueError("independent report selection/QC state differs")
            if status == "AVAILABLE_METADATA_ONLY":
                if selected["selected"] != indexed[choices[0]["announcement_id"]] or selected["competing_announcement_ids"]:
                    raise ValueError("independent preferred report identity differs")
                if choices[0]["revision_marked_in_title"]:
                    revisions.append({"stock_code": code, "period": period, "announcement_id": choices[0]["announcement_id"]})
            else:
                if selected["selected"] is not None:
                    raise ValueError("independent missing/ambiguous report was populated")
                missing.append({"stock_code": code, "period": period, "status": status})
            counts[status] += 1
    verify()
    return {"pipeline_version": "independent-official-issuer-disclosure-inventory-audit-v1", "companies_checked": len(actual),
            "raw_api_responses_checked": sum(len(row["responses"]) for row in manifest["companies"].values()),
            "announcement_rows_checked": counts["announcements"], "report_status_counts": {key: value for key, value in counts.items() if key != "announcements"},
            "date_precision_counts": dict(time_precision), "missing_or_ambiguous_reports": missing, "selected_title_marked_revisions": revisions,
            "financial_values_verified": False, "agent_signal_enabled": False, "inputs": dict(sorted(bindings.items())),
            "code_sha256": digest(Path(__file__)),
            "interpretation": "Independent raw JSON, regex title forms and epoch arithmetic; no production identity, eligibility or report selection functions called. Full 123-company inventory only, not verified financial values."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    value = compute()
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("independent issuer inventory differs from frozen audit")
        print("Independent issuer disclosure inventory audit rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({key: value[key] for key in ("companies_checked", "raw_api_responses_checked", "announcement_rows_checked", "report_status_counts", "missing_or_ambiguous_reports")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
