from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any


HEADER_ALIASES: dict[str, str] = {
    "From": "source",
    "数据来源": "source",
    "来源": "source",
    "Scode": "stock_code",
    "股票代码": "stock_code",
    "Coname": "company_name",
    "公司简称": "company_name",
    "Usertp": "user_type",
    "浏览用户/注册用户": "user_type",
    "Usernm": "user_name",
    "用户名": "user_name",
    "Qtm": "question_time",
    "提问时间": "question_time",
    "Qsubj": "question_text",
    "提问内容": "question_text",
    "Wpaonot": "reply_status",
    "上市公司是否回复": "reply_status",
    "Recvtm": "reply_time",
    "回复时间": "reply_time",
    "Reply": "reply_text",
    "回复内容": "reply_text",
    "Words1": "question_length_source",
    "字数1": "question_length_source",
    "Words2": "reply_length_source",
    "字数2": "reply_length_source",
    "Isviolated": "label_company_violation",
    "季度": "fiscal_quarter",
}

DESCRIPTION_VALUES = {
    "数据来源",
    "股票代码",
    "公司简称",
    "浏览用户/注册用户",
    "用户名",
    "提问时间",
    "提问内容",
    "上市公司是否回复",
    "回复时间",
    "回复内容",
    "字数1",
    "字数2",
}


def canonical_header(value: Any, index: int) -> str:
    """Map known source headers and keep unknown financial fields stable."""
    text = "" if value is None else str(value).strip()
    if text in HEADER_ALIASES:
        return HEADER_ALIASES[text]
    if not text:
        return f"unnamed_{index}"
    return f"financial__{text}"


def canonical_headers(headers: list[Any]) -> list[str]:
    result: list[str] = []
    used: dict[str, int] = {}
    for index, value in enumerate(headers):
        name = canonical_header(value, index)
        occurrence = used.get(name, 0)
        used[name] = occurrence + 1
        result.append(name if occurrence == 0 else f"{name}_{occurrence + 1}")
    return result


def row_looks_like_header(row: list[Any], headers: list[Any]) -> bool:
    values = {str(value).strip() for value in row if value not in (None, "")}
    if not values:
        return False
    header_values = {str(value).strip() for value in headers if value not in (None, "")}
    known_count = len(values & (header_values | DESCRIPTION_VALUES))
    return known_count >= 3 and known_count / max(len(values), 1) >= 0.5


def normalize_stock_code(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return str(int(float(text))).zfill(6)
    return text.zfill(6) if text.isdigit() else text


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    for candidate in (text, text.replace("/", "-")):
        try:
            return datetime.fromisoformat(candidate.replace("Z", ""))
        except ValueError:
            continue
    return None


def normalize_reply_status(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if "未回复" in text:
        return "unreplied"
    if "已回复" in text:
        return "replied"
    return text


def make_qa_id(source_file: str, stock_code: str | None, question_time: Any, question_text: Any) -> str:
    payload = "\x1f".join(
        [
            source_file,
            stock_code or "",
            str(question_time or ""),
            str(question_text or "").strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def iso_or_none(value: Any) -> str | None:
    parsed = parse_datetime(value)
    return parsed.isoformat(sep=" ") if parsed else None
