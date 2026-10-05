"""Locate literal business/region evidence; never infer quantified exposure."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def heading_kind(text):
    value = compact(text)
    value = re.sub(r"^(?:\(?[一二三四五六七八九十\d]+\)?[、.．]?)", "", value)
    value = re.sub(r"\(续\)$", "", value)
    if value in {"报告期内公司从事的主要业务", "公司主要业务", "主要业务、产品和经营模式", "主营业务情况"}:
        return "MAIN_BUSINESS_HEADING"
    if value in {"分地区", "分区域", "主营业务分地区情况", "营业收入分地区", "营业收入分地区情况", "地区分部"}:
        return "GEOGRAPHIC_REVENUE_OR_SEGMENT_HEADING"
    if value in {"营业收入构成", "营业收入构成及变动情况", "主营业务构成情况", "主营业务分行业、分产品、分地区情况"}:
        return "REVENUE_COMPOSITION_HEADING"
    if value in {"所有权或使用权受到限制的资产", "所有权或使用权受限制的资产", "受限货币资金", "使用受到限制的货币资金"}:
        return "ASSET_RESTRICTION_HEADING"
    return None


def locate_evidence(page_texts, terms):
    if not page_texts or any(not isinstance(t, str) for t in page_texts) or not terms or len(set(terms)) != len(terms):
        raise ValueError("business evidence requires page strings and unique fixed literal terms")
    headings, region_mentions = [], []
    literal_pages = {term: [] for term in terms}
    for page_number, text in enumerate(page_texts, 1):
        normalized = compact(text)
        for term in terms:
            if compact(term) in normalized:
                literal_pages[term].append(page_number)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            kind = heading_kind(line)
            if kind:
                before, after = max(0, i - 12), min(len(lines), i + 45)
                headings.append({"kind": kind, "pdf_page": page_number, "line_number": i + 1,
                                 "text": line, "context_first_line_number": before + 1,
                                 "context_last_line_number": after, "context_lines": lines[before:after],
                                 "scope_verified": False, "value_column_verified": False, "quantified_exposure": None})
            matched = [term for term in ("湖北", "武汉") if term in compact(line)]
            if matched:
                before, after = max(0, i - 5), min(len(lines), i + 10)
                region_mentions.append({"kind": "REGION_LITERAL_CONTEXT_ONLY", "pdf_page": page_number,
                                        "line_number": i + 1, "text": line, "matched_terms": matched,
                                        "context_first_line_number": before + 1, "context_last_line_number": after,
                                        "context_lines": lines[before:after], "economic_relationship_verified": False,
                                        "quantified_exposure": None, "shock_direction": "unknown"})
    status = "TEXT_EVIDENCE_REVIEW_REQUIRED" if headings or region_mentions else "NO_LITERAL_EVIDENCE_CANDIDATE"
    return {"review_queue_status": status, "heading_counts": dict(Counter(h["kind"] for h in headings)),
            "heading_candidates": headings, "region_literal_contexts": region_mentions,
            "literal_term_pages": literal_pages, "business_exposure_values_verified": False,
            "quantified_exposure": None, "agent_signal_enabled": False,
            "interpretation": "Original-page evidence pointers only. Registered location, subsidiaries, guarantees, customer geography, operating geography and revenue coverage require separate review. Literal absence is not zero exposure."}
