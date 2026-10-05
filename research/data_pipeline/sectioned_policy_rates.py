"""Preserve separate MLF/TMLF table scope and renewable tenor qualifiers."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from .monetary_operations import BulletinHTML, CHINA, chinese_number, normalize


class SectionTables(BulletinHTML):
    def __init__(self):
        super().__init__()
        self.context, self.sections, self.table = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == "table" and "body" in self.regions:
            if self.table is not None:
                raise ValueError("nested policy tender tables are unsupported")
            context = re.sub(r"\s+", "", "".join(self.context))
            label = re.search(r"(TMLF|(?<!T)MLF)操作情况$", context)
            if label is None:
                raise ValueError("policy tender table lacks its immediately preceding tool heading")
            self.table = {"heading": label[1], "row_start": len(self.rows)}
            self.context = []
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "table" and self.table is not None:
            self.sections.append({"heading": self.table["heading"], "rows": self.rows[self.table["row_start"]:]})
            self.table, self.context = None, []
        super().handle_endtag(tag)

    def handle_data(self, value):
        if self.table is None and "body" in self.regions and not any(tag in {"script", "style"} for tag in self.stack):
            self.context.append(value)
        super().handle_data(value)


def parse_sectioned_bulletin(raw: bytes, expected_date: str, expected_title: str) -> dict:
    decoded = raw.decode("utf-8")
    if "\ufffd" in decoded:
        raise ValueError("sectioned policy source encoding is damaged")
    parser = SectionTables()
    parser.feed(decoded)
    if parser.region_counts != {"title": 1, "timestamp": 1, "body": 1} or parser.table is not None:
        raise ValueError("sectioned policy article regions are ambiguous or unclosed")
    title = normalize("".join(parser.values["title"]))
    clock = normalize("".join(parser.values["timestamp"]))
    body = normalize(" ".join(parser.values["body"]))
    day = date.fromisoformat(expected_date)
    if title != expected_title or not re.fullmatch(r"公开市场业务交易公告 \[" + str(day.year) + r"\]第\d+号", title):
        raise ValueError("sectioned policy title/year differs")
    if not re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", clock):
        raise ValueError("sectioned policy lacks a historical second clock")
    stamp = datetime.strptime(clock, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA)
    signed = re.findall(r"([〇零一二三四五六七八九]{4})年([一二三四五六七八九十]+)月([一二三四五六七八九十]+)日", body)
    if stamp.date() != day or len(signed) != 1 or date(*(chinese_number(value) for value in signed[0])) != day or "中国人民银行公开市场业务操作室" not in body:
        raise ValueError("sectioned policy publication/signed date or operator differs")
    if set(section["heading"] for section in parser.sections) != {"MLF", "TMLF"} or len(parser.sections) != 2:
        raise ValueError("sectioned policy requires two unambiguous distinct MLF/TMLF tables")
    operations = []
    for section in parser.sections:
        if len(section["rows"]) != 2 or section["rows"][0] != ["期限", "操作量", "操作利率"]:
            raise ValueError("sectioned policy table header/data coverage differs")
        cells = section["rows"][1]
        if len(cells) != 3:
            raise ValueError("sectioned policy tender row lacks three fields")
        compact = [re.sub(r"\s+", "", value) for value in cells]
        tenor = re.fullmatch(r"(\d+)年(?:（可展期(\d+)次，实际期限为(\d+)年）)?", compact[0])
        amount = re.fullmatch(r"(\d+(?:\.\d+)?)亿元", compact[1])
        rate = re.fullmatch(r"(\d+(?:\.\d+)?)%", compact[2])
        if not (tenor and amount and rate):
            raise ValueError("sectioned policy tenor/quantity/rate evidence is incomplete")
        renewable = tenor[2] is not None
        if renewable and (section["heading"] != "TMLF" or int(tenor[3]) != int(tenor[1]) * (1 + int(tenor[2]))):
            raise ValueError("renewable policy tenor contradicts its extension schedule")
        operation = {"instrument": "tmlf" if section["heading"] == "TMLF" else "mlf", "tenor_value": int(tenor[1]),
                     "tenor_unit": "年", "rate_pct": float(Decimal(rate[1])), "gross_amount_100m_yuan": float(Decimal(amount[1])),
                     "rate_kind": "tender_interest", "market": "mainland", "table_evidence": cells, "table_heading": section["heading"],
                     "renewal_count_stated": int(tenor[2]) if renewable else None,
                     "actual_term_years_stated": int(tenor[3]) if renewable else None,
                     "tenor_qualifier_text": compact[0] if renewable else None}
        operations.append(operation)
    if not re.search(r"(?:不开展|无)逆回购", re.sub(r"\s+", "", body)):
        raise ValueError("sectioned MLF/TMLF scope lacks its explicit no-repo statement")
    return {"kind": "omo", "title": title, "publication_timestamp": stamp.isoformat(), "publication_precision": "second",
            "available_at": stamp.isoformat(), "availability_rule": "explicit_original_page_second_clock", "signed_date": expected_date,
            "body_text": body, "operations": operations, "policy_measures": [], "explicit_no_reverse_repo": True,
            "gross_reverse_repo_amount_100m_yuan": 0.0, "net_liquidity_100m_yuan": None,
            "use_policy": {"mode": "source_verified_context", "agent_signal_enabled": False}}
