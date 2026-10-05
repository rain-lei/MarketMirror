"""Parse physical CSRC table cells without inventing missing industry labels."""

from __future__ import annotations

import argparse
import json
import math
import re
import tempfile
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .fetch_csrc_industry_2019q3 import PAGE_URL, publication_and_attachment
from .provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_industry_2019q3.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_industry_2019q3_v1"
SCHEMA = ROOT / "research/data_contracts/industry_membership.schema.json"
VERSION = "pre-wuhan-official-industry-dictionary-v1"
HEADER = ("门类名称及代码", "行业大类代码", "行业大类名称", "上市公司代码", "上市公司简称")
LOCAL_ZONE = timezone(timedelta(hours=8))


def compact(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("table text must be a string")
    return re.sub(r"\s+", "", value)


def decode_table(page: int, values: list[list], cells: list[list]) -> list[dict]:
    """Resolve merged labels only from a cell covering the security's entire row.

    Each page is independent: an absent first label cannot inherit another page's
    last category. Original wrapped text and cell coordinates remain traceable.
    """
    if (type(page) is not int or page < 1 or len(values) < 2 or len(values) != len(cells)
            or any(len(row) != 5 for row in values + cells)
            or tuple(compact(value) for value in values[0]) != HEADER):
        raise ValueError("industry table shape or repeated header differs")
    physical = [[] for _ in range(5)]
    for row_values, row_cells in zip(values[1:], cells[1:]):
        for column, (text, bbox) in enumerate(zip(row_values, row_cells)):
            if bbox is None:
                if text is not None:
                    raise ValueError("text exists without a physical table cell")
                continue
            if (len(bbox) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in bbox)
                    or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
                raise ValueError("invalid physical cell coordinates")
            if not compact(text):
                raise ValueError("blank physical table cell")
            physical[column].append((tuple(bbox), text))
    records = []
    for index in range(1, len(values)):
        security_cell = cells[index][3]
        name_cell = cells[index][4]
        if security_cell is None or name_cell is None or security_cell[1::2] != name_cell[1::2]:
            raise ValueError("security and name must have separate aligned row cells")
        top, bottom = security_cell[1], security_cell[3]
        resolved = []
        for column in range(5):
            left, right = cells[0][column][0], cells[0][column][2]
            covering = [(bbox, text) for bbox, text in physical[column]
                        if abs(bbox[0] - left) < 0.05 and abs(bbox[2] - right) < 0.05
                        and bbox[1] <= top + 0.05 and bbox[3] >= bottom - 0.05]
            if len(covering) != 1:
                raise ValueError("missing or ambiguous merged label coverage")
            resolved.append(covering[0])
        texts = [text for _, text in resolved]
        normalized = [compact(text) for text in texts]
        category = re.fullmatch(r"(.+)\(([A-S])\)", normalized[0])
        if not re.fullmatch(r"\d{2}", normalized[1]):
            raise ValueError("industry division code must retain two digits")
        if not re.fullmatch(r"\d{6}", normalized[3]):
            raise ValueError("security code must retain six digits")
        issues = [] if category else ["incomplete_source_category_and_division_label"]
        records.append({
            "security_code": normalized[3], "historical_short_name": normalized[4],
            "category_code": category[2] if category else None,
            "category_name": category[1] if category else None,
            "division_code": normalized[1], "division_name": normalized[2] if category else None,
            "industry_code": category[2] + normalized[1] if category else None,
            "status": "verified_table_label" if category else "source_label_incomplete",
            "issues": issues, "source_text": texts,
            "source_location": {"pdf_page": page, "table_index": 1, "table_row": index + 1,
                                "column_bboxes": [list(bbox) for bbox, _ in resolved]},
            "impact_direction": "unknown", "impact_magnitude": None, "agent_signal_enabled": False,
        })
    return records


def validate_records(records: list[dict]) -> None:
    if not records:
        raise ValueError("industry dictionary must contain security rows")
    codes = [row["security_code"] for row in records]
    if len(codes) != len(set(codes)):
        raise ValueError("duplicate security code in official table")
    categories, divisions = {}, {}
    for row in records:
        if row["status"] not in {"verified_table_label", "source_label_incomplete"}:
            raise ValueError("unknown source quality status")
        if row["agent_signal_enabled"] is not False or row["impact_direction"] != "unknown" or row["impact_magnitude"] is not None:
            raise ValueError("industry membership cannot invent an economic impact signal")
        if row["status"] != "verified_table_label":
            if any(row[key] is not None for key in ("category_code", "category_name", "industry_code", "division_name")):
                raise ValueError("incomplete source label must remain unassigned")
            continue
        category = row["category_code"]
        division = row["division_code"]
        if categories.setdefault(category, row["category_name"]) != row["category_name"]:
            raise ValueError("category name differs across physical cells")
        if divisions.setdefault(division, (category, row["division_name"])) != (category, row["division_name"]):
            raise ValueError("division mapping differs across physical cells")
        if row["industry_code"] != category + division:
            raise ValueError("industry code differs from explicit table labels")


def map_cohort(records: list[dict], codes: list[str], available_at: str, earliest_cutoff: str) -> list[dict]:
    validate_records(records)
    if (not codes or codes != sorted(set(codes))
            or any(not isinstance(code, str) or not re.fullmatch(r"\d{6}", code) for code in codes)):
        raise ValueError("cohort requires sorted unique six-digit strings")
    available = datetime.fromisoformat(available_at)
    cutoff = datetime.fromisoformat(earliest_cutoff)
    if available.tzinfo is None or cutoff.tzinfo is None:
        raise ValueError("industry availability and cutoff need time zones")
    if available > cutoff:
        raise ValueError("industry source is not available at the first model cutoff")
    dictionary = {row["security_code"]: row for row in records}
    mapped = []
    for code in codes:
        row = dictionary.get(code)
        mapped.append({"stock_code": code,
                       "historical_short_name": row["historical_short_name"] if row else None,
                       "industry_code": row["industry_code"] if row else None,
                       "category_code": row["category_code"] if row else None,
                       "division_code": row["division_code"] if row else None,
                       "division_name": row["division_name"] if row else None,
                       "status": row["status"] if row else "missing_official_security_row",
                       "source_location": row["source_location"] if row else None,
                       "available_at_proxy": available_at, "earliest_signal_cutoff": earliest_cutoff,
                       "impact_direction": "unknown", "impact_magnitude": None, "agent_signal_enabled": False})
    return mapped


def load_development(config: dict) -> tuple[dict, dict[str, str]]:
    source = (CONFIG.parent / config["development_archive"]).resolve()
    manifest_path = source.parent / "manifest.json"
    if (file_sha256(source) != config["development_archive_sha256"]
            or file_sha256(manifest_path) != config["development_manifest_sha256"]):
        raise ValueError("frozen development archive or manifest hash differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs = {str(source): file_sha256(source), str(manifest_path): file_sha256(manifest_path)}
    for name, metadata in manifest["artifacts"].items():
        path = source.parent / name
        digest = metadata["sha256"]
        if path.parent != source.parent or file_sha256(path) != digest:
            raise ValueError("development artifact hash differs")
        inputs[str(path)] = digest
    result = json.loads(source.read_text(encoding="utf-8"))["result"]
    if result["pipeline_version"] != "pre-wuhan-background-news-liquidity-2x2-v1":
        raise ValueError("development archive version differs")
    sample = result["sample"]
    if (sample["companies"], sample["sessions"], sample["company_days_per_variant"]) != (123, 43, 5289):
        raise ValueError("frozen development cohort coverage differs")
    return result, inputs


def compute() -> tuple[dict, dict]:
    import pdfplumber

    if pdfplumber.__version__ != "0.11.10":
        raise ValueError("use the pinned PDF extraction runtime in requirements-industry.txt")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    directory = (CONFIG.parent / config["source_directory"]).resolve()
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs = {str(CONFIG): file_sha256(CONFIG), str(SCHEMA): file_sha256(SCHEMA),
              str(manifest_path): file_sha256(manifest_path)}
    for name, expected in (("release_page.html", config["release_page_sha256"]), ("industry_2019q3.pdf", config["pdf_sha256"])):
        path = directory / name
        if file_sha256(path) != expected or manifest["artifacts"][name]["sha256"] != expected:
            raise ValueError("official source file or manifest hash differs")
        inputs[str(path)] = expected
    page = (directory / "release_page.html").read_text(encoding="utf-8")
    publication_date, pdf_url = publication_and_attachment(page)
    available = datetime.combine(date.fromisoformat(publication_date) + timedelta(days=1), time(), LOCAL_ZONE).isoformat()
    if (publication_date != config["publication_date"] or available != config["source_available_at_proxy"]
            or manifest["publication_date"] != publication_date or manifest["available_at_proxy"] != available
            or manifest["classification_period"] != config["classification_period"]
            or manifest["artifacts"]["industry_2019q3.pdf"]["url"] != pdf_url
            or manifest["artifacts"]["release_page.html"]["url"] != PAGE_URL):
        raise ValueError("official source publication or availability identity differs")
    records = []
    page_counts = []
    with pdfplumber.open(directory / "industry_2019q3.pdf") as document:
        if len(document.pages) != config["expected_pdf_pages"]:
            raise ValueError("official PDF page count differs")
        for index, page in enumerate(document.pages, 1):
            tables = page.find_tables()
            if len(tables) != 1:
                raise ValueError("each official page must contain one physical table")
            parsed = decode_table(index, tables[0].extract(), [row.cells for row in tables[0].rows])
            records.extend(parsed)
            page_counts.append({"pdf_page": index, "security_rows": len(parsed)})
    validate_records(records)
    if len(records) != config["expected_security_rows"]:
        raise ValueError("official security row count differs")
    records.sort(key=lambda row: row["security_code"])
    development, archived_inputs = load_development(config)
    inputs.update(archived_inputs)
    variants = development["variants"]
    dates = sorted({row["signal_cutoff_date"] for row in variants[0]["daily_asset_rows"]})
    earliest = datetime.combine(date.fromisoformat(dates[0]), time(), LOCAL_ZONE).isoformat()
    mapped = map_cohort(records, development["sample"]["selected_stock_codes"], available, earliest)
    counts = Counter(row["industry_code"] for row in mapped if row["status"] == "verified_table_label")
    catalog = {
        "pipeline_version": VERSION, "data_kind": "historical_official_industry_membership",
        "classification_period": config["classification_period"],
        "source": {"publisher": "中国证券监督管理委员会", "release_page_url": PAGE_URL,
                   "pdf_url": pdf_url, "pdf_sha256": config["pdf_sha256"], "pdf_pages": len(page_counts),
                   "publication_date": publication_date, "available_at_proxy": available,
                   "date_basis": "official_page_date_next_day_conservative_proxy",
                   "retrieved_at": manifest["retrieved_at"], "pdf_extractor": "pdfplumber==0.11.10"},
        "coverage": {"security_rows": len(records), "security_rows_verified": sum(row["status"] == "verified_table_label" for row in records),
                     "source_qc_rows": sum(row["status"] != "verified_table_label" for row in records),
                     "cohort_companies": len(mapped), "cohort_verified": sum(row["status"] == "verified_table_label" for row in mapped),
                     "cohort_unassigned": sum(row["status"] != "verified_table_label" for row in mapped),
                     "industry_divisions_in_cohort": len(counts), "earliest_signal_cutoff": earliest},
        "page_counts": page_counts, "security_memberships": records, "cohort_memberships": mapped,
        "cohort_industry_counts": dict(sorted(counts.items())),
        "limitations": manifest["limitations"] + [
            "Security rows include both A and B shares; unique security codes are not a count of unique issuers.",
            "The cohort was selected retrospectively from 2020 Q&A activity; no new blind validation cohort is created.",
            "Incomplete source labels remain null and require QC; no security-code-based industry inference or present-day lookup is used.",
            "Industry codes are grouping information; response direction, magnitude and information coverage remain unverified.",
        ],
    }
    provenance = {"inputs": dict(sorted(inputs.items())),
                  "code_sha256": {str(Path(__file__).resolve()): file_sha256(Path(__file__)),
                                  str(ROOT / "research/data_pipeline/fetch_csrc_industry_2019q3.py"): file_sha256(ROOT / "research/data_pipeline/fetch_csrc_industry_2019q3.py"),
                                  str(ROOT / "research/data_pipeline/provenance.py"): file_sha256(ROOT / "research/data_pipeline/provenance.py")}}
    return catalog, provenance


def encoded(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("industry dictionary requires a direct research_outputs directory")
    catalog, provenance = compute()
    files = {"industry_memberships.json": encoded(catalog), "provenance.json": encoded(provenance)}
    if args.audit_existing:
        if any((output / name).read_bytes() != data for name, data in files.items()):
            raise ValueError("archived industry dictionary differs byte-for-byte from recomputation")
    else:
        if output.exists():
            raise ValueError("choose a fresh industry dictionary directory")
        with tempfile.TemporaryDirectory(prefix="industry-dictionary-stage-", dir=output.parent) as temporary:
            stage = Path(temporary)
            for name, data in files.items():
                (stage / name).write_bytes(data)
            stage.replace(output)
    print(json.dumps(catalog["coverage"], ensure_ascii=False))


if __name__ == "__main__":
    main()
