"""Parse dated PBC OMO facts without assigning equity-price effects."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser

CHINA = timezone(timedelta(hours=8))
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\ufeff", "")).strip()


class BulletinHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.regions = [], {}
        self.values = {"title": [], "timestamp": [], "body": []}
        self.rows, self.row, self.cell = [], None, None
        self.region_counts = {"title": 0, "timestamp": 0, "body": 0}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        region = "title" if tag == "title" else "timestamp" if attributes.get("id") == "shijian" else "body" if attributes.get("id") == "zoom" else None
        if region:
            self.region_counts[region] += 1
            self.regions[region] = len(self.stack)
        if "body" in self.regions:
            if tag == "tr":
                if self.row is not None:
                    raise ValueError("nested bulletin rows are unsupported")
                self.row = []
            elif tag in {"td", "th"} and self.row is not None:
                self.cell = []
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if "body" in self.regions:
            if tag in {"td", "th"} and self.cell is not None:
                self.row.append(normalize("".join(self.cell)))
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.rows.append(self.row)
                self.row = None
        if tag not in self.stack:
            return
        position = len(self.stack) - 1 - self.stack[::-1].index(tag)
        self.stack = self.stack[:position]
        for region, depth in list(self.regions.items()):
            if depth >= position:
                del self.regions[region]

    def handle_data(self, value):
        if any(tag in {"script", "style"} for tag in self.stack):
            return
        for region in self.regions:
            self.values[region].append(value)
        if self.cell is not None:
            self.cell.append(value)


def chinese_number(value: str) -> int:
    digits = {char: number for number, char in enumerate("〇一二三四五六七八九")}
    digits["零"] = 0
    if "十" not in value:
        return int("".join(str(digits[char]) for char in value))
    left, right = value.split("十")
    return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)


def parse_bulletin(raw: bytes, expected_date: str, expected_title: str) -> dict:
    # Fail rather than replace undecodable characters in factual source material.
    text = raw.decode("utf-8")
    if "\ufffd" in text:
        raise ValueError("bulletin source contains replacement characters")
    parser = BulletinHTML()
    parser.feed(text)
    if parser.region_counts != {"title": 1, "timestamp": 1, "body": 1}:
        raise ValueError("bulletin title, historical page clock or body is missing/ambiguous")
    title = normalize("".join(parser.values["title"]))
    timestamp = normalize("".join(parser.values["timestamp"]))
    body = normalize(" ".join(parser.values["body"]))
    if title != expected_title or not re.fullmatch(r"公开市场业务交易公告 \[2019\]第\d+号", title):
        raise ValueError("bulletin title disagrees with frozen discovery index")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", timestamp):
        raise ValueError("bulletin requires the explicit historical page timestamp")
    stamp = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA)
    if stamp.date().isoformat() != expected_date:
        raise ValueError("bulletin page date disagrees with discovery index")
    footer = re.findall(r"([〇零一二三四五六七八九]{4})年([一二三四五六七八九十]+)月([一二三四五六七八九十]+)日", body)
    if len(footer) != 1 or date(*(chinese_number(value) for value in footer[0])).isoformat() != expected_date:
        raise ValueError("bulletin signed date is missing or differs from publication date")
    if "中国人民银行公开市场业务操作室" not in body:
        raise ValueError("bulletin operator signature is missing")
    repo_absent = bool(re.search(r"不开展逆回购(?:操作)?", body))
    cbs = "央行票据互换" in body or "CBS" in body
    hk_bill = "央行票据" in body and "香港金融管理局" in body
    mlf = "中期借贷便利" in body or "MLF" in body
    repo = "逆回购操作情况" in body
    operations = []
    for cells in parser.rows:
        fields = [cells[2], cells[1], cells[3]] if len(cells) == 4 and hk_bill and "央行票据（香港）" in cells[0] else cells
        if len(fields) != 3:
            continue
        term = re.fullmatch(r"(\d+)(天|年|个月)(?:[（(]\d+天[）)])?", re.sub(r"\s+", "", fields[0]))
        amount = re.fullmatch(r"(\d+(?:\.\d+)?)亿元", re.sub(r"\s+", "", fields[1]))
        rate = re.fullmatch(r"(\d+(?:\.\d+)?)%", re.sub(r"\s+", "", fields[2]))
        if not (term and amount and rate):
            continue
        instrument = "hk_central_bank_bill" if hk_bill else "cbs" if cbs else "mlf" if mlf and term[2] == "年" else "reverse_repo" if repo else None
        if instrument is None:
            raise ValueError("bulletin tender table has an unsupported instrument")
        operations.append({"instrument": instrument, "tenor_value": int(term[1]), "tenor_unit": term[2],
                           "gross_amount_100m_yuan": float(Decimal(amount[1])), "rate_pct": float(Decimal(rate[1])),
                           "rate_kind": "bill_yield" if hk_bill else "fee" if instrument == "cbs" else "tender_interest",
                           "market": "offshore_hk" if hk_bill else "mainland", "table_evidence": cells})
    if cbs and not operations:
        amount = re.search(r"操作量为(\d+(?:\.\d+)?)亿元", body)
        tenor = re.search(r"期限为(\d+)(个月|年)", body)
        rate = re.search(r"费率为(\d+(?:\.\d+)?)%", body)
        if not (amount and tenor and rate):
            raise ValueError("CBS facts do not have complete explicit source evidence")
        operations.append({"instrument": "cbs", "tenor_value": int(tenor[1]), "tenor_unit": tenor[2],
                           "gross_amount_100m_yuan": float(Decimal(amount[1])), "rate_pct": float(Decimal(rate[1])),
                           "rate_kind": "fee", "market": "mainland", "table_evidence": None})
    if (mlf and not any(row["instrument"] == "mlf" for row in operations)
            or repo and not any(row["instrument"] == "reverse_repo" for row in operations)
            or hk_bill and not any(row["instrument"] == "hk_central_bank_bill" for row in operations)
            or repo_absent and any(row["instrument"] == "reverse_repo" for row in operations)
            or not operations and not repo_absent):
        raise ValueError("bulletin stated operations are omitted or contradictory")
    return {"title": title, "publication_timestamp": stamp.isoformat(), "signed_date": expected_date,
            "body_text": body, "operations": operations, "explicit_no_reverse_repo": repo_absent,
            "gross_reverse_repo_amount_100m_yuan": 0.0 if repo_absent else sum(row["gross_amount_100m_yuan"] for row in operations if row["instrument"] == "reverse_repo") if repo else None,
            "net_liquidity_100m_yuan": None,
            "use_policy": {"mode": "source_verified_context", "agent_signal_enabled": False}}


def annotate_rate_changes(records: list[dict]) -> None:
    previous = {}
    for record in sorted(records, key=lambda row: (row["publication_timestamp"], row["title"])):
        for operation in record["operations"]:
            key = (operation["instrument"], operation["tenor_value"], operation["tenor_unit"])
            prior = previous.get(key)
            operation["previous_observation"] = prior
            operation["change_from_previous_observed_bps"] = float((Decimal(str(operation["rate_pct"])) - Decimal(str(prior["rate_pct"]))) * 100) if prior else None
            previous[key] = {"title": record["title"], "publication_timestamp": record["publication_timestamp"],
                             "source_sha256": record["source"]["sha256"], "rate_pct": operation["rate_pct"]}


def lagged_context(records: list[dict], calendar: list[dict]) -> list[dict]:
    result = []
    for clock in calendar:
        if not clock["signal_cutoff_date"] < clock["trade_date"]:
            raise ValueError("monetary context clock must precede the labelled step")
        cutoff = datetime.fromisoformat(clock["signal_cutoff_date"] + "T23:59:59+08:00")
        visible = sorted((record for record in records if datetime.fromisoformat(record["publication_timestamp"]) <= cutoff),
                         key=lambda row: (row["publication_timestamp"], row["title"]))
        latest = {}
        for record in visible:
            for operation in record["operations"]:
                key = operation["instrument"] + ":" + str(operation["tenor_value"]) + operation["tenor_unit"]
                latest[key] = {"rate_pct": operation["rate_pct"], "rate_kind": operation["rate_kind"],
                               "change_from_previous_observed_bps": operation["change_from_previous_observed_bps"],
                               "publication_timestamp": record["publication_timestamp"], "source_sha256": record["source"]["sha256"], "title": record["title"]}
        on_cutoff = [record for record in visible if record["signed_date"] == clock["signal_cutoff_date"]]
        declarations = [record["gross_reverse_repo_amount_100m_yuan"] for record in on_cutoff if record["gross_reverse_repo_amount_100m_yuan"] is not None]
        result.append({**clock, "cutoff_timestamp": cutoff.isoformat(), "latest_observed_instrument_rates": latest,
                       "cutoff_date_bulletins": [record["title"] for record in on_cutoff],
                       "cutoff_date_gross_reverse_repo_100m_yuan": sum(declarations) if declarations else None,
                       "cutoff_date_net_liquidity_100m_yuan": None,
                       "agent_signal_enabled": False})
    return result
