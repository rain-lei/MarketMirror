"""Independently recompute published industry response statistics with stdlib."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
from pathlib import Path

from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_industry_response_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_industry_response_statistics_2019_v1.json"


def compute() -> dict:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    inputs = {str(SOURCE / "manifest.json"): file_sha256(SOURCE / "manifest.json")}
    for field in ("inputs", "code_sha256"):
        for name, digest in manifest[field].items():
            if file_sha256(Path(name)) != digest:
                raise ValueError("industry statistics source or code hash differs")
            inputs[name] = digest
    for name, info in manifest["artifacts"].items():
        path = SOURCE / name
        if path.parent != SOURCE or file_sha256(path) != info["sha256"]:
            raise ValueError("industry response artifact hash differs")
        inputs[str(path)] = info["sha256"]
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    codes = result["sample"]["selected_stock_codes"]
    catalog_path = ROOT / "research_outputs/pre_wuhan_industry_2019q3_v1/industry_memberships.json"
    inputs[str(catalog_path)] = file_sha256(catalog_path)
    labels = {row["stock_code"]: row["industry_code"] for row in json.loads(catalog_path.read_text(encoding="utf-8"))["cohort_memberships"]}
    baskets = {code: index // 3 for index, code in enumerate(codes)}
    pairs = list(itertools.combinations(codes, 2))
    if len(codes) != 123 or len(labels) != 123 or set(labels) != set(codes) or len(pairs) != 7503:
        raise ValueError("industry statistics cohort coverage differs")
    scalar_checks, pair_evaluations, maximum = 0, 0, 0.0
    observed_reference = None

    def close(actual, expected):
        nonlocal scalar_checks, maximum
        scalar_checks += 1
        if expected is None:
            if actual is not None:
                raise ValueError("undefined industry statistic was filled")
        else:
            if not math.isfinite(actual):
                raise ValueError("nonfinite industry statistic")
            difference = abs(actual - expected)
            maximum = max(maximum, difference)
            if difference > 1e-12:
                raise ValueError("independent industry statistic differs")

    def summary(saved, values):
        defined = [value for value in values if value is not None]
        close(saved["total_pairs"], len(values))
        close(saved["defined_pairs"], len(defined))
        close(saved["undefined_pairs"], len(values) - len(defined))
        close(saved["mean_correlation"], statistics.mean(defined) if defined else None)
        close(saved["median_correlation"], statistics.median(defined) if defined else None)

    for variant in result["variants"]:
        rows = variant["daily_asset_rows"]
        panel = {(row["stock_code"], row["trade_date"]): row for row in rows}
        dates = sorted({day for _, day in panel})
        if len(panel) != 5289 or len(rows) != 5289 or len(dates) != 43 or set(panel) != set(itertools.product(codes, dates)):
            raise ValueError("industry statistics company-date coverage differs")
        observed = {(code, day): panel[code, day]["observed_return"] for code, day in panel}
        if observed_reference is None:
            observed_reference = observed
        elif observed != observed_reference:
            raise ValueError("observed industry panel differs between scenarios")
        flat = [row["price_after_minor"] / row["price_before_minor"] - 1 for row in rows]
        saved = variant["comparison"]["synthetic"]
        close(saved["mean"], statistics.mean(flat))
        close(saved["standard_deviation"], statistics.pstdev(flat))
        close(saved["mean_absolute_return"], statistics.mean(abs(value) for value in flat))
        close(saved["zero_return_fraction"], sum(value == 0 for value in flat) / len(flat))
        close(variant["total_matched_volume"], sum(row["matched_volume"] for row in rows))
        close(variant["comparison"]["return_correlation"], statistics.correlation(flat, [row["observed_return"] for row in rows]))
        series = {code: [panel[code, day]["price_after_minor"] / panel[code, day]["price_before_minor"] - 1 for day in dates] for code in codes}
        daily_means = [statistics.mean(series[code][index] for code in codes) for index in range(43)]
        close(variant["common_factor_metrics"]["synthetic"]["equal_weight_daily_return_std"], statistics.pstdev(daily_means))
        for mode in ("raw", "stock_minus_same_day_panel_mean"):
            transformed = series if mode == "raw" else {code: [value - daily_means[index] for index, value in enumerate(values)] for code, values in series.items()}
            values = []
            for left, right in pairs:
                try:
                    value = statistics.correlation(transformed[left], transformed[right])
                except statistics.StatisticsError:
                    value = None
                values.append(value)
                pair_evaluations += 1
            metric = variant["industry_metrics"]["synthetic"][mode]
            summary(metric["all"], values)
            within = [value for (left, right), value in zip(pairs, values) if labels[left] == labels[right]]
            between = [value for (left, right), value in zip(pairs, values) if labels[left] != labels[right]]
            summary(metric["same_industry"], within)
            summary(metric["different_industry"], between)
            close(metric["within_minus_between"], statistics.mean([x for x in within if x is not None]) - statistics.mean([x for x in between if x is not None]))
            industry_means = []
            for industry in sorted(set(labels.values())):
                subset = [value for (left, right), value in zip(pairs, values) if labels[left] == industry and labels[right] == industry]
                summary(metric["by_industry"][industry], subset)
                defined = [x for x in subset if x is not None]
                if defined:
                    industry_means.append(statistics.mean(defined))
            close(metric["industries_with_defined_within_pairs"], len(industry_means))
            close(metric["within_industry_equal_weight_mean"], statistics.mean(industry_means) if industry_means else None)
            for same_industry, same_basket in itertools.product((True, False), repeat=2):
                key = f'{"same" if same_industry else "different"}_industry_{"same" if same_basket else "different"}_basket'
                subset = [value for (left, right), value in zip(pairs, values) if (labels[left] == labels[right]) == same_industry and (baskets[left] == baskets[right]) == same_basket]
                summary(metric["by_industry_and_basket"][key], subset)
            if mode == "raw":
                close(variant["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"], statistics.mean(x for x in values if x is not None))
    return {"pipeline_version": "pre-wuhan-industry-response-stdlib-statistics-v1", "variants": len(result["variants"]),
            "independent_pair_correlations_evaluated": pair_evaluations, "aggregate_scalar_checks": scalar_checks,
            "maximum_absolute_difference": maximum, "tolerance": 1e-12,
            "method": "Independent stdlib correlation/mean/pstdev over archived prices and official labels; no production diagnostic function is called.",
            "inputs": dict(sorted(inputs.items())), "code_sha256": file_sha256(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("industry statistics output must be inside research_outputs")
    result = compute()
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if output.read_bytes() != payload:
            raise ValueError("industry statistics archive differs byte-for-byte")
    else:
        with output.open("xb") as handle:
            handle.write(payload)
    print(json.dumps({key: value for key, value in result.items() if key not in {"inputs", "code_sha256"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
