"""Independent raw-CSV/Decimal/SVD audit; no production diagnostic imports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_financial_increment_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_financial_increment_2019_v1"
PROTOCOL_SHA256 = "3d06c61228e8aaa2fd3375bef898190e595cf5841c5a816d9b51e0cbb7b0734b"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("independent read found duplicate or absent CSV headers")
        rows = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError("independent read found malformed CSV rows")
    return rows


def close(actual, expected, label, *, tolerance=1e-10):
    if (not math.isfinite(float(actual)) or not math.isfinite(float(expected)) or
            not math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)):
        raise ValueError(f"independent reconstruction differs: {label}")


def independent_metrics(targets, predictions, days):
    y, p = np.asarray(targets, dtype=float), np.asarray(predictions, dtype=float)
    error = p - y
    daily = []
    for day in sorted(set(days)):
        mask = np.asarray([d == day for d in days])
        daily.append({"trade_date": day, "company_rows": int(mask.sum()),
                      "mae": float(np.mean(np.abs(error[mask]))), "mse": float(np.mean(error[mask] ** 2)),
                      "mean_target": float(np.mean(y[mask])), "mean_prediction": float(np.mean(p[mask]))})
    delta = np.asarray([r["mean_prediction"] - r["mean_target"] for r in daily])
    return {"rows": len(y), "sessions": len(daily), "pooled_mae": float(np.mean(np.abs(error))),
            "pooled_rmse": float(np.sqrt(np.mean(error ** 2))),
            "equal_session_mae": float(np.mean([r["mae"] for r in daily])),
            "daily_mean_target_mae": float(np.mean(np.abs(delta))),
            "daily_mean_target_rmse": float(np.sqrt(np.mean(delta ** 2))),
            "negative_prediction_count": int((p < 0).sum()), "daily": daily}


def check_metrics(recorded, calculated, name):
    for key, value in calculated.items():
        if key == "daily":
            if len(value) != len(recorded[key]):
                raise ValueError("independent daily metric coverage differs")
            for a, b in zip(recorded[key], value, strict=True):
                for field in b:
                    if field in ("trade_date", "company_rows"):
                        if a[field] != b[field]:
                            raise ValueError("independent daily metric identity differs")
                    else:
                        close(a[field], b[field], name + "." + field)
        elif key in ("rows", "sessions", "negative_prediction_count"):
            if recorded[key] != value:
                raise ValueError("independent metric count differs")
        else:
            close(recorded[key], value, name + "." + key)


def svd_fit(train_x, train_y, check_x, *, intercept=True):
    x, y, z = (np.asarray(v, dtype=float) for v in (train_x, train_y, check_x))
    means = x.mean(axis=0) if intercept else np.zeros(x.shape[1])
    scales = x.std(axis=0, ddof=1)
    if not np.isfinite(x).all() or not np.isfinite(y).all() or not np.isfinite(z).all() or (scales <= 1e-12).any():
        raise ValueError("independent training design is nonfinite or lacks variation")
    a, b = (x - means) / scales, (z - means) / scales
    if intercept:
        a, b = np.column_stack((np.ones(len(x)), a)), np.column_stack((np.ones(len(z)), b))
    rank = int(np.linalg.matrix_rank(a, tol=1e-10))
    coefficients, _, solver_rank, _ = np.linalg.lstsq(a, y, rcond=1e-12)
    if rank != a.shape[1] or solver_rank != rank:
        raise ValueError("independent unpenalized design is rank deficient")
    return b @ coefficients, {"training_rows": len(x), "columns": a.shape[1], "rank": rank,
                              "condition_number": float(np.linalg.cond(a)), "means": means,
                              "scales": scales, "coefficients": coefficients}


def compute(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    panel = json.loads((directory / "panel.json").read_text(encoding="utf-8"))
    result = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if digest(CONFIG) != PROTOCOL_SHA256 or manifest["inputs"][str(CONFIG)] != PROTOCOL_SHA256:
        raise ValueError("prespecified financial diagnostic protocol changed")
    bindings = {**manifest["inputs"], **manifest["code_sha256"],
                str(directory / "manifest.json"): digest(directory / "manifest.json")}
    for name, record in manifest["artifacts"].items():
        if name not in {"panel.json", "results.json"}:
            raise ValueError("independent audit found unexpected frozen artifacts")
        bindings[str(directory / name)] = record["sha256"]
    if set(manifest["artifacts"]) != {"panel.json", "results.json"}:
        raise ValueError("independent audit lacks paired panel/results bindings")

    def verify():
        if any(digest(Path(p)) != expected for p, expected in bindings.items()):
            raise ValueError("independent audit source or producer changed")

    verify()
    sources = {name: json.loads((CONFIG.parent / value["path"]).resolve().read_text(encoding="utf-8"))
               for name, value in cfg["inputs"].items()}
    for value in cfg["inputs"].values():
        path = (CONFIG.parent / value["path"]).resolve()
        if bindings.get(str(path)) != value["sha256"]:
            raise ValueError("independent source is not bound to the frozen protocol")
    financial, industry, download = (sources[n] for n in ("financial_states", "industry_memberships", "download_manifest"))
    states = {r["stock_code"]: r for r in financial["companies"]}
    industries = {r["stock_code"]: r for r in industry["cohort_memberships"]}
    codes = sorted(sources["cohort_archive"]["result"]["sample"]["selected_stock_codes"])
    if (len(codes) != 123 or len(set(codes)) != 123 or set(codes) != set(states) or set(codes) != set(industries) or
            len(states) != len(financial["companies"]) or len(industries) != len(industry["cohort_memberships"]) or
            financial["agent_signal_enabled"] is not False):
        raise ValueError("independent financial/industry/cohort identity differs")
    raw_dir = (CONFIG.parent / cfg["inputs"]["download_manifest"]["path"]).resolve().parent
    calendar = [r["trade_date"] for r in read_csv(raw_dir / "calendar.csv")]
    if calendar != sorted(set(calendar)) or calendar != sources["market_manifest"]["session_dates"]:
        raise ValueError("independent exchange calendar differs")
    dates = [d for d in calendar if cfg["period_start"] <= d <= cfg["period_end"]]
    first_check = dates[cfg["nominal_training_sessions"]]
    fit_day = calendar[calendar.index(first_check) - 2]
    split = {"dates": dates, "training_dates": [d for d in dates[:30] if d <= fit_day],
             "purged_dates": [d for d in dates[:30] if d > fit_day], "evaluation_dates": dates[30:],
             "model_fit_cutoff_at": fit_day + "T15:00:00+08:00"}
    if len(dates) != 43 or len(split["training_dates"]) != 29 or panel["split"] != split or result["split"] != split:
        raise ValueError("independent frozen training/outcome clock differs")
    queries = [q for q in download["queries"] if q["function"] == "query_history_k_data_plus"]
    query_by_symbol = {q["symbol"]: q for q in queries}
    if len(query_by_symbol) != len(queries):
        raise ValueError("independent provider queries contain duplicates")
    benchmark_rows = read_csv(raw_dir / query_by_symbol[download["benchmark_symbol"]]["raw_file"])
    benchmark_close = {r["date"]: float(r["close"]) for r in benchmark_rows}
    if len(benchmark_close) != len(benchmark_rows) or sorted(benchmark_close) != calendar:
        raise ValueError("independent raw benchmark/calendar differs")
    benchmark_returns = {d: benchmark_close[d] / benchmark_close[calendar[i - 1]] - 1
                         for i, d in enumerate(calendar) if i}
    returns, amounts = {}, {}
    for stock in codes:
        options = [q for q in queries if q["symbol"].split(".")[1] == stock]
        if len(options) != 1:
            raise ValueError("independent issuer raw-query identity is ambiguous")
        q = options[0]
        if q["adjustflag"] != "1" or q["frequency"] != "d":
            raise ValueError("independent stock return basis differs")
        raw = read_csv(raw_dir / q["raw_file"])
        if len(raw) != q["rows"]:
            raise ValueError("independent raw issuer row count differs")
        for r in raw:
            key = (stock, r["date"])
            if key in returns or r["code"] != q["symbol"] or r["adjustflag"] != "1":
                raise ValueError("independent raw issuer identity/duplication differs")
            returns[key] = float(Decimal(r["pctChg"]) / 100)
            amounts[key] = float(Decimal(r["amount"]))
    prepared_dir = (CONFIG.parent / cfg["inputs"]["market_manifest"]["path"]).resolve().parent
    checked_returns = 0
    for r in read_csv(prepared_dir / "market_daily.csv"):
        if r["stock_code"] in states:
            key = (r["stock_code"], r["trade_date"])
            close(r["stock_return"], returns[key], "raw stock return", tolerance=1e-12)
            close(r["market_return"], benchmark_returns[r["trade_date"]], "raw benchmark return", tolerance=1e-12)
            checked_returns += 1
    reconstructed, feature_checks = [], 0
    archived = {(r["stock_code"], r["trade_date"]): r for r in panel["rows"]}
    if len(archived) != 123 * 43 or len(panel["rows"]) != len(archived) or panel["parent_stock_codes"] != codes:
        raise ValueError("independent company-date grid differs")
    for day in dates:
        i = calendar.index(day)
        cutoff_day = calendar[i - 2]
        cutoff = datetime.fromisoformat(cutoff_day + "T15:00:00+08:00")
        for stock in codes:
            state, sector = states[stock], industries[stock]
            r = archived[(stock, day)]
            partition = "train" if day in split["training_dates"] else "purged" if day in split["purged_dates"] else "evaluation"
            expected_identity = {"partition": partition, "signal_cutoff_date": cutoff_day,
                                 "signal_cutoff_at": cutoff.isoformat(), "execution_reference_date": calendar[i - 1],
                                 "financial_available_at_proxy": state["available_at_proxy"],
                                 "industry_available_at_proxy": sector["available_at_proxy"],
                                 "industry_code": sector["industry_code"], "industry_category": sector["category_code"]}
            if any(r[k] != v for k, v in expected_identity.items()) or state["industry_code"] != sector["industry_code"]:
                raise ValueError("independent information-clock/issuer/industry identity differs")
            reason = ("FINANCIAL_INSTITUTION_OR_UNVERIFIED_SCOPE" if not state["generic_nonfinancial_ratios_applicable"] else
                      "FINANCIAL_SOURCE_NOT_YET_AVAILABLE" if datetime.fromisoformat(state["available_at_proxy"]) > cutoff else
                      "INDUSTRY_SOURCE_NOT_YET_AVAILABLE" if datetime.fromisoformat(sector["available_at_proxy"]) > cutoff else
                      "MISSING_FIXED_FINANCIAL_SOURCE_VALUE" if state["amounts"]["assets"]["status"] != "NUMERIC" or
                      any(state["ratios"][f]["status"] != "OBSERVED_RATIO" for f in cfg["financial_features"][1:]) else None)
            if r["paired_exclusion_reason"] != reason:
                raise ValueError("independent paired exclusion differs")
            if reason:
                if any(r[k] is not None for k in ("features", "financial_decimal_inputs", "target_absolute_return")):
                    raise ValueError("excluded source was backfilled into the diagnostic")
                continue
            window = calendar[i - 21:i - 1]
            stock_history = np.asarray([returns[(stock, d)] for d in window])
            benchmark_history = np.asarray([benchmark_returns[d] for d in window])
            bv = float(np.std(benchmark_history, ddof=1))
            assets = state["amounts"]["assets"]["value_yuan"]
            with localcontext() as context:
                context.prec = 50
                liabilities_ratio = Decimal(state["amounts"]["liabilities"]["value_yuan"]) / Decimal(assets)
                money_ratio = Decimal(state["amounts"]["money_funds"]["value_yuan"]) / Decimal(assets)
            features = {"stock_volatility_20": float(np.std(stock_history, ddof=1)), "benchmark_volatility_20": bv,
                        "absolute_benchmark_momentum_5": abs(float(np.tanh(np.sum(benchmark_history[-5:]) / (max(1e-6, bv) * np.sqrt(5))))),
                        "log_amount_surprise_20": float(np.log1p(amounts[(stock, cutoff_day)]) - np.median(
                            np.log1p([amounts[(stock, d)] for d in calendar[i - 22:i - 2]]))),
                        "log_reported_assets_yuan": float(np.log(float(assets))),
                        "total_liabilities_to_assets": float(liabilities_ratio), "money_funds_to_assets": float(money_ratio)}
            if set(features) != set(r["features"]) or r["financial_decimal_inputs"] != {
                    "assets_value_yuan": assets, "total_liabilities_to_assets": str(liabilities_ratio), "money_funds_to_assets": str(money_ratio)}:
                raise ValueError("independent fixed financial block differs")
            for name, v in features.items():
                close(r["features"][name], v, "feature " + name, tolerance=1e-12)
                feature_checks += 1
            target = abs(returns[(stock, day)])
            close(r["target_absolute_return"], target, "raw target", tolerance=1e-12)
            reconstructed.append({**r, "features": features, "target_absolute_return": target})
    train = [r for r in reconstructed if r["partition"] == "train"]
    check = [r for r in reconstructed if r["partition"] == "evaluation"]
    if len(train) != result["training_rows"] or len(check) != result["evaluation_rows"]:
        raise ValueError("independent paired training/evaluation coverage differs")
    if Counter(r["paired_exclusion_reason"] for r in panel["rows"] if r["paired_exclusion_reason"]) != result["excluded_slot_counts"]:
        raise ValueError("independent total exclusion counts differ")
    categories = sorted({r["industry_category"] for r in train})
    indicators = ["industry_category_" + c for c in categories[1:]]
    for row in [*train, *check]:
        row["features"].update({name: float(row["industry_category"] == name[-1]) for name in indicators})
    if result["industry_reference_category"] != categories[0] or result["industry_control_categories"] != categories:
        raise ValueError("independent historical category coding differs")
    y = np.asarray([r["target_absolute_return"] for r in train])
    actual = [r["target_absolute_return"] for r in check]
    days = [r["trade_date"] for r in check]
    matrices, all_predictions, numeric_designs = {}, {}, {}
    for name, fs in (("market", cfg["market_features"]), ("market_financial", cfg["market_features"] + cfg["financial_features"]),
                     ("market_industry", cfg["market_features"] + indicators),
                     ("market_industry_financial", cfg["market_features"] + indicators + cfg["financial_features"])):
        x, z = ([[r["features"][f] for f in fs] for r in rows] for rows in (train, check))
        pred, design = svd_fit(x, y, z)
        fit = result["models"][name]["fit"]
        if fit["features"] != fs or fit["regularization"] != "none" or fit["intercept"] is not True:
            raise ValueError("independent model feature specification differs")
        for index, f in enumerate(fs):
            close(fit["training_means"][f], design["means"][index], "training mean " + f)
            close(fit["training_sample_scales"][f], design["scales"][index], "training scale " + f)
        for a, b in zip(fit["standardized_coefficients"], design["coefficients"], strict=True):
            close(a, b, "SVD coefficient", tolerance=1e-9)
        numeric_designs[name] = {k: v for k, v in design.items() if k not in ("means", "scales", "coefficients")}
        all_predictions[name] = pred
    per_code = defaultdict(list)
    for r in train:
        per_code[r["stock_code"]].append(r)
    means = {code: float(np.mean([r["target_absolute_return"] for r in rows])) for code, rows in per_code.items()}
    mean_features = {code: np.mean([[r["features"][f] for f in cfg["market_features"]] for r in rows], axis=0)
                     for code, rows in per_code.items()}
    if result["company_training_row_counts"] != {code: len(rows) for code, rows in per_code.items()}:
        raise ValueError("independent issuer training history counts differ")
    all_predictions["training_mean"] = np.full(len(check), np.mean(y))
    all_predictions["company_training_mean"] = np.asarray([means[r["stock_code"]] for r in check])
    x, z = (np.asarray([[r["features"][f] for f in cfg["market_features"]] for r in rows]) - np.asarray(
                [mean_features[r["stock_code"]] for r in rows]) for rows in (train, check))
    within_target = y - np.asarray([means[r["stock_code"]] for r in train])
    within_pred, within_design = svd_fit(x, within_target, z, intercept=False)
    all_predictions["company_fixed_market"] = within_pred + all_predictions["company_training_mean"]
    numeric_designs["company_fixed_market_within"] = {k: v for k, v in within_design.items() if k not in ("means", "scales", "coefficients")}
    if set(result["models"]) != set(all_predictions) or len(result["evaluation_predictions"]) != len(check):
        raise ValueError("independent model/prediction coverage differs")
    prediction_checks, max_difference = 0, 0.0
    scored = {}
    for name, predictions in all_predictions.items():
        for i, r in enumerate(check):
            archived_prediction = result["evaluation_predictions"][i]
            if (archived_prediction["stock_code"], archived_prediction["trade_date"]) != (r["stock_code"], r["trade_date"]):
                raise ValueError("independent evaluation row identity differs")
            value = archived_prediction["predictions"][name]
            close(value, predictions[i], "SVD prediction " + name, tolerance=1e-9)
            close(archived_prediction["target_absolute_return"], actual[i], "prediction target", tolerance=1e-12)
            max_difference = max(max_difference, abs(value - float(predictions[i])))
            prediction_checks += 1
        scored[name] = independent_metrics(actual, predictions, days)
        check_metrics(result["models"][name]["metrics"], scored[name], name)
    for label, (base, extra) in (("primary_industry_controlled", cfg["primary_comparison"]),
                                 ("secondary_market_only", cfg["secondary_comparison"])):
        record = result["comparisons"][label]
        for key, observed in record["relative_error_improvement"].items():
            close(observed, 1 - scored[extra][key] / scored[base][key], label + key)
        for deletion in record["delete_one_evaluation_date"]:
            mask = np.asarray([d != deletion["deleted_trade_date"] for d in days])
            a = np.asarray(all_predictions[base]) - np.asarray(actual)
            b = np.asarray(all_predictions[extra]) - np.asarray(actual)
            close(deletion["pooled_mae_improvement"], 1 - np.mean(np.abs(b[mask])) / np.mean(np.abs(a[mask])), "delete-date MAE")
            close(deletion["pooled_rmse_improvement"], 1 - np.sqrt(np.mean(b[mask] ** 2) / np.mean(a[mask] ** 2)), "delete-date RMSE")
    if panel["agent_signal_enabled"] is not False or result["agent_signal_enabled"] is not False:
        raise ValueError("independent diagnostic must not enable an Agent signal")
    verify()
    return {"pipeline_version": "independent-raw-Decimal-SVD-financial-increment-audit-v1", "status": "PASS",
            "company_date_slots": len(archived), "paired_feature_cells": feature_checks,
            "raw_prepared_company_return_pairs": checked_returns, "training_rows": len(train), "evaluation_rows": len(check),
            "paired_forecast_cells": prediction_checks, "maximum_forecast_absolute_difference": max_difference,
            "designs": numeric_designs, "numpy_version": np.__version__, "model_fit_cutoff_at": split["model_fit_cutoff_at"],
            "source_and_output_bindings": dict(sorted(bindings.items())), "audit_code_sha256": digest(Path(__file__)),
            "agent_signal_enabled": False,
            "interpretation": "Independent raw market CSVs, benchmark close ratios, Decimal financial ratios, NumPy windows and SVD fits agree with the complete frozen paired study. This proves implementation agreement, not economic usefulness or blind-validation performance."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if directory.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("independent audit requires a direct research_outputs directory")
    result = compute(directory)
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    path = directory / "independent_audit.json"
    if args.audit_existing:
        if path.read_bytes() != raw:
            raise ValueError("independent financial-increment audit differs from frozen reconstruction")
        print("Independent full-panel financial increment audit rebuilt byte-identically.")
    else:
        with path.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({k: result[k] for k in ("status", "company_date_slots", "paired_feature_cells", "paired_forecast_cells", "maximum_forecast_absolute_difference")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
