"""Ground a limited development case study in archived public documents and replies."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time, timezone
from html.parser import HTMLParser
from pathlib import Path

from .signal_validation import load_pack, strict_json_loads

ROOT = Path(__file__).resolve().parents[2]
VERSION = "source-grounded-development-case-facts-v1"
KINDS = {"regulatory_constraint", "operational_disruption", "completed_transaction",
         "approval_uncertainty", "static_information", "information_gap", "calendar_change", "other"}
STATUSES = {"completed", "in_force", "scheduled", "ongoing", "uncertain", "static", "unknown"}
SOURCE_CASE_COUNT = 6


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def clean_text(value):
    lines = [re.sub(r"[ \t\r\f\v\u00a0]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


class ArchivedBody(HTMLParser):
    """Extract a known article element locally; scripts and menus are never instructions."""
    def __init__(self, identifier):
        super().__init__(convert_charrefs=True)
        self.identifier = identifier
        self.stack = []
        self.depth = None
        self.parts = []
        self.paragraphs = []
        self.paragraph = None
        self.hidden = 0
        self.matches = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "hr", "meta", "link", "img", "input", "source", "wbr"}:
            if tag == "br":
                self.handle_data("\n")
            return
        self.stack.append(tag)
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if dict(attrs).get("id") == self.identifier:
            self.matches += 1
            self.depth = len(self.stack)
        if self.depth is not None and tag == "p":
            self.paragraph = []

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        removed = self.stack[index:]
        self.stack = self.stack[:index]
        self.hidden = max(0, self.hidden - sum(t in {"script", "style", "noscript"} for t in removed))
        if tag == "p" and self.paragraph is not None:
            self.paragraphs.append(clean_text("".join(self.paragraph)))
            self.paragraph = None
        if self.depth is not None and len(self.stack) < self.depth:
            self.depth = None
        elif tag in {"p", "div", "li"}:
            self.handle_data("\n")

    def handle_data(self, data):
        if self.depth is not None and not self.hidden:
            self.parts.append(data)
            if self.paragraph is not None:
                self.paragraph.append(data)


def extract_official(event):
    source = event["source"]
    path = ROOT / source["archive_path"]
    raw = path.read_bytes()
    if digest(raw) != source["sha256"]:
        raise ValueError("archived official source changed")
    if event["event_id"] == "asset_management_guidance_2018":
        parser = ArchivedBody("zoom")
        parser.feed(raw.decode("utf-8"))
        if parser.matches != 1:
            raise ValueError("unique PBC body missing")
        text = clean_text("".join(parser.parts))
        rule = "HTML id=zoom; drop script/style; preserve decoded article text, normalize whitespace"
        title = "中国人民银行有关负责人就《关于规范金融机构资产管理业务的指导意见》答记者问"
    elif event["event_id"] == "wuhan_transport_restrictions_2020":
        parser = ArchivedBody("p-detail")
        parser.feed(raw.decode("utf-8"))
        if parser.matches != 1 or not parser.paragraphs:
            raise ValueError("unique Xinhua body missing")
        paragraphs = [p for p in parser.paragraphs if p.startswith("新华社武汉1月23日电")]
        if len(paragraphs) != 1:
            raise ValueError("expected full-text notice paragraph missing")
        text = paragraphs[0]
        rule = "HTML id=p-detail; unique news-dateline paragraph; exclude empty/photo paragraphs"
        title = event["title"]
    else:
        text = raw.decode("utf-8").replace("\r\n", "\n")
        start = "## 关于调整2020年春节休市相关安排的公告"
        end = "二○二○年一月二十七日"
        if text.count(start) != 1 or text.count(end) != 1:
            raise ValueError("unique SSE notice body missing")
        text = clean_text(text[text.index(start):text.index(end) + len(end)])
        rule = "Archived markdown: exact notice heading through signed date; normalize whitespace"
        title = event["title"]
    if len(text) < 100:
        raise ValueError("source extraction too short")
    publication = event["publication"]
    if publication["timestamp"]:
        available = publication["timestamp"]
        precision = "page_timestamp_not_first_public_appearance"
    else:
        available = publication["date"] + "T23:59:59.999999+08:00"
        precision = "date_only_end_of_day_conservative"
    case = {
        "case_id": event["event_id"], "source_kind": source["source_kind"], "title": title,
        "source_url": source["url"], "source_archive_path": source["archive_path"],
        "source_archive_sha256": source["sha256"], "extraction_rule": rule,
        "available_at": available, "visibility_precision": precision,
        "source_scope": event["scope"], "source_use_policy": event["use_policy"],
        "segments": [{"source": "document", "text": text}],
        "development_case_not_holdout": True,
        "hypothetical_exposed_asset": "A",
        "asset_exposure_is_assumed_not_observed": True,
    }
    case["case_text_sha256"] = digest(canonical(case["segments"]))
    return case


def build_cases(catalog_path=None, pack_dir=None):
    catalog_path = catalog_path or ROOT / "research/configs/policy_event_catalog_2018_2020.json"
    pack_dir = pack_dir or ROOT / "research_outputs/semantic_holdout_h2_2020"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    cases = [extract_official(event) for event in catalog["events"]]
    items, _ = load_pack(pack_dir)
    replies = sorted((x for x in items.values() if x["stage"] == "reply"), key=lambda x:x["item_id"])[:3]
    for item in replies:
        # The workbook-derived midnight does not establish an intraday publication.
        original_date = datetime.fromisoformat(item["available_at"]).date()
        case = {
            "case_id": "qa_" + item["item_id"], "source_kind": "company_reply_workbook",
            "title": "公司回复开发案例", "source_url": None, "source_item_id": item["item_id"],
            "source_archive_path": (pack_dir / "annotation_items.jsonl").relative_to(ROOT).as_posix(),
            "source_archive_sha256": digest((pack_dir / "annotation_items.jsonl").read_bytes()),
            "source_text_sha256": item["source_text_sha256"], "stock_code": item["stock_code"],
            "original_available_at": item["available_at"],
            "available_at": original_date.isoformat() + "T23:59:59.999999+08:00",
            "visibility_precision": "workbook_date_only_end_of_day_conservative",
            "segments": item["segments"], "development_case_not_holdout": True,
            "selection_rule": "First three reply-stage source item IDs in ascending lexicographic order",
            "hypothetical_exposed_asset": "A", "asset_exposure_is_assumed_not_observed": True,
        }
        case["case_text_sha256"] = digest(canonical(case["segments"]))
        cases.append(case)
    if len(cases) != SOURCE_CASE_COUNT or len({c["case_id"] for c in cases}) != SOURCE_CASE_COUNT:
        raise ValueError("complete registered six-case source set required")
    return cases


def evidence_span(case, source, quote):
    segments = {s["source"]: s["text"] for s in case["segments"]}
    text = segments.get(source)
    if not isinstance(quote, str) or not quote.strip() or text is None or text.count(quote) != 1:
        raise ValueError("case evidence must occur exactly once in a provided source")
    start = text.index(quote)
    return {"source": source, "quote": quote, "start": start, "end": start + len(quote)}


def normalize_facts(case, raw_response):
    if digest(canonical(case["segments"])) != case["case_text_sha256"]:
        raise ValueError("case source text changed")
    payload = strict_json_loads(raw_response)
    if not isinstance(payload, dict) or set(payload) != {"facts"} or not isinstance(payload["facts"], list) or len(payload["facts"]) > 12:
        raise ValueError("at most twelve structured source facts required")
    facts = []
    seen = set()
    for fact in payload["facts"]:
        if (not isinstance(fact, dict) or set(fact) != {"kind", "status", "claim", "evidence"}
                or fact["kind"] not in KINDS or fact["status"] not in STATUSES
                or not isinstance(fact["claim"], str) or not fact["claim"].strip()
                or not isinstance(fact["evidence"], list) or not fact["evidence"]):
            raise ValueError("source fact enums, claim or evidence invalid")
        spans = []
        for evidence in fact["evidence"]:
            if not isinstance(evidence, dict) or set(evidence) != {"source", "quote"}:
                raise ValueError("exact source and quote fields required")
            spans.append(evidence_span(case, evidence["source"], evidence["quote"]))
        if case["source_kind"] == "company_reply_workbook" and any(s["source"] != "reply" for s in spans):
            raise ValueError("a question cannot establish a company-confirmed fact")
        identity = fact["kind"], tuple((s["source"],s["quote"]) for s in spans)
        if identity in seen:
            raise ValueError("duplicate fact evidence")
        seen.add(identity)
        facts.append({**fact, "evidence": spans})
    return {"case_id": case["case_id"], "case_text_sha256": case["case_text_sha256"], "facts": facts}


def validate_fact_record(case, record):
    if (record["case_id"] != case["case_id"] or record["case_text_sha256"] != case["case_text_sha256"]):
        raise ValueError("fact record belongs to different source text")
    payload = {"facts": [{**fact, "evidence": [{k:s[k] for k in ("source","quote")} for s in fact["evidence"]]}
                         for fact in record["facts"]]}
    rebuilt = normalize_facts(case, canonical(payload).decode("utf-8"))
    if record != rebuilt:
        raise ValueError("saved fact evidence offsets differ from original source")
    return record


def asof_facts(case, record, cutoff):
    """Do not equate missing/unavailable text with a known neutral company event."""
    available = datetime.fromisoformat(case["available_at"])
    current = datetime.fromisoformat(cutoff)
    if available.tzinfo is None or current.tzinfo is None:
        raise ValueError("timezone-aware source and cutoff required")
    validate_fact_record(case, record)
    if current < available:
        return {"case_id": case["case_id"], "available": False, "facts": [], "source_evidence_used": []}
    return {"case_id": case["case_id"], "available": True, "facts": record["facts"],
            "source_evidence_used": [s for fact in record["facts"] for s in fact["evidence"]]}
