"""Diagnose industry co-movement using official labels published before development."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import statistics
import tempfile
from datetime import date, datetime
from pathlib import Path

from ..data_pipeline.csrc_industry_2019q3 import CONFIG as INDUSTRY_CONFIG, encoded, load_development
from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/pre_wuhan_industry_comovement_2019.json"
OUTPUT = ROOT / "research_outputs/pre_wuhan_industry_comovement_2019_v1"
VERSION = "pre-wuhan-industry-comovement-description-v1"
MODES = ("raw", "stock_minus_same_day_panel_mean")


def pearson(left: list[float], right: list[float]) -> float | None:
    left_mean, right_mean = statistics.fmean(left), statistics.fmean(right)
    a, b = [x - left_mean for x in left], [x - right_mean for x in right]
    a2, b2 = math.fsum(x * x for x in a), math.fsum(x * x for x in b)
    if a2 == 0 or b2 == 0:
        return None
    return max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(a, b)) / math.sqrt(a2 * b2)))


def summarize(values: list[float | None]) -> dict:
    defined = [value for value in values if value is not None]
    return {"total_pairs": len(values), "defined_pairs": len(defined), "undefined_pairs": len(values) - len(defined),
            "mean_correlation": statistics.fmean(defined) if defined else None,
            "median_correlation": statistics.median(defined) if defined else None}


def returns_panel(rows: list[dict]) -> tuple[list[str], list[str], dict[str, dict[str, list[float]]]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("industry diagnostic needs nonempty company-date rows")
    panel = {}
    for row in rows:
        code, day = row["stock_code"], row["trade_date"]
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code) or date.fromisoformat(day).isoformat() != day:
            raise ValueError("invalid stock code or trade date")
        key = (code, day)
        if key in panel:
            raise ValueError("duplicate company-date in industry panel")
        before, after, observed = row["price_before_minor"], row["price_after_minor"], row["observed_return"]
        if (type(before) is not int or type(after) is not int or before <= 0 or after <= 0
                or type(observed) not in (int, float) or not math.isfinite(observed) or observed < -1):
            raise ValueError("invalid observed return or synthetic price")
        panel[key] = {"observed": float(observed), "synthetic": after / before - 1}
    codes, dates = sorted({key[0] for key in panel}), sorted({key[1] for key in panel})
    if len(codes) < 2 or len(dates) < 2 or set(panel) != {(code, day) for code in codes for day in dates}:
        raise ValueError("industry panel needs complete coverage and at least two dates and stocks")
    return codes, dates, {field: {code: [panel[code, day][field] for day in dates] for code in codes}
                          for field in ("observed", "synthetic")}


def series_metrics(series: dict[str, list[float]], groups: dict[str, str], baskets: dict[str, int]) -> tuple[dict, list[dict]]:
    codes = sorted(series)
    if (set(groups) != set(codes) or set(baskets) != set(codes)
            or any(not isinstance(groups[code], str) or not re.fullmatch(r"[A-S]\d{2}", groups[code]) for code in codes)):
        raise ValueError("industry grouping and baskets must cover every stock without null labels")
    panel_mean = [statistics.fmean(series[code][index] for code in codes) for index in range(len(series[codes[0]]))]
    transformed = {"raw": series, "stock_minus_same_day_panel_mean": {
        code: [value - panel_mean[index] for index, value in enumerate(series[code])] for code in codes}}
    pairs = []
    for index, left in enumerate(codes):
        for right in codes[index + 1:]:
            pair = {"left_stock": left, "right_stock": right, "left_industry": groups[left], "right_industry": groups[right],
                    "same_industry": groups[left] == groups[right], "same_basket": baskets[left] == baskets[right]}
            for mode in MODES:
                pair[mode] = pearson(transformed[mode][left], transformed[mode][right])
            pairs.append(pair)
    metrics = {}
    for mode in MODES:
        within = summarize([pair[mode] for pair in pairs if pair["same_industry"]])
        between = summarize([pair[mode] for pair in pairs if not pair["same_industry"]])
        gap = (within["mean_correlation"] - between["mean_correlation"]
               if within["mean_correlation"] is not None and between["mean_correlation"] is not None else None)
        by_industry = {group: summarize([pair[mode] for pair in pairs if pair["same_industry"] and pair["left_industry"] == group])
                       for group in sorted(set(groups.values()))}
        industry_means = [value["mean_correlation"] for value in by_industry.values() if value["mean_correlation"] is not None]
        basket_groups = {f"{industry}_industry_{basket}_basket": summarize([
            pair[mode] for pair in pairs if pair["same_industry"] == (industry == "same") and pair["same_basket"] == (basket == "same")])
            for industry in ("same", "different") for basket in ("same", "different")}
        metrics[mode] = {"all": summarize([pair[mode] for pair in pairs]), "same_industry": within,
                         "different_industry": between, "within_minus_between": gap, "by_industry": by_industry,
                         "within_industry_equal_weight_mean": statistics.fmean(industry_means) if industry_means else None,
                         "industries_with_defined_within_pairs": len(industry_means), "by_industry_and_basket": basket_groups}
    return metrics, pairs


def diagnose(rows: list[dict], groups: dict[str, str], baskets: dict[str, int]) -> tuple[dict, dict, list[str], list[str]]:
    codes, dates, panel = returns_panel(rows)
    results, pairs = {}, {}
    for field in ("observed", "synthetic"):
        results[field], pairs[field] = series_metrics(panel[field], groups, baskets)
    return results, pairs, codes, dates


def compute() -> tuple[dict, bytes, dict]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    directory = (CONFIG.parent / config["industry_directory"]).resolve()
    inputs = {str(CONFIG): file_sha256(CONFIG)}
    for name, expected in (("industry_memberships.json", config["industry_catalog_sha256"]),
                           ("provenance.json", config["industry_provenance_sha256"])):
        path = directory / name
        if file_sha256(path) != expected:
            raise ValueError("official industry dictionary or provenance hash differs")
        inputs[str(path)] = expected
    catalog = json.loads((directory / "industry_memberships.json").read_text(encoding="utf-8"))
    provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    for field in ("inputs", "code_sha256"):
        for name, digest in provenance[field].items():
            if file_sha256(Path(name)) != digest:
                raise ValueError("industry dictionary source or extraction code changed")
            inputs[name] = digest
    development, archived_inputs = load_development(json.loads(INDUSTRY_CONFIG.read_text(encoding="utf-8")))
    inputs.update(archived_inputs)
    memberships = catalog["cohort_memberships"]
    if (len(memberships) != 123 or any(row["status"] != "verified_table_label" or row["agent_signal_enabled"] is not False
                                     or row["impact_direction"] != "unknown" or row["impact_magnitude"] is not None for row in memberships)):
        raise ValueError("industry comparison requires verified grouping with economic signals disabled")
    groups = {row["stock_code"]: row["industry_code"] for row in memberships}
    codes = development["sample"]["selected_stock_codes"]
    if len(groups) != 123 or set(groups) != set(codes):
        raise ValueError("official industry dictionary cohort differs")
    baskets = {code: index // 3 for index, code in enumerate(codes)}
    variants = development["variants"]
    if [variant["name"] for variant in variants] != config["variants"]:
        raise ValueError("development variant coverage differs")
    comparisons = {}
    reference_observed, reference_pairs, reference_panel = None, None, None
    wide_pairs = None
    for variant in variants:
        rows = variant["daily_asset_rows"]
        cutoff = min(row["signal_cutoff_date"] for row in rows) + "T00:00:00+08:00"
        if (datetime.fromisoformat(catalog["source"]["available_at_proxy"]) > datetime.fromisoformat(cutoff)
                or any(row["earliest_signal_cutoff"] != cutoff for row in memberships)):
            raise ValueError("official industry table is not eligible at the frozen information cutoff")
        metrics, pairs, this_codes, dates = diagnose(rows, groups, baskets)
        if this_codes != codes or len(dates) != 43 or dates[0] != "2019-11-01" or dates[-1] != "2019-12-31":
            raise ValueError("industry diagnostic date or company coverage differs")
        observed_panel = {(row["stock_code"], row["trade_date"]): row["observed_return"] for row in rows}
        if reference_observed is None:
            reference_observed, reference_pairs, reference_panel = metrics["observed"], pairs["observed"], observed_panel
            wide_pairs = [{**{key: row[key] for key in ("left_stock", "right_stock", "left_industry", "right_industry", "same_industry", "same_basket")},
                           **{f"observed_{mode}": row[mode] for mode in MODES}} for row in reference_pairs]
        elif metrics["observed"] != reference_observed or pairs["observed"] != reference_pairs or observed_panel != reference_panel:
            raise ValueError("observed reference differs between industry variant panels")
        comparisons[variant["name"]] = metrics["synthetic"]
        for output_row, synthetic_row in zip(wide_pairs, pairs["synthetic"]):
            if (output_row["left_stock"], output_row["right_stock"]) != (synthetic_row["left_stock"], synthetic_row["right_stock"]):
                raise ValueError("industry pair alignment differs")
            output_row.update({f'{variant["name"]}_{mode}': synthetic_row[mode] for mode in MODES})
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(wide_pairs[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(wide_pairs)
    result = {"pipeline_version": VERSION, "purpose": config["interpretation"],
              "sample": {**development["sample"], "industry_divisions": len(set(groups.values())),
                         "singleton_industries": sum(list(groups.values()).count(group) == 1 for group in set(groups.values())),
                         "stock_pairs": len(wide_pairs)},
              "industry_source": catalog["source"], "industry_counts": catalog["cohort_industry_counts"],
              "observed": reference_observed, "variants": comparisons,
              "method": {"industry": "exact official category letter + two-digit division; no inferred sector impact",
                         "correlation": "Pearson over all 43 development dates; pair-weighted arithmetic means",
                         "market_removal": "subtract the same-day equal-weight return of all 123 panel stocks, coefficient fixed at one; not a fitted beta residual",
                         "basket_control": "separate pairs inside the original three-stock basket from pairs in different baskets",
                         "undefined": "zero-variance correlations remain null; CSV cells stay blank; no zero imputation"},
              "limitations": catalog["limitations"] + [
                  "These 43 dates and all variant results were already inspected; no forecast, causal or out-of-time validation is claimed.",
                  "Pair correlations share stocks and dates; pair counts are not independent sample sizes or significance tests.",
                  "The largest industry contributes many more pairs; equal-industry means and singleton counts are reported separately.",
                  "Synthetic paths retain the original t-2 information, t-1 execution reference, t outcome label; not same exchange-day reconstruction.",
                  "Same-day panel means and returns are diagnostic outcomes only; no Agent input or impact coefficient is enabled.",
              ]}
    provenance = {"inputs": dict(sorted(inputs.items())),
                  "code_sha256": {str(Path(__file__).resolve()): file_sha256(Path(__file__)),
                                  str(ROOT / "research/data_pipeline/provenance.py"): file_sha256(ROOT / "research/data_pipeline/provenance.py")}}
    return result, buffer.getvalue().encode("utf-8"), provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("industry co-movement output requires a direct research_outputs directory")
    result, pairs, provenance = compute()
    files = {"results.json": encoded(result), "pair_correlations.csv": pairs, "provenance.json": encoded(provenance)}
    if args.audit_existing:
        if any((output / name).read_bytes() != content for name, content in files.items()):
            raise ValueError("industry co-movement archive differs byte-for-byte from recomputation")
    else:
        if output.exists():
            raise ValueError("choose a fresh industry co-movement output directory")
        with tempfile.TemporaryDirectory(prefix="industry-comovement-stage-", dir=output.parent) as temporary:
            stage = Path(temporary)
            for name, content in files.items():
                (stage / name).write_bytes(content)
            stage.replace(output)
    print(json.dumps({"sample": {key: value for key, value in result["sample"].items() if key != "selected_stock_codes"},
                      "raw_within_between": {name: {"within": metrics["raw"]["same_industry"]["mean_correlation"],
                                                     "between": metrics["raw"]["different_industry"]["mean_correlation"],
                                                     "difference": metrics["raw"]["within_minus_between"]}
                                             for name, metrics in {"observed": result["observed"], **result["variants"]}.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
