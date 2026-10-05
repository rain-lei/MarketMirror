"""Independent physical-cell/Decimal/SVD audit of fixed financing increments."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path

import numpy as np

from . import audit_issuer_financial_increment as base_audit
from .audit_issuer_operating_increment import check_comparison, independent_predictions
from ..data_pipeline import audit_issuer_half_financing_states as state_audit

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_financing_increment_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_financing_increment_2019_v1"
PROTOCOL_SHA256 = "5f8b1fe1540053dd1f734b55c127d66514c9d793a01b42274b512c7131efaf12"
SCALES = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}
RESTRICTION = "h1_max_verified_restricted_money_fund_row_to_money_funds"
RAW_DEFINITIONS = {
    "h1_money_funds_to_assets": ("money_funds", "assets"),
    "h1_short_term_borrowings_to_assets": ("short_term_borrowings", "assets"),
    "h1_due_within_one_year_noncurrent_liabilities_to_assets": ("noncurrent_liabilities_due_within_one_year", "assets"),
    "h1_money_funds_to_current_liabilities": ("money_funds", "current_liabilities"),
}
INTERACTION_DEFINITIONS = {
    "h1_restricted_row_x_benchmark_volatility_20": (RESTRICTION, "benchmark_volatility_20"),
    "h1_restricted_row_x_log_amount_surprise_20": (RESTRICTION, "log_amount_surprise_20"),
}


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ratio(n, d):
    if n is None or d is None:
        return None
    a, b = Decimal(n), Decimal(d)
    if not a.is_finite() or not b.is_finite() or a < 0 or b <= 0:
        return None
    with localcontext() as context:
        context.prec = 50
        return str(a / b)


def raw_financing_values(state, candidate, read, restricted):
    """Ignore exported ratios; use independently re-read current balance cells."""
    values = {f: None for f in [*RAW_DEFINITIONS, RESTRICTION]}
    if state["status"] != "LIMITED_SAME_PERIOD_FINANCING_FACTS_AVAILABLE":
        return values
    selected = candidate["candidates"][candidate["selected_candidate_index"]]
    current = selected["current_column_index"]
    column = selected["column_mapping"]["columns"][current]
    if column["period_end"] != "2019-06-30" or column["scope"] != "consolidated":
        raise ValueError("independent financing balance is not consolidated current H1")
    if read["selected_candidate_index"] != candidate["selected_candidate_index"] or read["status"] != "PASS":
        raise ValueError("independent financing physical-cell candidate differs")
    raw = {}
    for field in {x for pair in RAW_DEFINITIONS.values() for x in pair}:
        rows = read["independent_reading"]["fields"][field]
        cell = rows[0]["cells"][current] if len(rows) == 1 else None
        raw[field] = cell["reported_value"] if cell and cell["status"] == "NUMERIC" else None
    for feature, (n, d) in RAW_DEFINITIONS.items():
        values[feature] = ratio(raw[n], raw[d])
    if selected["currency_declaration"]["status"] != "CNY_EXPLICIT" or raw["money_funds"] is None:
        return values
    denominator = str(Decimal(raw["money_funds"]) * SCALES[selected["reported_unit"]])
    fractions = []
    for table in restricted["tables"]:
        if not table["source_number_checks_accepted"] or table["scope"] != "CONSOLIDATED_EXPLICIT":
            continue
        unit = table["unit"]
        for row in table["rows"]:
            if row["component_kind"] != "RESTRICTED_MONEY_FUNDS_AS_REPORTED" or not row["source_number_independently_verified"]:
                continue
            cny = unit["currency"] == "CNY_EXPLICIT"
            if unit["currency"] == "CURRENCY_UNSPECIFIED":
                cny = any(d["status"] == "PASS_EXPLICIT_CNY_DECLARATION"
                          and d["reported_unit"] == unit["reported_unit"]
                          and d["pdf_page"] < row["source_cell"]["pdf_page"]
                          for d in read.get("independent_note_presentation_declarations", []))
            if not cny or unit["reported_unit"] not in SCALES or row["verified_ending_value_reported_units"] is None:
                continue
            numerator = str(Decimal(row["verified_ending_value_reported_units"]) * SCALES[unit["reported_unit"]])
            fraction = ratio(numerator, denominator)
            if fraction is not None and Decimal(fraction) <= 1:
                fractions.append(Decimal(fraction))
    values[RESTRICTION] = str(max(fractions)) if fractions else None
    return values


def issuer_map(source):
    result = {c["stock_code"]: c for c in source["companies"]}
    if len(result) != len(source["companies"]):
        raise ValueError("independent financing duplicate company identity")
    return result


def check_fit(fit, design, features, intercept=True):
    if (fit["features"] != features or fit["regularization"] != "none" or fit["intercept"] is not intercept
            or fit["training_rows"] != design["training_rows"] or fit["full_rank_solver_completed"] is not True):
        raise ValueError("independent financing model fit specification differs")
    for i, feature in enumerate(features):
        base_audit.close(fit["training_means"][feature], design["means"][i], "financing training mean")
        base_audit.close(fit["training_sample_scales"][feature], design["scales"][i], "financing training scale")
    for actual, expected in zip(fit["standardized_coefficients"], design["coefficients"], strict=True):
        base_audit.close(actual, expected, "financing SVD coefficient", tolerance=1e-9)


def compute(directory):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if digest(CONFIG) != PROTOCOL_SHA256:
        raise ValueError("prespecified financing protocol changed")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    panel = json.loads((directory / "panel.json").read_text(encoding="utf-8"))
    result = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    bindings = {**manifest["inputs"], **manifest["code_sha256"], str(directory / "manifest.json"): digest(directory / "manifest.json")}
    for name, record in manifest["artifacts"].items():
        bindings[str(directory / name)] = record["sha256"]
    if set(manifest["artifacts"]) != {"panel.json", "results.json"} or bindings.get(str(CONFIG)) != PROTOCOL_SHA256:
        raise ValueError("financing experiment manifest binding differs")
    sources = {}
    for name, record in config["inputs"].items():
        path = (CONFIG.parent / record["path"]).resolve()
        if bindings.get(str(path)) != record["sha256"]:
            raise ValueError("financing source not bound to frozen protocol")
        sources[name] = json.loads(path.read_text(encoding="utf-8"))

    def verify():
        for path, expected in bindings.items():
            if digest(path) != expected:
                raise ValueError("independent financing source/output/producer differs: " + path)

    verify()
    base_dir = (CONFIG.parent / config["inputs"]["base_manifest"]["path"]).resolve().parent
    if serialize(base_audit.compute(base_dir)) != serialize(sources["base_independent_audit"]):
        raise ValueError("original full raw-market independent audit no longer reproduces")
    if serialize(state_audit.compute()) != serialize(sources["financing_state_audit"]):
        raise ValueError("original physical-cell financing-state contract audit no longer reproduces")
    states, candidates, readings, restrictions = [issuer_map(sources[name]) for name in (
        "financing_states", "half_balance_candidates", "half_balance_audit", "restricted_asset_states")]
    base = sources["base_panel"]
    codes = base["parent_stock_codes"]
    if (len(codes) != config["expected_parent_companies"] or codes != sorted(set(codes))
            or any(set(m) != set(codes) for m in (states, candidates, readings, restrictions))):
        raise ValueError("independent financing parent cohort differs")
    if (panel["split"] != base["split"] or result["split"] != base["split"] or base["split"] != config["frozen_split"]
            or panel["parent_stock_codes"] != codes or result["limitations"] != config["limitations"]):
        raise ValueError("independent financing frozen dates/cohort/limitations differ")
    if set(panel["blocks"]) != set(config["blocks"]) or set(result["blocks"]) != set(config["blocks"]):
        raise ValueError("independent financing block coverage differs")
    raw = {code: raw_financing_values(states[code], candidates[code], readings[code], restrictions[code]) for code in codes}
    base_cfg = json.loads(base_audit.CONFIG.read_text(encoding="utf-8"))
    summaries, total_features, total_predictions, maximum_difference = {}, 0, 0, 0.0
    for block, features in config["blocks"].items():
        archived_rows = panel["blocks"][block]
        if len(archived_rows) != len(base["rows"]):
            raise ValueError("independent financing company-date grid differs")
        eligible = []
        for row, original in zip(archived_rows, base["rows"], strict=True):
            code = original["stock_code"]
            state, metadata = states[code], states[code]["report_metadata"]
            if state["stock_code"] != code or state["industry_code"] != original["industry_code"] or state["agent_signal_enabled"] is not False:
                raise ValueError("independent financing issuer or industry association differs")
            available = metadata["available_at_proxy"] if metadata else None
            reason, decimal = original["paired_exclusion_reason"], None
            if reason is None:
                if state["status"] != "LIMITED_SAME_PERIOD_FINANCING_FACTS_AVAILABLE":
                    reason = "FINANCING_" + state["status"]
                elif datetime.fromisoformat(available) > datetime.fromisoformat(original["signal_cutoff_at"]):
                    reason = "FINANCING_SOURCE_NOT_YET_AVAILABLE"
                else:
                    decimal = {f: raw[code][f] for f in features if f not in INTERACTION_DEFINITIONS}
                    if any(v is None for v in decimal.values()):
                        reason, decimal = "MISSING_PRESPECIFIED_FINANCING_BLOCK", None
            expected = {
                **original, "base_paired_exclusion_reason": original["paired_exclusion_reason"],
                "paired_exclusion_reason": reason, "financing_decimal_inputs": decimal,
                "financing_source_pdf_sha256": state["source_pdf_sha256"], "financing_available_at_proxy": available,
                "financing_state_status": state["status"], "financing_report_end_date": metadata["report_end_date"] if metadata else None,
                "financing_balance_scope": state.get("balance_scope"), "block": block,
            }
            if set(row) != set(expected):
                raise ValueError("independent financing row field coverage differs")
            for key, value in expected.items():
                if key not in ("features", "target_absolute_return", "financial_decimal_inputs") and row[key] != value:
                    raise ValueError("independent financing identity/clock/exclusion/Decimal differs: " + key)
            if reason:
                if any(row[k] is not None for k in ("features", "target_absolute_return", "financial_decimal_inputs", "financing_decimal_inputs")):
                    raise ValueError("excluded financing row was backfilled")
                continue
            rebuilt_features = {**original["features"], **{f: float(Decimal(v)) for f, v in decimal.items()}}
            for feature in features:
                if feature in INTERACTION_DEFINITIONS:
                    left, right = INTERACTION_DEFINITIONS[feature]
                    rebuilt_features[feature] = rebuilt_features[left] * original["features"][right]
            if set(rebuilt_features) != set(row["features"]) or row["financial_decimal_inputs"] != original["financial_decimal_inputs"]:
                raise ValueError("independent financing feature specification differs")
            for f, value in rebuilt_features.items():
                base_audit.close(row["features"][f], value, "financing feature " + f, tolerance=1e-12)
                total_features += 1
            base_audit.close(row["target_absolute_return"], original["target_absolute_return"], "financing target", tolerance=1e-12)
            eligible.append({**row, "features": rebuilt_features})
        train, check = ([r for r in eligible if r["partition"] == part] for part in ("train", "evaluation"))
        record = result["blocks"][block]
        if (len(train) != record["training_rows"] or len(check) != record["evaluation_rows"]
                or any(datetime.fromisoformat(r["trade_date"] + "T15:00:00+08:00") > datetime.fromisoformat(base["split"]["model_fit_cutoff_at"]) for r in train)):
            raise ValueError("independent financing training target cutoff or paired split differs")
        pred, designs, definitions, a, b, categories, counts = independent_predictions(
            train, check, base_cfg["market_features"], base_cfg["financial_features"])
        name = "market_industry_financial_financing"
        added = definitions["market_industry_financial"] + features
        pred[name], designs[name] = base_audit.svd_fit(
            [[r["features"][f] for f in added] for r in a], [r["target_absolute_return"] for r in train],
            [[r["features"][f] for f in added] for r in b])
        definitions[name] = added
        if (record["industry_control_categories"] != categories or record["company_training_row_counts"] != counts
                or set(record["models"]) != set(pred) or len(record["evaluation_predictions"]) != len(check)
                or record["financing_features"] != features or record["block"] != block):
            raise ValueError("independent financing model/category/forecast coverage differs")
        for model, fs in definitions.items():
            check_fit(record["models"][model]["fit"], designs[model], fs)
        check_fit(record["models"]["company_fixed_market"]["fit"]["within"],
                  designs["company_fixed_market_within"], base_cfg["market_features"], intercept=False)
        groups = defaultdict(list)
        for r in train:
            groups[r["stock_code"]].append(r)
        for model in ("company_training_mean", "company_fixed_market"):
            fit = record["models"][model]["fit"]
            if set(fit["companies"]) != set(groups):
                raise ValueError("independent financing company-reference coverage differs")
            for code, rows in groups.items():
                c = fit["companies"][code]
                if c["training_rows"] != len(rows):
                    raise ValueError("independent financing company-reference training count differs")
                base_audit.close(c["target_mean"], np.mean([r["target_absolute_return"] for r in rows]), "company target mean")
                for f in base_cfg["market_features"]:
                    base_audit.close(c["feature_means"][f], np.mean([r["features"][f] for r in rows]), "company market mean")
        base_audit.close(record["models"]["training_mean"]["fit"]["training_target_mean"],
                         np.mean([r["target_absolute_return"] for r in train]), "overall target mean")
        targets, days = [r["target_absolute_return"] for r in check], [r["trade_date"] for r in check]
        for model, predictions in pred.items():
            for i, value in enumerate(predictions):
                archived = record["evaluation_predictions"][i]
                if (archived["stock_code"], archived["trade_date"]) != (check[i]["stock_code"], check[i]["trade_date"]):
                    raise ValueError("independent financing evaluation identity differs")
                base_audit.close(archived["predictions"][model], value, "financing SVD forecast", tolerance=1e-9)
                base_audit.close(archived["target_absolute_return"], targets[i], "forecast target", tolerance=1e-12)
                maximum_difference = max(maximum_difference, abs(archived["predictions"][model] - float(value)))
                total_predictions += 1
            base_audit.check_metrics(record["models"][model]["metrics"], base_audit.independent_metrics(targets, predictions, days), model)
        check_comparison(record["financing_comparison"], pred["market_industry_financial"], pred[name], targets, days)
        excluded = dict(Counter(r["paired_exclusion_reason"] for r in archived_rows if r["paired_exclusion_reason"]))
        if record["block_exclusions"] != excluded or record["excluded_slot_counts"] != excluded:
            raise ValueError("independent financing exclusion counts differ")
        for flag in ("references_rebuilt_on_same_company_dates", "restricted_feature_is_maximum_verified_single_row_not_total",
                     "h1_denominators_never_replaced_by_q3_values", "company_fixed_market_is_reference_without_financing"):
            if record[flag] is not True:
                raise ValueError("independent financing interpretation flag differs")
        if record["agent_signal_enabled"] is not False:
            raise ValueError("financing model may not enable an Agent response")
        summaries[block] = {
            "company_date_slots": len(archived_rows), "training_rows": len(train), "evaluation_rows": len(check),
            "training_companies": len(groups), "evaluation_companies": len({r["stock_code"] for r in check}),
            "designs": {n: {k: v for k, v in d.items() if k not in ("means", "scales", "coefficients")} for n, d in designs.items()},
        }
    primary, interactions = (panel["blocks"][b] for b in ("primary_restricted_cash_buffer", "secondary_restriction_market_interactions"))
    if [(r["stock_code"], r["trade_date"], r["paired_exclusion_reason"]) for r in primary] != [
            (r["stock_code"], r["trade_date"], r["paired_exclusion_reason"]) for r in interactions]:
        raise ValueError("independent financing interaction eligibility changed")
    verify()
    if panel["agent_signal_enabled"] is not False or result["agent_signal_enabled"] is not False:
        raise ValueError("independent financing output signal gate differs")
    audit_paths = [Path(__file__).resolve(), Path(base_audit.__file__).resolve(),
                   ROOT / "research/baselines/audit_issuer_operating_increment.py", Path(state_audit.__file__).resolve()]
    return {
        "pipeline_version": "independent-physical-cells-Decimal-SVD-financing-increment-audit-v1", "status": "PASS",
        "blocks": summaries, "feature_cells_checked": total_features, "forecast_cells_checked": total_predictions,
        "maximum_forecast_absolute_difference": maximum_difference,
        "base_raw_market_audit_repeated_byte_identically": True,
        "financing_state_contract_audit_repeated_byte_identically": True,
        "source_and_output_bindings": dict(sorted(bindings.items())),
        "audit_code_sha256": {str(p): digest(p) for p in audit_paths}, "numpy_version": np.__version__, "agent_signal_enabled": False,
        "interpretation": "Independent current physical balance cells and audited original restriction rows reproduce H1 factors without production financing imports; full base raw-market and financing-state audits replay byte-identically. SVD confirms all eight models, predictions, errors and date sensitivity. Implementation agreement does not confer economic adoption or blind validation.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("independent financing audit requires a direct research_outputs directory")
    result = compute(directory)
    path = directory / "independent_audit.json"
    if args.audit_existing:
        if path.read_bytes() != serialize(result):
            raise ValueError("independent financing audit differs from frozen reconstruction")
        print("Independent financing increment audit rebuilt byte-identically.")
    else:
        with path.open("xb") as handle:
            handle.write(serialize(result))
    print(json.dumps({k: result[k] for k in ("status", "feature_cells_checked", "forecast_cells_checked", "maximum_forecast_absolute_difference")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
