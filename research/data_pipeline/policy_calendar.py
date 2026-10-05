"""Source facts, scheduled implementation and first-visible policy changes."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser

from .monetary_operations import CHINA, VOID, normalize


class PolicyHTML(HTMLParser):
    """Read only named article regions; ignore navigation and script clocks."""

    def __init__(self, kind: str):
        super().__init__(convert_charrefs=True)
        self.kind, self.stack, self.active = kind, [], {}
        self.values = {key: [] for key in ("title", "heading", "clock", "body", "attribution")}
        self.counts = {key: 0 for key in self.values}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        key = "title" if tag == "title" else None
        if self.kind == "lpr":
            if "title-heading" in classes:
                key = "heading"
            elif "article-a-toolbar" in classes:
                key = "clock"
            elif attrs.get("id") == "ewebeditor_content":
                key = "body"
        elif self.kind == "rrr_republication":
            if "c_time" in classes:
                key = "clock"
            elif attrs.get("id") == "zoom":
                key = "body"
            elif attrs.get("id") == "ly":
                key = "attribution"
        if key:
            self.counts[key] += 1
            self.active[key] = len(self.stack)
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID or tag not in self.stack:
            return
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        self.stack = self.stack[:index]
        for key, depth in list(self.active.items()):
            if depth >= index:
                del self.active[key]

    def handle_data(self, value):
        if any(tag in {"script", "style"} for tag in self.stack):
            return
        for key in self.active:
            self.values[key].append(value)


def article(raw: bytes, kind: str, expected_date: str, expected_title: str) -> tuple[str, datetime]:
    decoded = raw.decode("utf-8")
    if "\ufffd" in decoded:
        raise ValueError("policy source contains replacement characters")
    parser = PolicyHTML(kind)
    parser.feed(decoded)
    required = {"title", "clock", "body"} | ({"heading"} if kind == "lpr" else {"attribution"})
    if any(parser.counts[key] != 1 for key in required):
        raise ValueError("policy article regions are missing or ambiguous")
    values = {key: normalize((" " if key == "clock" else "").join(value)) for key, value in parser.values.items()}
    suffix = "_中国货币网" if kind == "lpr" else "_中共广东省委金融委员会办公室"
    if values["title"] != expected_title + suffix or kind == "lpr" and values["heading"] != expected_title:
        raise ValueError("policy page identity differs from the frozen selection")
    if kind == "rrr_republication" and values["attribution"] != "中国人民银行网站":
        raise ValueError("RRR republication is not explicitly attributed to PBC")
    stamps = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?![:\d])", values["clock"])
    if len(stamps) != 1:
        raise ValueError("policy article requires one explicit minute publication clock")
    stamp = datetime.strptime(stamps[0], "%Y-%m-%d %H:%M").replace(tzinfo=CHINA)
    if stamp.date().isoformat() != expected_date:
        raise ValueError("policy article date differs from source discovery")
    return values["body"], stamp


def base_record(kind: str, title: str, body: str, stamp: datetime) -> dict:
    return {"kind": kind, "title": title, "body_text": body, "signed_date": stamp.date().isoformat(),
            "publication_timestamp": stamp.isoformat(timespec="minutes"), "publication_precision": "minute",
            "available_at": (stamp + timedelta(seconds=59)).isoformat(), "availability_rule": "end_of_publication_minute_bound",
            "operations": [], "policy_measures": [], "explicit_no_reverse_repo": None,
            "gross_reverse_repo_amount_100m_yuan": None, "net_liquidity_100m_yuan": None,
            "use_policy": {"mode": "source_verified_context", "agent_signal_enabled": False}}


def parse_lpr(raw: bytes, expected_date: str, expected_title: str) -> dict:
    body, stamp = article(raw, "lpr", expected_date, expected_title)
    compact = re.sub(r"\s+", "", body)
    match = re.fullmatch(r"中国人民银行授权全国银行间同业拆借中心公布，(\d{4})年(\d+)月(\d+)日贷款市场报价利率（LPR）为：1年期LPR为(\d+(?:\.\d+)?)%，5年期以上LPR为(\d+(?:\.\d+)?)%。以上LPR在下一次发布LPR之前有效。", compact)
    if not match or date(*(int(value) for value in match.groups()[:3])).isoformat() != expected_date:
        raise ValueError("LPR body does not state exactly two supported rates for its publication date")
    result = base_record("lpr", expected_title, body, stamp)
    for number, unit, value in ((1, "年", match[4]), (5, "年以上", match[5])):
        result["operations"].append({"instrument": "lpr", "tenor_value": number, "tenor_unit": unit,
                                     "rate_pct": float(Decimal(value)), "rate_kind": "lending_benchmark",
                                     "market": "mainland", "gross_amount_100m_yuan": None, "table_evidence": None,
                                     "evidence_text": compact, "source_declared_validity": "until_next_LPR_publication"})
    return result


def parse_rrr(raw: bytes, expected_date: str, expected_title: str) -> dict:
    body, stamp = article(raw, "rrr_republication", expected_date, expected_title)
    compact = re.sub(r"\s+", "", body)
    broad = re.search(r"中国人民银行决定于(\d{4})年(\d+)月(\d+)日全面下调金融机构存款准备金率(\d+(?:\.\d+)?)个百分点（不含([^）]+)）", compact)
    targeted = re.search(r"再额外对(.*?)定向下调存款准备金率(\d+(?:\.\d+)?)个百分点，于(\d+)月(\d+)日和(\d+)月(\d+)日分两次实施到位，每次下调(\d+(?:\.\d+)?)个百分点", compact)
    if not broad or not targeted or targeted[1] != "仅在省级行政区域内经营的城市商业银行":
        raise ValueError("RRR announcement scope or phased schedule is incomplete")
    effective = date(*(int(value) for value in broad.groups()[:3]))
    announced = stamp.date()
    if effective < announced or Decimal(targeted[2]) != 2 * Decimal(targeted[7]):
        raise ValueError("RRR date or targeted total contradicts the two stated phases")
    result = base_record("rrr_republication", expected_title, body, stamp)
    exclusions = broad[5].replace("和", "、").split("、")
    if len(set(exclusions)) != 3 or set(exclusions) != {"财务公司", "金融租赁公司", "汽车金融公司"}:
        raise ValueError("RRR excluded institution scope differs from the supported source")
    measures = [{"measure_key": "general", "scope_key": "financial_institutions_except_named_exclusions",
                 "scope_text": "金融机构", "excluded_institutions": exclusions, "effective_date": effective.isoformat(),
                 "change_percentage_points": float(-Decimal(broad[4])), "evidence_text": broad[0]}]
    for key, month, day in (("targeted_phase_1", targeted[3], targeted[4]), ("targeted_phase_2", targeted[5], targeted[6])):
        phase = date(effective.year, int(month), int(day))
        if phase < effective:
            raise ValueError("RRR targeted phase date precedes the general implementation")
        measures.append({"measure_key": key, "scope_key": "province_only_city_commercial_banks",
                         "scope_text": targeted[1], "excluded_institutions": [], "effective_date": phase.isoformat(),
                         "change_percentage_points": float(-Decimal(targeted[7])), "evidence_text": targeted[0]})
    if measures[1]["effective_date"] >= measures[2]["effective_date"]:
        raise ValueError("RRR implementation phases are duplicated or out of order")
    for measure in measures:
        measure.update({"absolute_reserve_requirement_pct": None, "issuer_exposure": "unmapped",
                        "equity_direction": None, "response_coefficient": None, "net_liquidity_100m_yuan": None})
    result["policy_measures"] = measures
    return result


def parse_rrr_legal_text(pages: list[str], approved_decimal_aliases: dict[str, str]) -> dict:
    """Corroborate a visually checked legal PDF, preserving its OCR ambiguity."""
    if len(pages) != 4:
        raise ValueError("RRR legal source page count differs")
    text = re.sub(r"\s+", "", "\n".join(pages))
    if not all(token in text for token in ("银发(2019)223号", "下调金融机构存款准备金率的通知", "中国人民银行办公厅2019年9月10日印发")):
        raise ValueError("RRR legal document identity differs")
    # This source's page boundary carries three non-numeric OCR specks; the
    # rendered first two pages have been inspected before approving decimals.
    broad = re.search(r"自(\d{4})年(\d+)月(\d+)日起,下调金融机构人民币存款准备[‘·电,]*金率([O\d]+\.\d+)个百分点", text)
    targeted = re.findall(r"自(\d{4})年(\d+)月(\d+)日起,下调仅在本省级行政区域内经营的城市商业银行人民币存款准备金率([O\d]+\.\d+)个百分点", text)
    if not broad or len(targeted) != 2 or "财务公司、汽车金融公司、金融租赁公司存款准备金率保持不变" not in text:
        raise ValueError("RRR legal PDF scope or schedule cannot be corroborated")
    if "在该行政区域外没有设立分支机构的城市商业银行" not in text:
        raise ValueError("RRR legal PDF lacks the province-only bank definition")
    schedule, aliases = [], []
    for key, values in (("general", broad.groups()), ("targeted_phase_1", targeted[0]), ("targeted_phase_2", targeted[1])):
        token = values[3]
        if not re.fullmatch(r"\d+\.\d+", token):
            if token not in approved_decimal_aliases:
                raise ValueError("RRR legal PDF decimal OCR ambiguity lacks visual source review")
            corrected = approved_decimal_aliases[token]
            if not re.fullmatch(r"\d+\.\d+", corrected):
                raise ValueError("RRR legal PDF approved decimal is not numeric")
            aliases.append({"measure_key": key, "extracted_token": token, "visually_verified_token": corrected})
            token = corrected
        schedule.append({"measure_key": key, "effective_date": date(*(int(v) for v in values[:3])).isoformat(),
                         "change_percentage_points": float(-Decimal(token))})
    return {"document_id": "银发〔2019〕223号", "page_count": 4, "office_imprint_date": "2019-09-10",
            "publication_timestamp": None, "use_policy": "corroboration_only_no_publication_clock",
            "schedule": schedule, "decimal_ocr_normalizations": aliases,
            "scope_definition": "Province-only city commercial banks have no branches outside their registered provincial-level region."}


def build_calendar(records: list[dict], calendar: list[dict]) -> list[dict]:
    """Separate inherited policy state, new information and known implementation."""
    if len({row["source_id"] for row in records}) != len(records):
        raise ValueError("policy calendar source identities are duplicated")
    ordered = sorted(records, key=lambda row: (datetime.fromisoformat(row["available_at"]), row["source_id"]))
    previous_cutoff, previous_step, result = None, None, []
    for clock in calendar:
        cutoff = datetime.fromisoformat(clock["signal_cutoff_date"] + "T23:59:59+08:00")
        if clock["signal_cutoff_date"] >= clock["trade_date"] or previous_cutoff is not None and (cutoff <= previous_cutoff or clock["trade_date"] <= previous_step):
            raise ValueError("policy calendar clocks are unordered or do not precede labelled steps")
        visible = [record for record in ordered if datetime.fromisoformat(record["available_at"]) <= cutoff]
        newly_visible = [] if previous_cutoff is None else [record for record in visible if datetime.fromisoformat(record["available_at"]) > previous_cutoff]
        latest, schedule, changes, newly_effective = {}, [], [], []
        for record in visible:
            for operation in record["operations"]:
                key = operation["instrument"] + ":" + str(operation["tenor_value"]) + operation["tenor_unit"]
                latest[key] = {"rate_pct": operation["rate_pct"], "rate_kind": operation["rate_kind"], "market": operation["market"],
                               "source_id": record["source_id"], "source_sha256": record["source"]["sha256"],
                               "publication_timestamp": record["publication_timestamp"], "available_at": record["available_at"],
                               "change_from_previous_observed_bps": operation["change_from_previous_observed_bps"]}
            for measure in record.get("policy_measures", []):
                measure_id = record["source_id"] + ":" + measure["measure_key"]
                schedule.append({**measure, "measure_id": measure_id, "source_id": record["source_id"],
                                 "announcement_available_at": record["available_at"],
                                 "status": "effective_by_cutoff" if measure["effective_date"] <= clock["signal_cutoff_date"] else "scheduled_after_cutoff",
                                 "anticipated_from_prior_announcement": True})
                if (previous_cutoff is not None and datetime.fromisoformat(record["available_at"]) <= previous_cutoff
                        and previous_cutoff.date().isoformat() < measure["effective_date"] <= clock["signal_cutoff_date"]):
                    newly_effective.append(measure_id)
        for record in newly_visible:
            for operation in record["operations"]:
                if operation["change_from_previous_observed_bps"] in {None, 0.0}:
                    continue
                changes.append({key: operation[key] for key in ("instrument", "tenor_value", "tenor_unit", "market", "rate_kind", "rate_pct", "change_from_previous_observed_bps", "previous_observation")}
                               | {"source_id": record["source_id"], "available_at": record["available_at"], "source_sha256": record["source"]["sha256"],
                                  "equity_direction": None, "response_coefficient": None, "agent_signal_enabled": False})
        on_cutoff = [record for record in visible if record["signed_date"] == clock["signal_cutoff_date"]]
        gross = [record["gross_reverse_repo_amount_100m_yuan"] for record in on_cutoff if record["gross_reverse_repo_amount_100m_yuan"] is not None]
        result.append({**clock, "cutoff_timestamp": cutoff.isoformat(), "baseline_state_only": previous_cutoff is None,
                       "baseline_source_ids": [record["source_id"] for record in visible] if previous_cutoff is None else [],
                       "newly_visible_source_ids": [record["source_id"] for record in newly_visible], "new_observed_rate_changes": changes,
                       "latest_observed_instrument_rates": latest, "known_rrr_schedule": schedule,
                       "newly_effective_known_rrr_measure_ids": newly_effective,
                       "cutoff_date_gross_reverse_repo_100m_yuan": sum(gross) if gross else None,
                       "cutoff_date_net_liquidity_100m_yuan": None, "agent_signal_enabled": False})
        previous_cutoff, previous_step = cutoff, clock["trade_date"]
    return result
