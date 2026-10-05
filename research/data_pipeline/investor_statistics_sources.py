"""Read investor statistics without turning aggregate observations into agent rules.

These readers preserve the reported units, populations and information dates.
Their output is a source inventory, not a calibrated investor population.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from urllib.parse import urlparse


YEARBOOK_DECIMAL_GLYPH = "\U001001b0"
SHANGHAI_TIME = timezone(timedelta(hours=8))
_NUMBER = rf"[0-9]+(?:\s*[.{YEARBOOK_DECIMAL_GLYPH}]\s*[0-9]+)?"


def reported_decimal(text: str, *, glyph_visually_verified: bool = False) -> Decimal:
    """Never guess the meaning of a private-use glyph or accept binary floats."""
    if not isinstance(text, str):
        raise ValueError("A reported decimal must retain its source string")
    normal = unicodedata.normalize("NFKC", text).strip()
    if YEARBOOK_DECIMAL_GLYPH in normal:
        if not glyph_visually_verified:
            raise ValueError("Private-use decimal glyph needs full-page visual review")
        normal = re.sub(rf"\s*{YEARBOOK_DECIMAL_GLYPH}\s*", ".", normal)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", normal):
        raise ValueError("Invalid or ambiguous reported decimal")
    try:
        value = Decimal(normal)
    except InvalidOperation as error:
        raise ValueError("Invalid reported decimal") from error
    if not value.is_finite():
        raise ValueError("Non-finite reported decimal")
    return value


def reported_interval(text: str) -> tuple[Decimal, Decimal]:
    """Use one reported last-place unit; the notes allow rounding or truncation."""
    value = reported_decimal(text)
    last_place = Decimal(1).scaleb(value.as_tuple().exponent)
    return max(Decimal(0), value - last_place), value + last_place


def _table_rows(text: str, count: int, *, glyph_visually_verified: bool) -> list[dict]:
    suffix = r"\s+".join(f"({_NUMBER})" for _ in range(count))
    pattern = re.compile(rf"^(.*?)\s+{suffix}\s*$")
    rows = []
    for line_number, original in enumerate(text.splitlines(), 1):
        normal = unicodedata.normalize("NFKC", original).strip()
        match = pattern.fullmatch(normal)
        if match is None:
            continue
        values = [
            format(reported_decimal(x, glyph_visually_verified=glyph_visually_verified), "f")
            for x in match.groups()[1:]
        ]
        rows.append({"label": match.group(1).strip(), "values": values,
                     "source_line_number": line_number, "source_line": original})
    return rows


def parse_yearbook_holdings(text: str, *, year: int, glyph_visually_verified: bool) -> dict:
    normal = unicodedata.normalize("NFKC", text)
    if ("年末各类投资者持股情况" not in normal
            or re.search(rf"Share\s+Hold\s+of\s+Investors\s+by\s+{year}\b", normal) is None):
        raise ValueError("Holdings table identity or observation year does not match")
    if (re.search(r"持股市值\s*\(\s*亿元\s*\)", normal) is None
            or re.search(r"持股账户数\s*\(\s*万户\s*\)", normal) is None):
        raise ValueError("Reported holding-value or account units are missing")
    rows = _table_rows(text, 4, glyph_visually_verified=glyph_visually_verified)
    if not rows or len({r["label"] for r in rows}) != len(rows):
        raise ValueError("Holdings rows are empty or repeated")
    categories = {
        "自然人投资者": "natural_person", "一般法人": "general_corporation",
        "沪股通": "northbound_channel", "专业机构": "professional_institution",
    }
    totals = {}
    for row in rows:
        label = row["label"]
        row["kind"] = "total" if label in categories else "subgroup"
        row["category"] = categories.get(label)
        for field in [1, 3]:
            if not Decimal(0) <= Decimal(row["values"][field]) <= Decimal(100):
                raise ValueError("Percentage is outside its reported range")
        row["value_unit"] = "100000000_CNY"
        row["account_unit"] = "10000_holding_accounts"
        row["percentage_denominator"] = "all_reported_investor_categories_not_retail_only"
        row["cash_value_observed"] = False
        row["tradable_inventory_observed"] = False
        row["exact_zero_accounts_certified"] = False
        if row["kind"] == "total":
            totals[label] = row
    if set(totals) != set(categories):
        raise ValueError("A published category is missing; do not silently drop northbound holdings")
    for column in [1, 3]:
        bounds = [reported_interval(r["values"][column]) for r in totals.values()]
        if not sum(x[0] for x in bounds) <= Decimal(100) <= sum(x[1] for x in bounds):
            raise ValueError("Published category percentages cannot reconcile at reported precision")
    return {"observation_date": f"{year}-12-31", "market_scope": "SSE",
            "rows": rows, "categories": list(categories.values()),
            "agent_role_mapping_identified": False}


def parse_yearbook_industry_holdings(text: str, *, year: int,
                                    glyph_visually_verified: bool) -> dict:
    normal = unicodedata.normalize("NFKC", text)
    if ("年末分行业持股信息" not in normal
            or re.search(rf"Hold\s+Distribution\s+by\s+{year}\b", normal) is None):
        raise ValueError("Industry holdings table identity or observation year does not match")
    if "持股市值单位为亿元" not in re.sub(r"\s+", "", normal):
        raise ValueError("Industry table unit footnote is missing")
    rows = _table_rows(text, 6, glyph_visually_verified=glyph_visually_verified)
    codes = []
    for row in rows:
        match = re.fullmatch(r"([A-Z])\s+(.+)", row["label"])
        if match is None:
            raise ValueError("Missing industry code or industry name")
        row["industry_code"], row["industry_name"] = match.groups()
        codes.append(row["industry_code"])
        row["column_order"] = ["natural_person", "professional_institution", "general_corporation"]
        row["value_unit"] = "100000000_CNY"
        row["percentage_denominator"] = "three_categories_in_this_industry_table"
        amounts = [Decimal(row["values"][i]) for i in [0, 2, 4]]
        total = sum(amounts)
        if total <= 0:
            raise ValueError("An industry holding-value denominator is nonpositive")
        total_uncertainty = sum(Decimal(1).scaleb(x.as_tuple().exponent) for x in amounts)
        if total <= total_uncertainty:
            raise ValueError("Reported precision cannot identify an industry denominator")
        for category_index, amount in enumerate(amounts):
            value = row["values"][2 * category_index + 1]
            lower, upper = reported_interval(value)
            numerator_uncertainty = Decimal(1).scaleb(amount.as_tuple().exponent)
            implied_lower = max(Decimal(0), amount - numerator_uncertainty) / (total + total_uncertainty) * 100
            implied_upper = (amount + numerator_uncertainty) / (total - total_uncertainty) * 100
            if upper < implied_lower or lower > implied_upper or not Decimal(0) <= Decimal(value) <= 100:
                raise ValueError("Industry percentage disagrees with amounts at reported precision")
    if not rows or len(set(codes)) != len(codes):
        raise ValueError("Industry rows are empty or repeated")
    return {"observation_date": f"{year}-12-31", "market_scope": "SSE", "rows": rows,
            "northbound_category_present": False, "all_market_denominator_equivalent": False}


def parse_szse_survey(archive: dict) -> dict:
    metadata = archive.get("metadata", {})
    url = metadata.get("sourceURL", "")
    if (metadata.get("statusCode") != 200 or urlparse(url).hostname != "www.szse.cn"
            or "深交所发布2020年个人投资者状况调查报告" != metadata.get("title")):
        raise ValueError("Expected the successful primary SZSE survey archive")
    markdown = archive.get("markdown", "")
    heading = "## 深交所发布2020年个人投资者状况调查报告"
    start = markdown.find(heading)
    if start < 0:
        raise ValueError("Survey article heading is missing")
    end = markdown.find("上一篇", start)
    if end < 0:
        raise ValueError("Survey article boundary is missing")
    body = markdown[start:end]
    patterns = {
        "publisher_date": r"时间[：:]\s*(\d{4}-\d{2}-\d{2})",
        "valid_questionnaires": r"收到([0-9,]+)份有效答卷",
        "provinces_excluding_hk_macau_taiwan": r"覆盖全国([0-9]+)个省级行政区（不含港澳台）",
        "cities": r"([0-9]+)个大中小城市",
        "age_lower": r"年龄在([0-9]+)~[0-9]+岁",
        "age_upper": r"年龄在[0-9]+~([0-9]+)岁",
        "prior_months_with_stock_trading": r"过去([0-9]+)个月交易过深沪两市股票",
        "mean_securities_account_assets_10000_cny": r"证券账户平均资产量([0-9.]+)万元",
    }
    facts = {}
    excerpts = {}
    for key, pattern in patterns.items():
        found = list(re.finditer(pattern, body))
        if len(found) != 1:
            raise ValueError("Missing or ambiguous survey fact: " + key)
        value = found[0].group(1).replace(",", "")
        facts[key] = value
        excerpts[key] = {"start_in_markdown": start + found[0].start(),
                         "end_in_markdown": start + found[0].end(),
                         "source_text": found[0].group(0)}
    date.fromisoformat(facts["publisher_date"])
    reported_decimal(facts["mean_securities_account_assets_10000_cny"])
    return {"facts": facts, "source_excerpts": excerpts, "nominal_survey_year": 2020,
            "fieldwork_start": None, "fieldwork_end": None,
            "sample_scope": "age_18_to_60_traded_SSE_or_SZSE_stocks_in_prior_12_months",
            "market_scope": ["SSE", "SZSE"], "cash_only": False,
            "all_household_wealth": False, "actual_orders_observed": False,
            "individual_risk_preferences_identified": False}


def publication_availability(clock: dict, as_of: datetime, *, mode: str = "strict",
                             verified_publication_bindings: dict[str, str] | None = None) -> dict:
    """Reject future information and unknown dates; label a date proxy as a proxy."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("An as-of cutoff must have a timezone")
    if mode not in {"strict", "declared_date_proxy"}:
        raise ValueError("Unknown availability mode")
    published_day = clock.get("publisher_date")
    timestamp_text = clock.get("first_publication_timestamp")
    timestamp = None
    if timestamp_text is not None:
        timestamp = datetime.fromisoformat(timestamp_text)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Publication timestamp must have a timezone")
        if published_day is not None and timestamp.astimezone(SHANGHAI_TIME).date() != date.fromisoformat(published_day):
            raise ValueError("Publisher day and publication timestamp disagree")
        if timestamp > as_of:
            return {"eligible": False, "reason": "future_publication_timestamp", "point_in_time_certified": False}
    if published_day is None and timestamp is None:
        return {"eligible": False, "reason": "unknown_first_publication_time", "point_in_time_certified": False}
    cutoff_day = as_of.astimezone(SHANGHAI_TIME).date()
    if published_day is not None:
        day = date.fromisoformat(published_day)
        if day > cutoff_day:
            return {"eligible": False, "reason": "future_publisher_date", "point_in_time_certified": False}
        if timestamp is None and day == cutoff_day:
            return {"eligible": False, "reason": "unknown_intraday_publication_time", "point_in_time_certified": False}
    if mode == "declared_date_proxy":
        return {"eligible": True, "reason": "declared_publisher_date_proxy_only", "point_in_time_certified": False}
    if timestamp is None:
        return {"eligible": False, "reason": "unknown_intraday_publication_time", "point_in_time_certified": False}
    if clock.get("original_public_version_verified") is not True:
        return {"eligible": False, "reason": "original_public_version_not_verified", "point_in_time_certified": False}
    bindings = clock.get("publication_evidence_bindings")
    if not bindings:
        return {"eligible": False, "reason": "missing_publication_evidence_bindings", "point_in_time_certified": False}
    if (not isinstance(bindings, dict) or any(
            not isinstance(path, str) or not isinstance(sha, str)
            or re.fullmatch(r"[a-f0-9]{64}", sha) is None
            for path, sha in bindings.items())):
        return {"eligible": False, "reason": "invalid_publication_evidence_bindings", "point_in_time_certified": False}
    # A source's self-declaration is insufficient. The caller must have checked
    # the registered evidence bytes independently before supplying this mapping.
    if (verified_publication_bindings is None or any(
            verified_publication_bindings.get(path) != sha for path, sha in bindings.items())):
        return {"eligible": False, "reason": "publication_evidence_not_independently_verified", "point_in_time_certified": False}
    return {"eligible": True, "reason": "verified_publication_timestamp_and_version", "point_in_time_certified": True}
