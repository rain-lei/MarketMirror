"""Cross-check the industry archives using flat PDF text and stdlib correlation."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import re
import statistics
from pathlib import Path

from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "research_outputs/pre_wuhan_industry_crosscheck_2019_v1.json"
MODES = ("raw", "stock_minus_same_day_panel_mean")


def check() -> dict:
    import pdfplumber

    industry_dir = ROOT / "research_outputs/pre_wuhan_industry_2019q3_v1"
    diagnostic_dir = ROOT / "research_outputs/pre_wuhan_industry_comovement_2019_v1"
    inputs = {}
    for directory in (industry_dir, diagnostic_dir):
        provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
        for field in ("inputs", "code_sha256"):
            for name, digest in provenance[field].items():
                if file_sha256(Path(name)) != digest:
                    raise ValueError("industry cross-check input or code hash differs")
                inputs[name] = digest
        for path in directory.iterdir():
            if path.is_file():
                inputs[str(path)] = file_sha256(path)
    catalog = json.loads((industry_dir / "industry_memberships.json").read_text(encoding="utf-8"))
    dictionary = {row["security_code"]: row for row in catalog["security_memberships"]}
    independent_text = {}
    pdf = ROOT / "research_outputs/csrc_industry_2019q3_raw_v1/industry_2019q3.pdf"
    with pdfplumber.open(pdf) as document:
        for index, page in enumerate(document.pages, 1):
            for line in page.extract_text().splitlines():
                match = re.search(r"\b(\d{6})\s+(.+)$", line)
                if match:
                    code = match[1]
                    if code in independent_text:
                        raise ValueError("duplicate security in independent flat text")
                    independent_text[code] = (re.sub(r"\s+", "", match[2]), index)
    if set(independent_text) != set(dictionary) or len(dictionary) != 3702:
        raise ValueError("independent PDF code coverage differs")
    for code, (name, page) in independent_text.items():
        row = dictionary[code]
        if (name, page) != (row["historical_short_name"], row["source_location"]["pdf_page"]):
            raise ValueError("independent PDF security name or page differs")
    source_path = ROOT / "research_outputs/pre_wuhan_background_response_2019_v1/results.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))["result"]
    report = json.loads((diagnostic_dir / "results.json").read_text(encoding="utf-8"))
    with (diagnostic_dir / "pair_correlations.csv").open(encoding="utf-8", newline="") as handle:
        pairs = list(csv.DictReader(handle))
    codes = source["sample"]["selected_stock_codes"]
    labels = {row["stock_code"]: row["industry_code"] for row in catalog["cohort_memberships"]}
    baskets = {code: index // 3 for index, code in enumerate(codes)}
    expected_pairs = list(itertools.combinations(codes, 2))
    if [(row["left_stock"], row["right_stock"]) for row in pairs] != expected_pairs or len(pairs) != 7503:
        raise ValueError("independent pair coverage or ordering differs")
    for row in pairs:
        left, right = row["left_stock"], row["right_stock"]
        if (row["left_industry"] != labels[left] or row["right_industry"] != labels[right]
                or row["same_industry"] != str(labels[left] == labels[right])
                or row["same_basket"] != str(baskets[left] == baskets[right])):
            raise ValueError("independent industry or basket assignment differs")
    panels = {}
    for variant in source["variants"]:
        keyed = {(row["stock_code"], row["trade_date"]): row for row in variant["daily_asset_rows"]}
        if len(keyed) != 5289:
            raise ValueError("independent source panel coverage differs")
        dates = sorted({day for _, day in keyed})
        panels[variant["name"]] = {code: [keyed[code, day]["price_after_minor"] / keyed[code, day]["price_before_minor"] - 1
                                          for day in dates] for code in codes}
        observed = {code: [keyed[code, day]["observed_return"] for day in dates] for code in codes}
        if "observed" in panels and panels["observed"] != observed:
            raise ValueError("independent observed variant panels differ")
        panels["observed"] = observed
    max_difference, coefficient_checks, summary_checks = 0.0, 0, 0

    def verify_summary(archived: dict, values: list[float | None]) -> None:
        nonlocal max_difference, summary_checks
        defined = [value for value in values if value is not None]
        expected = {"total_pairs": len(values), "defined_pairs": len(defined), "undefined_pairs": len(values) - len(defined),
                    "mean_correlation": statistics.mean(defined) if defined else None,
                    "median_correlation": statistics.median(defined) if defined else None}
        if set(archived) != set(expected):
            raise ValueError("independent aggregate fields differ")
        for key, value in expected.items():
            summary_checks += 1
            if value is None:
                if archived[key] is not None:
                    raise ValueError("undefined industry aggregate was filled")
            elif key.endswith("pairs"):
                if archived[key] != value:
                    raise ValueError("independent aggregate count differs")
            else:
                difference = abs(archived[key] - value)
                max_difference = max(max_difference, difference)
                if difference > 1e-12:
                    raise ValueError("independent aggregate correlation differs")

    for name, series in panels.items():
        reference = report["observed"] if name == "observed" else report["variants"][name]
        average = [statistics.mean(series[code][i] for code in codes) for i in range(43)]
        for mode in MODES:
            adjusted = series if mode == "raw" else {code: [value - average[i] for i, value in enumerate(series[code])] for code in codes}
            values = []
            for row in pairs:
                try:
                    value = statistics.correlation(adjusted[row["left_stock"]], adjusted[row["right_stock"]])
                except statistics.StatisticsError:
                    value = None
                archived = row[f"{name}_{mode}"]
                if value is None:
                    if archived != "":
                        raise ValueError("undefined pair correlation was filled")
                else:
                    difference = abs(value - float(archived))
                    max_difference = max(max_difference, difference)
                    if not math.isfinite(value) or difference > 1e-12:
                        raise ValueError("stdlib pair correlation differs")
                coefficient_checks += 1
                values.append(value)
            metric = reference[mode]
            verify_summary(metric["all"], values)
            for flag, key in (("True", "same_industry"), ("False", "different_industry")):
                verify_summary(metric[key], [value for row, value in zip(pairs, values) if row["same_industry"] == flag])
            for industry in set(labels.values()):
                verify_summary(metric["by_industry"][industry], [value for row, value in zip(pairs, values)
                               if row["left_industry"] == industry and row["right_industry"] == industry])
            for same_industry in (True, False):
                for same_basket in (True, False):
                    key = f'{"same" if same_industry else "different"}_industry_{"same" if same_basket else "different"}_basket'
                    verify_summary(metric["by_industry_and_basket"][key], [value for row, value in zip(pairs, values)
                                   if row["same_industry"] == str(same_industry) and row["same_basket"] == str(same_basket)])
    return {"pipeline_version": "pre-wuhan-industry-independent-crosscheck-v1",
            "security_code_name_page_checks": len(dictionary), "stock_pairs": len(pairs),
            "pair_correlation_checks": coefficient_checks, "aggregate_field_checks": summary_checks,
            "maximum_absolute_difference": max_difference, "tolerance": 1e-12,
            "scope": "Flat PDF text checks codes/names/pages; stdlib correlation independently checks every paired coefficient and all count/mean/median aggregates. Full industry labels remain grounded in physical cells and source visual review.",
            "inputs": dict(sorted(inputs.items())), "code_sha256": file_sha256(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("industry cross-check output must be inside research_outputs")
    result = check()
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if output.read_bytes() != payload:
            raise ValueError("industry cross-check archive differs byte-for-byte")
    else:
        with output.open("xb") as handle:
            handle.write(payload)
    print(json.dumps({key: value for key, value in result.items() if key not in {"inputs", "code_sha256"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
