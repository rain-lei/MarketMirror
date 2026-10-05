"""Independently verify operating amounts in PDF borders or reviewed bank regions.

Does not import the operating producer, labels, geometry or identity functions.
Balance-table audit primitives are a separate immutable pdfplumber reader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from statistics import median

import pdfplumber

from .audit_issuer_balance_sheets import (ContentTable, independent_cell,
                                          normalized, table_read, reviewed_bank_read, UNIT_FACTORS)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_q3_operating_statements_2019.json"
CANDIDATES = ROOT / "research_outputs/issuer_q3_operating_candidates_2019_v2.json"
OUTPUT = ROOT / "research_outputs/issuer_q3_operating_audit_2019_v2.json"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_label(text):
    value = normalized(text)
    value = re.sub(r"^(?:[一二三四五六七八九十]+[、．.]|\d+[．.、])", "", value)
    for prefix in ("其中：", "其中:", "减：", "减:", "加：", "加:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    cuts = [value.index(marker) for marker in ("（亏", "（净", "（损失") if marker in value]
    return value[:min(cuts)] if cuts else value.rstrip("：:")


def flow_period(text):
    value = normalized(text)
    complete = re.findall(r"(20\d{2})年(\d+)月(\d+)日", value)
    if len(complete) == 2:
        return tuple(f"{int(y):04}-{int(m):02}-{int(d):02}" for y, m, d in complete)
    span = re.findall(r"(20\d{2})年([17])[-—－](9)月", value)
    if len(span) == 1:
        y, first, _ = span[0]
        return y + f"-{int(first):02}-01", y + "-09-30"
    years = re.findall(r"20\d{2}", value)
    if len(set(years)) == 1 and ("前三季度" in value or "第三季度" in value):
        return years[0] + ("-01-01" if "前三季度" in value else "-07-01"), years[0] + "-09-30"
    return None


def column_signature(columns, flow=True):
    keys = ("scope", "period_start", "period_end") if flow else ("scope", "period_end")
    return [{key: col.get(key) for key in keys} for col in columns]


def verified_scope_title(document, candidate):
    first = candidate["heading_evidence"][0]
    lines = document.pages[first["pdf_page"] - 1].extract_text_lines()
    matched = [line for line in lines if abs(line["top"] - first["bbox"][1]) < 15 and
               normalized(line["text"]) == normalized(first["text"])]
    if len(matched) != 1:
        raise ValueError("independent formal statement caption is missing or ambiguous")
    caption = normalized(matched[0]["text"])
    caption = re.sub(r"^(?:（[一二三四五六七八九十]+）[、.]?|\d+[、.．]|[一二三四五六七八九十]+[、.．])", "", caption)
    scope = "combined" if caption.startswith("合并及母公司") or "和" in caption else "consolidated" if caption.startswith("合并") else \
        "parent" if caption.startswith("母公司") else "issuer_as_stated"
    if scope != candidate["declared_heading"]["scope"] or scope == "parent":
        raise ValueError("independent source caption scope disagrees")
    return scope


class LabelTable(ContentTable):
    def extract(self):
        # Only text labels are changed; money and its original presentation stay intact.
        return [[value if independent_cell(value)["status"] in ("NUMERIC", "REPORTED_DASH", "REPORTED_BLANK")
                 else clean_label(value) for value in row] for row in self.values]


def flow_columns(table, count, scope, heading, year):
    rows = table.extract()
    raw = table.values
    date_cells = []
    for row_index, row in enumerate(raw):
        for col, text in enumerate(row):
            p = flow_period(text)
            if p and table.rows[row_index].cells[col] is not None:
                date_cells.append((p, table.rows[row_index].cells[col], text))
        if any("本期发生额" in normalized(v) for v in row):
            if count != 2 or scope == "combined":
                raise ValueError("independent relative period header is unsupported")
            if "年初" in heading:
                first = "01"
            elif "本报告期" in heading:
                first = "07"
            else:
                raise ValueError("independent relative period has no formal flow window")
            relevant = [(normalized(v), rect) for v, rect in zip(row, table.rows[row_index].cells) if rect is not None and
                        ("本期发生额" in normalized(v) or "上期发生额" in normalized(v))]
            if len(relevant) != 2:
                raise ValueError("independent current/prior columns are ambiguous")
            return [{"scope": scope, "period_start": f"{year}-{first}-01" if "本期" in v else None,
                     "period_end": f"{year}-09-30" if "本期" in v else None} for v, _ in relevant]
        # Only actual date headers precede the monetary body.
        if any(independent_cell(v)["status"] == "NUMERIC" for v in row) and not date_cells:
            break
        if date_cells and not any(flow_period(v) for v in row):
            # A merged header's scope row may follow its date row.
            if any(normalized(v) in ("合并数", "公司数") for v in row):
                continue
            if any(independent_cell(v)["status"] == "NUMERIC" for v in row):
                break
    if not date_cells:
        return None
    if scope == "combined":
        tokens = [normalized(v) for row in raw[:5] for v in row if normalized(v) in ("合并数", "公司数")]
        if tokens != ["合并数", "公司数", "合并数", "公司数"] or count != 4:
            raise ValueError("independent merged flow header has no explicit scope order")
        full = next((geometry.cells for geometry in table.rows if len(geometry.cells) == count + 1 and
                     all(rect is not None for rect in geometry.cells)), None)
        if full is None:
            raise ValueError("independent full money rectangles unavailable")
        result = []
        for index, rect in enumerate(full[1:]):
            mid = (rect[0] + rect[2]) / 2
            matches = {p for p, box, _ in date_cells if box[0] < mid < box[2]}
            if len(matches) != 1:
                raise ValueError("independent merged period does not cover one money column")
            p = matches.pop()
            result.append({"scope": "consolidated" if index % 2 == 0 else "parent", "period_start": p[0], "period_end": p[1]})
        return result
    ordered = sorted(date_cells, key=lambda entry: entry[1][0])
    if len(ordered) != count:
        raise ValueError("independent ordinary flow dates are ambiguous")
    return [{"scope": scope, "period_start": p[0], "period_end": p[1]} for p, _, _ in ordered]


def flow_bounds(document, candidate, page_number):
    first = candidate["heading_evidence"][0]
    page = document.pages[page_number - 1]
    lo = max(0, first["bbox"][1] - 4) if page_number == first["pdf_page"] else 0
    hi = page.height
    # Independently stop before a later formal statement caption on the same page.
    captions = page.extract_text_lines()
    for line in captions:
        text = normalized(line["text"])
        text = re.sub(r"^(?:（[一二三四五六七八九十]+）[、.]?|\d+[、.．]|[一二三四五六七八九十]+[、.．])", "", text)
        is_selected_caption = (page_number == first["pdf_page"] and
                               normalized(line["text"]) == normalized(first["text"]) and abs(line["top"] - first["bbox"][1]) < 15)
        if is_selected_caption or line["top"] <= lo + 2 or text.endswith("（续）"):
            continue
        if re.fullmatch(r"(?:合并及母公司|合并|母公司)?(?:本报告期|年初到报告期末|年初至报告期末)?(?:利润表|现金流量表|资产负债表)(?:和(?:利润表|现金流量表|资产负债表))?", text):
            hi = min(hi, line["top"] - 2)
    return lo, hi


def border_layout(table, count):
    complete = []
    for values, row in zip(table.values, table.rows):
        numeric = [box for value, box in zip(values, row.cells) if box is not None and independent_cell(value)["status"] == "NUMERIC"]
        if len(numeric) == count:
            complete.append(numeric)
    if complete:
        return [median(r[0][0] for r in complete)] + [median(r[i][2] for r in complete) for i in range(count - 1)] + [table.bbox[2]]
    full = [row.cells for row in table.rows if len(row.cells) == count + 1 and all(box is not None for box in row.cells)]
    if full:
        return [median(r[i][2] for r in full) for i in range(count + 1)]
    return None


def logical_border_read(document, chunks, labels, count):
    """Read vertical logical rows through their actual independent border boxes.

    A currency rectangle may be split into several PDF table rows while its
    label spans them. Loss qualifiers and cross-page label continuations are
    grouped using the printed left cells; amounts come from fresh border-region
    reads, not the producer's word coordinates or numeric row indexes.
    """
    reverse = {normalized(a): f for f, aliases in labels.items() for a in aliases}
    reverse.update({"持续经营净利润": "_guard_continuing", "终止经营净利润": "_guard_discontinued"})
    found = {f: [] for f in labels}
    index = 0
    while index < len(chunks):
        first = chunks[index]
        raw, cleaned, end = first["text"], clean_label(first["text"]), index
        if not cleaned or not any(a.startswith(cleaned) for a in reverse):
            index += 1
            continue
        while end + 1 < len(chunks) and end < index + 8:
            exact = cleaned in reverse
            open_qualifier = raw.count("（") + raw.count("(") > raw.count("）") + raw.count(")")
            if exact and not open_qualifier:
                break
            following = chunks[end + 1]
            previous = chunks[end]
            bridge = (following["pdf_page"] == previous["pdf_page"] + 1 and
                      previous["box"][3] > previous["page_height"] * .7 and following["box"][1] < following["page_height"] * .2)
            if following["pdf_page"] != previous["pdf_page"] and not bridge:
                break
            candidate_raw = raw + following["text"]
            candidate_clean = clean_label(candidate_raw)
            if not any(a.startswith(candidate_clean) for a in reverse):
                break
            raw, cleaned, end = candidate_raw, candidate_clean, end + 1
        field = reverse.get(cleaned)
        if field is None:
            index += 1
            continue
        group = chunks[index:end + 1]
        if not field.startswith("_guard_"):
            texts = [[] for _ in range(count)]
            for number in sorted({c["pdf_page"] for c in group}):
                portion = [c for c in group if c["pdf_page"] == number]
                if any(c["boundaries"] != portion[0]["boundaries"] for c in portion):
                    raise ValueError("one logical field crosses inconsistent independent border layouts")
                bounds = portion[0]["boundaries"]
                lo, hi = min(c["box"][1] for c in portion), max(c["box"][3] for c in portion)
                page = document.pages[number - 1]
                for col in range(count):
                    # Some PDF vector cuts run through the last digit or a loss
                    # qualifier. Assign complete monetary tokens by their center
                    # to the independently established border regions; do not
                    # clip glyphs and concatenate a stray digit into the next cell.
                    words = [w for w in page.extract_words(x_tolerance=1, y_tolerance=1)
                             if bounds[col] < (w["x0"] + w["x1"]) / 2 <= bounds[col + 1] and
                             lo <= (w["top"] + w["bottom"]) / 2 <= hi and
                             independent_cell(w["text"])["status"] in ("NUMERIC", "REPORTED_DASH")]
                    texts[col].extend(w["text"] for w in words)
            cells = []
            for pieces in texts:
                cells.append(independent_cell(pieces[0] if pieces else "") if len(pieces) <= 1 else
                             {"status": "AMBIGUOUS_INDEPENDENT_BORDER_AMOUNT", "reported_value": None, "source_cell_text": pieces})
            found[field].append({"pdf_page": first["pdf_page"], "source_label": raw, "independent_label_border_spans": group, "cells": cells})
        index = end + 1
    return found


def table_flow_read(document, candidate, labels, year):
    reverse = {normalized(alias): f for f, aliases in labels.items() for alias in aliases}
    reverse.update({"持续经营净利润": "_guard_continuing", "终止经营净利润": "_guard_discontinued"})
    mappings, units, headings, chunks = [], [], [], []
    count = len(candidate["column_mapping"]["columns"])
    for number in range(candidate["start_pdf_page"], candidate["end_pdf_page"] + 1):
        page = document.pages[number - 1]
        lo, hi = flow_bounds(document, candidate, number)
        if hi <= lo:
            raise ValueError("independent statement bounds are empty")
        cropped = page.crop((0, lo, page.width, hi))
        text = cropped.extract_text(layout=False) or ""
        headings.extend(text.splitlines()[:20])
        units.extend({"pdf_page": number, "reported_unit": u} for u in re.findall(
            r"(?:货币|金额)?单位(?:[:：]|为)(?:人民币)?(百万元|千元|万元|亿元|元)", normalized(text)))
        for raw_table in cropped.find_tables():
            table = LabelTable(raw_table)
            if not table.values:
                continue
            mapping = flow_columns(table, count, candidate["declared_heading"]["scope"], candidate["heading_evidence"][0]["text"], year)
            if mapping:
                mappings.append({"pdf_page": number, "columns": mapping, "table_bbox": list(table.bbox)})
            boundaries = border_layout(table, count)
            if boundaries is None:
                if any(clean_label(value) in reverse for row in table.values for value in row):
                    raise ValueError("independent selected border table has no currency geometry")
                continue
            for values, row in zip(table.values, table.rows):
                left = [(value, box) for value, box in zip(values, row.cells) if normalized(value) and box is not None and
                        (box[0] + box[2]) / 2 < boundaries[0]]
                if left:
                    chunks.append({"pdf_page": number, "page_height": page.height, "text": "".join(value for value, _ in left),
                                   "box": [min(b[0] for _, b in left), min(b[1] for _, b in left), max(b[2] for _, b in left), max(b[3] for _, b in left)],
                                   "boundaries": boundaries})
    readings = logical_border_read(document, chunks, labels, count)
    return {"fields": readings, "mappings": mappings, "unit_evidence": units, "heading_evidence_text": headings,
            "method": "independent_pdfplumber_vector_border_operating_tables"}


def bank_flow_read(document, candidate, labels, anchor):
    count = len(anchor["columns_reviewed_left_to_right"])
    readings = {f: [] for f in labels}
    reverse = {normalized(alias): f for f, aliases in labels.items() for alias in aliases}
    mappings, units, headings = [], [], []
    for number in anchor["complete_pdf_pages_reviewed"]:
        page = document.pages[number - 1]
        boundaries = anchor["numeric_column_boundaries_by_page"][str(number)]
        text = page.extract_text(layout=False) or ""
        headings.extend(text.splitlines()[:20])
        units.extend({"pdf_page": number, "reported_unit": u} for u in re.findall(
            r"(?:货币|金额)?单位(?:[:：]|为)(?:人民币)?(百万元|千元|万元|亿元|元)", normalized(text)))
        all_words = page.extract_words(x_tolerance=1, y_tolerance=1)
        groups = []
        for word in sorted((w for w in all_words if (w["x0"] + w["x1"]) / 2 < boundaries[0]), key=lambda w: (w["top"], w["x0"])):
            center = (word["top"] + word["bottom"]) / 2
            if not groups or abs(center - median((w["top"] + w["bottom"]) / 2 for w in groups[-1])) > 3:
                groups.append([word])
            else:
                groups[-1].append(word)
        lines = [{"text": "".join(w["text"] for w in sorted(g, key=lambda w: w["x0"])),
                  "top": min(w["top"] for w in g), "bottom": max(w["bottom"] for w in g)} for g in groups]
        index = 0
        first_body_top = None
        while index < len(lines):
            line = lines[index]
            label = clean_label(line["text"])
            end = index
            while label not in reverse and any(a.startswith(label) for a in reverse) and end + 1 < len(lines) and end < index + 4:
                following = lines[end + 1]
                if following["top"] - lines[end]["bottom"] > 18:
                    break
                combined = label + clean_label(following["text"])
                if not any(a.startswith(combined) for a in reverse):
                    break
                label = combined
                end += 1
            if label in reverse:
                field = reverse[label]
                first_body_top = min(first_body_top, line["top"]) if first_body_top is not None else line["top"]
                y0 = (line["top"] + line["bottom"]) / 2 - 3.5
                y1 = (lines[end]["top"] + lines[end]["bottom"]) / 2 + 3.5
                cells = []
                for col in range(count):
                    words = [w["text"] for w in all_words
                             if boundaries[col] < (w["x0"] + w["x1"]) / 2 < boundaries[col + 1] and
                             y0 <= (w["top"] + w["bottom"]) / 2 <= y1]
                    cells.append(independent_cell(words[0] if words else "") if len(words) <= 1 else
                                 {"status": "AMBIGUOUS_INDEPENDENT_BANK_AMOUNT", "reported_value": None, "source_cell_text": words})
                readings[field].append({"pdf_page": number, "source_label": label, "source_label_lines": lines[index:end + 1],
                                        "reviewed_numeric_boundaries": boundaries, "cells": cells})
            index = end + 1
        if first_body_top is not None:
            columns = []
            for col, reviewed in enumerate(anchor["columns_reviewed_left_to_right"]):
                region = anchor["period_header_regions_by_page"][str(number)][col]
                head = page.crop(region).extract_text(layout=False)
                actual = flow_period(head)
                if actual != (reviewed["period_start"], reviewed["period_end"]):
                    raise ValueError("reviewed bank actual period header disagrees: " + str(number) + ":" + str(col))
                columns.append(dict(reviewed))
            mappings.append({"pdf_page": number, "columns": columns, "basis": "complete_page_reviewed_regions_and_fresh_period_text"})
        page.close()
    return {"fields": readings, "mappings": mappings, "unit_evidence": units, "heading_evidence_text": headings,
            "method": "independent_pdfplumber_reviewed_bank_regions"}


def independent_identities(fields, count, kind):
    results = []
    for index in range(count):
        if kind == "利润表":
            tax = fields["income_tax_expense"][index]
            tax_sign = 1 if normalized(tax.get("source_cell_text")).startswith("（") else -1
            definitions = [("profit_total_tax_net", [("profit_total", 1), ("income_tax_expense", tax_sign), ("net_profit", -1)]),
                           ("parent_plus_minority_profit", [("net_profit_attributable_to_parent", 1), ("minority_profit", 1), ("net_profit", -1)])]
        else:
            definitions = []
            for channel in ("operating", "investing", "financing"):
                outflow = fields[channel + "_cash_outflow"][index]
                sign = 1 if normalized(outflow.get("source_cell_text")).startswith("（") else -1
                definitions.append((channel + "_inflow_minus_outflow", [(channel + "_cash_inflow", 1), (channel + "_cash_outflow", sign), (channel + "_net_cashflow", -1)]))
            definitions.extend([("opening_plus_change_closing", [("cash_equivalent_opening", 1), ("cash_equivalent_net_change", 1), ("cash_equivalent_closing", -1)]),
                                ("three_activities_plus_fx_change", [("operating_net_cashflow", 1), ("investing_net_cashflow", 1), ("financing_net_cashflow", 1), ("fx_effect_on_cash", 1), ("cash_equivalent_net_change", -1)])])
        for name, terms in definitions:
            source = [fields[f][index] for f, _ in terms]
            if any(c["status"] != "NUMERIC" for c in source):
                results.append({"name": name, "column_index": index, "status": "NOT_CHECKABLE_MISSING_SOURCE_VALUE"})
                continue
            values = [Decimal(c["reported_value"]) for c in source]
            residual = sum((v * sign for v, (_, sign) in zip(values, terms)), Decimal(0))
            precision = max(Decimal(1).scaleb(v.as_tuple().exponent) for v in values)
            tolerance = precision * len(terms) / 2
            results.append({"name": name, "column_index": index, "status": "EXACT" if residual == 0 else
                            "WITHIN_REPORTED_ROUNDING" if abs(residual) <= tolerance else "IDENTITY_MISMATCH",
                            "residual_reported_unit": str(residual)})
    return results


def compare(candidate, read, labels, kind):
    issues, checked, values = [], 0, {}
    columns = column_signature(candidate["column_mapping"]["columns"], kind != "additional_assets")
    if not read["mappings"] or any(column_signature(m["columns"], kind != "additional_assets") != columns for m in read["mappings"]):
        issues.append("INDEPENDENT_COLUMN_MAPPING_DISAGREES")
    if {r["reported_unit"] for r in read["unit_evidence"]} != {candidate["reported_unit"]}:
        issues.append("INDEPENDENT_UNIT_DISAGREES")
    for field in labels:
        found = read["fields"][field]
        wanted = candidate["fields"][field]
        if len(found) > 1:
            issues.append(field + ":MULTIPLE_INDEPENDENT_ROWS")
            continue
        if not found:
            if wanted["status"] != "ROW_ABSENT":
                issues.append(field + ":INDEPENDENT_ROW_MISSING")
            values[field] = [{"status": "ROW_ABSENT", "reported_value": None} for _ in columns]
            continue
        values[field] = cells = found[0]["cells"]
        if len(cells) != len(columns):
            issues.append(field + ":CELL_COUNT_DISAGREES")
            continue
        for index, (actual, target) in enumerate(zip(cells, wanted["cells"])):
            checked += 1
            if actual["status"] != target["status"]:
                issues.append(f"{field}:{index}:CELL_STATUS_DISAGREES")
            elif actual["status"] == "NUMERIC":
                if Decimal(actual["reported_value"]) != Decimal(target["reported_value"]):
                    issues.append(f"{field}:{index}:NUMBER_DISAGREES")
                if Decimal(actual["reported_value"]) * UNIT_FACTORS[candidate["reported_unit"]] != Decimal(target["value_yuan"]):
                    issues.append(f"{field}:{index}:UNIT_NORMALIZATION_DISAGREES")
    checks = []
    if kind != "additional_assets" and set(values) == set(labels):
        checks = independent_identities(values, len(columns), kind)
        if any(c["status"] == "IDENTITY_MISMATCH" for c in checks):
            issues.append("INDEPENDENT_SOURCE_IDENTITY_FAILED")
        wanted_checks = {(c["name"], c["column_index"]): c["status"] for c in candidate["identity_checks"]}
        if any(wanted_checks.get((c["name"], c["column_index"])) != c["status"] for c in checks):
            issues.append("INDEPENDENT_IDENTITY_STATUS_DISAGREES")
        current = [i for i, col in enumerate(columns) if col["scope"] != "parent" and col["period_start"] == "2019-01-01" and col["period_end"] == "2019-09-30"]
        if current != [candidate["current_ytd_column_index"]]:
            issues.append("INDEPENDENT_CURRENT_YTD_SELECTION_DISAGREES")
    return {"status": "PASS" if not issues else "FAIL", "issues": issues, "cells_checked": checked,
            "independent_identity_checks": checks, "independent_reading": read}


def compute(source):
    protocol = json.loads(CONFIG.read_text(encoding="utf-8"))
    candidates = json.loads(source.read_text(encoding="utf-8"))
    balances = json.loads((ROOT / protocol["balance_candidates"]).read_text(encoding="utf-8"))
    by_code = {c["stock_code"]: c for c in balances["companies"]}
    reviews_path = ROOT / protocol["reviewed_flow_anchors"]
    reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    by_pdf = {r["source_pdf_sha256"]: r for r in reviews["sources"]}
    balance_reviews = json.loads((ROOT / protocol["bank_balance_review"]).read_text(encoding="utf-8"))
    bank_balances = {r["source_pdf_sha256"]: r for r in balance_reviews["sources"]}
    bs_labels = json.loads((ROOT / "research/configs/issuer_q3_balance_sheets_2019.json").read_text(encoding="utf-8"))["field_labels"]
    bindings = {str(source): digest(source), str(reviews_path): digest(reviews_path), **candidates["inputs"], **candidates["code_sha256"],
                str(Path(__file__).resolve()): digest(__file__),
                str(ROOT / "research/data_pipeline/audit_issuer_balance_sheets.py"): digest(ROOT / "research/data_pipeline/audit_issuer_balance_sheets.py")}

    def verify():
        for path, expected in bindings.items():
            if digest(path) != expected:
                raise ValueError("independent operating source or producer changed: " + path)

    verify()
    codes = [c["stock_code"] for c in candidates["companies"]]
    if len(codes) != len(set(codes)) or set(codes) != set(by_code) or len(codes) != protocol["expected_companies"]:
        raise ValueError("independent operating cohort changed")
    reports = []
    for n, company in enumerate(candidates["companies"], 1):
        original = by_code[company["stock_code"]]
        if any(company[k] != original[k] for k in ("source_pdf_sha256", "source_pdf_path", "report_metadata", "industry_code", "historical_short_name")):
            raise ValueError("operating issuer/source/period/availability association changed")
        record = {"stock_code": company["stock_code"], "source_pdf_sha256": company["source_pdf_sha256"], "statements": {}}
        with pdfplumber.open(ROOT / company["source_pdf_path"]) as document:
            for kind, key in (("利润表", "profit"), ("现金流量表", "cashflow")):
                part = company["flows"][kind]
                try:
                    if part["status"] != "UNIQUE_SOURCE_DATED_YTD_CANDIDATE":
                        raise ValueError("no unique operating source selection")
                    chosen = part["candidates"][part["selected_candidate_index"]]
                    scope = verified_scope_title(document, chosen)
                    anchor = by_pdf.get(company["source_pdf_sha256"])
                    if anchor and (anchor["stock_code"] != company["stock_code"] or anchor["source_pdf_path"] != company["source_pdf_path"]):
                        raise ValueError("reviewed bank anchor belongs to another issuer/source")
                    read = bank_flow_read(document, chosen, protocol["field_labels"][key], anchor["statements"][kind]) if anchor else \
                        table_flow_read(document, chosen, protocol["field_labels"][key], protocol["report_year"])
                    record["statements"][kind] = compare(chosen, read, protocol["field_labels"][key], kind)
                    record["statements"][kind].update(selected_candidate_index=part["selected_candidate_index"], source_caption_scope=scope)
                except (ValueError, IndexError, StopIteration) as error:
                    record["statements"][kind] = {"status": "FAIL", "issues": [str(error)]}
            try:
                selected_bs = original["candidates"][original["selected_candidate_index"]]
                labels = {**bs_labels, **protocol["field_labels"]["additional_assets"]}
                anchor = bank_balances.get(company["source_pdf_sha256"])
                read = reviewed_bank_read(document, selected_bs, labels, anchor) if anchor else table_read(document, selected_bs, labels)
                read["fields"] = {f: read["fields"][f] for f in protocol["field_labels"]["additional_assets"]}
                record["statements"]["additional_assets"] = compare(company["additional_assets"], read, protocol["field_labels"]["additional_assets"], "additional_assets")
            except (ValueError, IndexError, StopIteration) as error:
                record["statements"]["additional_assets"] = {"status": "FAIL", "issues": [str(error)]}
        record["status"] = "PASS" if all(s["status"] == "PASS" for s in record["statements"].values()) else "FAIL"
        reports.append(record)
        if n % 20 == 0:
            print(f"Independently read operating tables {n}/{len(codes)}.", flush=True)
    verify()
    summary = {"companies": len(reports), "company_status_counts": dict(Counter(r["status"] for r in reports)),
               "statement_status_counts": {kind: dict(Counter(r["statements"][kind]["status"] for r in reports)) for kind in ("利润表", "现金流量表", "additional_assets")},
               "financial_cells_checked": sum(s.get("cells_checked", 0) for r in reports for s in r["statements"].values()),
               "independent_identity_counts": dict(Counter(c["status"] for r in reports for s in r["statements"].values() for c in s.get("independent_identity_checks", [])))}
    return {"pipeline_version": "independent-operating-border-regions-and-reviewed-bank-audit-v2", "reports": reports, "summary": summary,
            "status": "PASS" if all(r["status"] == "PASS" for r in reports) else "FAIL", "inputs": dict(sorted(bindings.items())),
            "code_sha256": digest(__file__), "pdfplumber_version": pdfplumber.__version__, "agent_signal_enabled": False,
            "interpretation": "Independent original-PDF border regions with complete pdfplumber monetary tokens, separately reviewed bank regions, verified formal source captions, period/unit and signed identities. Vector cuts cannot clip/concatenate digits. Null cells remain uncheckable, not zero. Automated agreement does not establish predictive increment, causal effect or unchanged historical bytes."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    result = compute(args.candidates)
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if args.output.read_bytes() != raw:
            raise ValueError("independent operating re-read differs from frozen audit")
        print("Independent operating audit rebuilt byte-identically.")
    else:
        with args.output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(result["summary"], ensure_ascii=False))
    for report in result["reports"]:
        for kind, statement in report["statements"].items():
            if statement["status"] != "PASS":
                print("QC", report["stock_code"], kind, statement["issues"])
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
