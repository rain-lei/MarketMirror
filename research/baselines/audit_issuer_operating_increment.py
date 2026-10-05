"""Independent source-amount/Decimal/SVD verification of all operating blocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path

import numpy as np

from . import audit_issuer_financial_increment as base_audit

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_operating_increment_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_operating_increment_2019_v1"
PROTOCOL_SHA256 = "e6d21660d96910a586c16b7f72f430ac4156042a3768a9abf552d3ed7aa3707a"

DEFINITIONS = {
    "operating_net_cashflow_to_period_end_assets": ("cashflow", "operating_net_cashflow", "balance", "assets"),
    "ytd_net_profit_to_period_end_assets": ("profit", "net_profit", "balance", "assets"),
    "operating_net_cashflow_to_selected_revenue": ("cashflow", "operating_net_cashflow", "profit", "selected_revenue"),
    "ytd_net_profit_to_selected_revenue": ("profit", "net_profit", "profit", "selected_revenue"),
    "cash_equivalent_closing_to_assets": ("cashflow", "cash_equivalent_closing", "balance", "assets"),
    "trading_financial_assets_to_assets": ("additional_assets", "trading_financial_assets", "balance", "assets"),
    "other_current_assets_to_assets": ("additional_assets", "other_current_assets", "balance", "assets"),
}


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def raw_ratio(operating, balance, name):
    part, field, denominator_part, denominator_field = DEFINITIONS[name]
    if denominator_field == "selected_revenue":
        denominator_field = next((f for f in ("operating_total_revenue", "operating_revenue")
                                  if operating["profit"]["amounts"][f]["status"] == "NUMERIC"), None)
        if denominator_field is None:
            return None
        if operating["selected_revenue_source_field"] != denominator_field:
            raise ValueError("independent selected source revenue field differs")
    numerator = operating[part]["amounts"][field]
    denominator = balance["amounts"][denominator_field] if denominator_part == "balance" else operating[denominator_part]["amounts"][denominator_field]
    if numerator["status"] != "NUMERIC" or denominator["status"] != "NUMERIC":
        return None
    n, d = Decimal(numerator["value_yuan"]), Decimal(denominator["value_yuan"])
    signed = field in ("net_profit", "operating_net_cashflow")
    if not n.is_finite() or not d.is_finite() or d <= 0 or not signed and n < 0:
        return None
    with localcontext() as context:
        context.prec = 50
        return str(n / d)


def independent_predictions(train, check, market_features, financial_features):
    categories = sorted({r["industry_category"] for r in train})
    if set(r["industry_category"] for r in check) - set(categories):
        raise ValueError("independent check has unseen category")
    indicators = ["industry_category_" + c for c in categories[1:]]
    all_rows = [{**r, "features": {**r["features"], **{f: float(r["industry_category"] == f[-1]) for f in indicators}}} for r in [*train, *check]]
    a, b = all_rows[:len(train)], all_rows[len(train):]
    y = np.asarray([r["target_absolute_return"] for r in train])
    predictions, designs = {}, {}
    definitions = {
        "market": market_features,
        "market_financial": market_features + financial_features,
        "market_industry": market_features + indicators,
        "market_industry_financial": market_features + indicators + financial_features,
    }
    for name, features in definitions.items():
        x, z = ([[r["features"][f] for f in features] for r in rows] for rows in (a, b))
        predictions[name], designs[name] = base_audit.svd_fit(x, y, z)
    groups = defaultdict(list)
    for r in train:
        groups[r["stock_code"]].append(r)
    means = {code: float(np.mean([r["target_absolute_return"] for r in rows])) for code, rows in groups.items()}
    feature_means = {code: np.mean([[r["features"][f] for f in market_features] for r in rows], axis=0) for code, rows in groups.items()}
    if set(r["stock_code"] for r in check) - set(means):
        raise ValueError("independent issuer reference lacks visible training outcomes")
    predictions["training_mean"] = np.full(len(check), np.mean(y))
    predictions["company_training_mean"] = np.asarray([means[r["stock_code"]] for r in check])
    x, z = (np.asarray([[r["features"][f] for f in market_features] for r in rows]) -
            np.asarray([feature_means[r["stock_code"]] for r in rows]) for rows in (train, check))
    centered = y - np.asarray([means[r["stock_code"]] for r in train])
    p, design = base_audit.svd_fit(x, centered, z, intercept=False)
    predictions["company_fixed_market"] = p + predictions["company_training_mean"]
    designs["company_fixed_market_within"] = design
    return predictions, designs, definitions, a, b, categories, {code: len(rows) for code, rows in groups.items()}


def check_comparison(record, baseline, extra, targets, days):
    left = base_audit.independent_metrics(targets, baseline, days)
    right = base_audit.independent_metrics(targets, extra, days)
    for key, value in record["relative_error_improvement"].items():
        base_audit.close(value, 1 - right[key] / left[key], "operating improvement " + key)
    lower = sum(b["mae"] < a["mae"] for a, b in zip(left["daily"], right["daily"]))
    if record["evaluation_dates_with_lower_mae"] != lower:
        raise ValueError("independent lower-error date count differs")
    if len(record["delete_one_evaluation_date"]) != len(set(days)) or {d["deleted_trade_date"] for d in record["delete_one_evaluation_date"]} != set(days):
        raise ValueError("independent delete-date coverage differs")
    ranges = defaultdict(list)
    for deletion in record["delete_one_evaluation_date"]:
        mask = np.asarray([d != deletion["deleted_trade_date"] for d in days])
        a = np.asarray(baseline) - np.asarray(targets)
        b = np.asarray(extra) - np.asarray(targets)
        for key, value in (("pooled_mae_improvement", 1 - np.mean(np.abs(b[mask])) / np.mean(np.abs(a[mask]))),
                           ("pooled_rmse_improvement", 1 - np.sqrt(np.mean(b[mask] ** 2) / np.mean(a[mask] ** 2)))):
            base_audit.close(deletion[key], value, "operating delete-date " + key)
            ranges[key].append(float(value))
    for key, values in ranges.items():
        for actual, expected in zip(record["delete_one_date_improvement_range"][key], [min(values), max(values)]):
            base_audit.close(actual, expected, "operating sensitivity range")


def compute(directory):
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if digest(CONFIG) != PROTOCOL_SHA256:
        raise ValueError("prespecified operating protocol changed")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    panel = json.loads((directory / "panel.json").read_text(encoding="utf-8"))
    result = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    bindings = {**manifest["inputs"], **manifest["code_sha256"], str(directory / "manifest.json"): digest(directory / "manifest.json")}
    for name, record in manifest["artifacts"].items():
        bindings[str(directory / name)] = record["sha256"]
    if set(manifest["artifacts"]) != {"panel.json", "results.json"} or bindings.get(str(CONFIG)) != PROTOCOL_SHA256:
        raise ValueError("operating study manifest binding differs")
    sources = {}
    for name, record in config["inputs"].items():
        path = (CONFIG.parent / record["path"]).resolve()
        if bindings.get(str(path)) != record["sha256"]:
            raise ValueError("operating source is not bound to frozen protocol")
        sources[name] = json.loads(path.read_text(encoding="utf-8"))

    def verify():
        for path, expected in bindings.items():
            if digest(path) != expected:
                raise ValueError("independent operating-study input/producer differs: " + path)

    verify()
    base_dir = (CONFIG.parent / config["inputs"]["base_manifest"]["path"]).resolve().parent
    # Repeat the original independent raw CSV/Decimal/SVD audit, not just trust
    # its status. This reconfirms all base amounts, clocks, targets and features.
    rebuilt_base = base_audit.compute(base_dir)
    if serialize(rebuilt_base) != serialize(sources["base_independent_audit"]):
        raise ValueError("the base full raw-market independent audit no longer reproduces")
    operating = {c["stock_code"]: c for c in sources["operating_states"]["companies"]}
    balances = {c["stock_code"]: c for c in sources["balance_states"]["companies"]}
    base = sources["base_panel"]
    codes = base["parent_stock_codes"]
    if len(codes) != 123 or len(set(codes)) != 123 or set(codes) != set(operating) or set(codes) != set(balances):
        raise ValueError("independent operating cohort differs")
    if panel["split"] != base["split"] or result["split"] != base["split"] or panel["parent_stock_codes"] != codes:
        raise ValueError("independent operating frozen clock differs")
    if set(panel["blocks"]) != set(config["blocks"]) or set(result["blocks"]) != set(config["blocks"]):
        raise ValueError("independent operating block coverage differs")
    base_cfg = json.loads(base_audit.CONFIG.read_text(encoding="utf-8"))
    summaries, total_features, total_predictions = {}, 0, 0
    maximum_difference = 0.0
    for block, features in config["blocks"].items():
        archived_rows = panel["blocks"][block]
        original_rows = base["rows"]
        if len(archived_rows) != len(original_rows):
            raise ValueError("independent operating company-date grid differs")
        eligible = []
        for row, original in zip(archived_rows, original_rows):
            code = original["stock_code"]
            state, balance = operating[code], balances[code]
            if state["status"] != "INDEPENDENT_OPERATING_EXTRACTION_AGREEMENT" or state["agent_signal_enabled"] is not False:
                raise ValueError("independent operating source status differs")
            for part in ("profit", "cashflow"):
                value = state[part]
                if value["period_start"] != "2019-01-01" or value["period_end"] != "2019-09-30" or value["scope"] != balance["scope"]:
                    raise ValueError("independent operating flow period/scope differs")
            if any(state[k] != balance[k] for k in ("stock_code", "source_pdf_sha256", "industry_code", "available_at_proxy", "report_metadata")):
                raise ValueError("independent operating company/source/availability association differs")
            reason = original["paired_exclusion_reason"]
            decimal = None
            if reason is None:
                if datetime.fromisoformat(state["available_at_proxy"]) > datetime.fromisoformat(original["signal_cutoff_at"]):
                    reason = "OPERATING_SOURCE_NOT_YET_AVAILABLE"
                elif not state["generic_nonfinancial_ratios_applicable"]:
                    reason = "OPERATING_SCOPE_NOT_APPLICABLE"
                else:
                    decimal = {f: raw_ratio(state, balance, f) for f in features}
                    if any(value is None for value in decimal.values()):
                        reason, decimal = "MISSING_PRESPECIFIED_OPERATING_BLOCK", None
            expected = {**original, "base_paired_exclusion_reason": original["paired_exclusion_reason"], "paired_exclusion_reason": reason,
                        "operating_source_pdf_sha256": state["source_pdf_sha256"], "operating_available_at_proxy": state["available_at_proxy"],
                        "operating_decimal_inputs": decimal, "block": block}
            for key, value in expected.items():
                if key in ("features", "target_absolute_return", "financial_decimal_inputs"):
                    continue
                if row.get(key) != value:
                    raise ValueError("independent operating row identity/clock/exclusion/Decimal differs: " + key)
            if reason:
                if any(row[k] is not None for k in ("features", "target_absolute_return", "financial_decimal_inputs", "operating_decimal_inputs")):
                    raise ValueError("excluded operating row was backfilled")
                continue
            rebuilt_features = {**original["features"], **{f: float(Decimal(v)) for f, v in decimal.items()}}
            if set(rebuilt_features) != set(row["features"]) or row["financial_decimal_inputs"] != original["financial_decimal_inputs"]:
                raise ValueError("independent fixed operating feature specification differs")
            for f, value in rebuilt_features.items():
                base_audit.close(row["features"][f], value, "independent operating feature " + f, tolerance=1e-12)
                total_features += 1
            base_audit.close(row["target_absolute_return"], original["target_absolute_return"], "operating target", tolerance=1e-12)
            eligible.append({**row, "features": rebuilt_features})
        train, check = ([r for r in eligible if r["partition"] == part] for part in ("train", "evaluation"))
        record = result["blocks"][block]
        if len(train) != record["training_rows"] or len(check) != record["evaluation_rows"]:
            raise ValueError("independent operating paired split coverage differs")
        pred, designs, definitions, a, b, categories, counts = independent_predictions(train, check, base_cfg["market_features"], base_cfg["financial_features"])
        operating_features = definitions["market_industry_financial"] + features
        name = "market_industry_financial_operating"
        pred[name], designs[name] = base_audit.svd_fit([[r["features"][f] for f in operating_features] for r in a],
                                                    [r["target_absolute_return"] for r in train],
                                                    [[r["features"][f] for f in operating_features] for r in b])
        definitions[name] = operating_features
        if record["industry_control_categories"] != categories or record["company_training_row_counts"] != counts:
            raise ValueError("independent operating category or issuer training coverage differs")
        if set(record["models"]) != set(pred) or len(record["evaluation_predictions"]) != len(check):
            raise ValueError("independent operating model/prediction coverage differs")
        for model, fs in definitions.items():
            fit, design = record["models"][model]["fit"], designs[model]
            if fit["features"] != fs or fit["regularization"] != "none" or fit["intercept"] is not True:
                raise ValueError("independent operating fit specification differs")
            for i, f in enumerate(fs):
                base_audit.close(fit["training_means"][f], design["means"][i], "operating training mean")
                base_audit.close(fit["training_sample_scales"][f], design["scales"][i], "operating training scale")
            for actual, expected in zip(fit["standardized_coefficients"], design["coefficients"], strict=True):
                base_audit.close(actual, expected, "operating SVD coefficient", tolerance=1e-9)
        targets, days = [r["target_absolute_return"] for r in check], [r["trade_date"] for r in check]
        for model, predictions in pred.items():
            for i, value in enumerate(predictions):
                archived = record["evaluation_predictions"][i]
                if (archived["stock_code"], archived["trade_date"]) != (check[i]["stock_code"], check[i]["trade_date"]):
                    raise ValueError("independent operating evaluation identity differs")
                base_audit.close(archived["predictions"][model], value, "operating SVD forecast", tolerance=1e-9)
                base_audit.close(archived["target_absolute_return"], targets[i], "operating forecast target", tolerance=1e-12)
                maximum_difference = max(maximum_difference, abs(archived["predictions"][model] - float(value)))
                total_predictions += 1
            base_audit.check_metrics(record["models"][model]["metrics"], base_audit.independent_metrics(targets, predictions, days), model)
        check_comparison(record["operating_comparison"], pred["market_industry_financial"], pred[name], targets, days)
        if record["block_exclusions"] != dict(Counter(r["paired_exclusion_reason"] for r in archived_rows if r["paired_exclusion_reason"])):
            raise ValueError("independent operating exclusion counts differ")
        summaries[block] = {"company_date_slots": len(archived_rows), "training_rows": len(train), "evaluation_rows": len(check),
                            "designs": {name: {k: v for k, v in d.items() if k not in ("means", "scales", "coefficients")} for name, d in designs.items()}}
    verify()
    if panel["agent_signal_enabled"] is not False or result["agent_signal_enabled"] is not False:
        raise ValueError("independent operating study may not enable an Agent response")
    return {"pipeline_version": "independent-source-amount-Decimal-SVD-operating-increment-audit-v1", "status": "PASS", "blocks": summaries,
            "feature_cells_checked": total_features, "forecast_cells_checked": total_predictions,
            "maximum_forecast_absolute_difference": maximum_difference, "base_raw_market_audit_repeated_byte_identically": True,
            "source_and_output_bindings": dict(sorted(bindings.items())), "audit_code_sha256": digest(__file__),
            "numpy_version": np.__version__, "agent_signal_enabled": False,
            "interpretation": "All block identities, exclusions, signed source amount ratios, training clocks, SVD designs, predictions, metrics and delete-date sensitivities independently agree. Original full raw-market base audit reproduced. No economic adoption or blind validation follows from implementation agreement."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("independent operating audit requires a direct research_outputs directory")
    result = compute(directory)
    raw = serialize(result)
    path = directory / "independent_audit.json"
    if args.audit_existing:
        if path.read_bytes() != raw:
            raise ValueError("independent operating audit differs from frozen reconstruction")
        print("Independent operating increment audit rebuilt byte-identically.")
    else:
        with path.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({k: result[k] for k in ("status", "feature_cells_checked", "forecast_cells_checked", "maximum_forecast_absolute_difference")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
