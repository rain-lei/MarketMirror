"""Independent whole-message convolution and raw-source receipt reconstruction.

No whole-coverage producer import. Decimal density integration is different
from the production closed formula; the multiplier is tested against its target.
"""
from functools import lru_cache
import math

from .audit_public_information_risk import independent_second_moment, expected_receipt as prior_receipt
from .audit_issuer_valuation import expected_receipt as private_receipt
from .audit_temporal_inputs import independent_paths
from .audit_temporal_residual_coupling import independent_windows, independent_selection, independent_row
from .temporal_information_study import require, read, ROOT

VERSION = "whole-information-matched-received-risk-v1"
BUDGET_FIELDS = {"whole_raw_multiplier", "reference_private_receipt_probability",
    "baseline_received_second_moment_bps2", "whole_public_received_second_moment_bps2",
    "matching_absolute_error_bps2", "matching_tolerance_bps2"}


@lru_cache(maxsize=20000)
def verify_numbers(residual, market, probability, cap, scale, declared_baseline, declared_public,
                   declared_error, declared_tolerance):
    vals = (residual, market, probability, cap, scale, declared_baseline, declared_public,
        declared_error, declared_tolerance)
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in vals):
        raise ValueError("invalid independent whole received budget")
    if probability > 1 or scale > 1 or cap <= 0:
        raise ValueError("independent whole probability, scale or cap differs")
    baseline = probability * independent_second_moment(residual, market, cap)
    received = independent_second_moment(scale * residual, scale * market, cap)
    tolerance = max(1e-300, baseline) * 5e-12 + 16 * math.ulp(baseline)
    if (abs(declared_baseline - baseline) > tolerance or abs(declared_public - received) > tolerance
            or abs(received - baseline) > tolerance
            or not math.isclose(declared_tolerance, tolerance, rel_tol=5e-12, abs_tol=1e-310)
            or declared_error != abs(declared_public - declared_baseline)
            or probability == 0 and scale != 0
            or baseline == 0 and scale != 0
            or probability == 1 and baseline > 0 and scale != 1):
        raise ValueError("independent whole post-cap second moment mismatch")
    return {"independent_baseline_bps2": baseline, "independent_whole_public_bps2": received,
        "independent_matching_error_bps2": abs(received - baseline)}


def verify_budget(row, parameters):
    risk, declared = row["risk_budget"], row["whole_information_budget"]
    if set(declared) != BUDGET_FIELDS or any(type(v) not in (int, float) or not math.isfinite(v)
            or v < 0 for v in declared.values()):
        raise ValueError("whole budget schema or value type differs")
    if declared["reference_private_receipt_probability"] != parameters["information_probability_bps"] / 10000:
        raise ValueError("whole reference receipt probability differs")
    return verify_numbers(risk["residual_amplitude_bps"], abs(risk["signed_market_amplitude_bps"]),
        declared["reference_private_receipt_probability"], parameters["max_shift_bps"],
        declared["whole_raw_multiplier"], declared["baseline_received_second_moment_bps2"],
        declared["whole_public_received_second_moment_bps2"], declared["matching_absolute_error_bps2"],
        declared["matching_tolerance_bps2"])


def expected_receipt(row, parameters, stock, owner):
    if "whole_information_budget" not in row:
        return prior_receipt(row, parameters, stock, owner)
    old = private_receipt(row, parameters, stock, owner)
    scale = row["whole_information_budget"]["whole_raw_multiplier"]
    residual = row["risk_budget"]["residual_raw_shift_bps"] * scale
    market = row["risk_budget"]["market_raw_shift_bps"] * scale
    combined = residual + market
    applied = min(parameters["max_shift_bps"], max(-parameters["max_shift_bps"], combined))
    return {"received": True, "receipt_sha256": old["receipt_sha256"],
        "information_probability_bps": parameters["information_probability_bps"],
        "valuation_shift_bps": applied, "applied_valuation_shift_bps": applied,
        "applied_belief_signal": applied / parameters["belief_scale_bps"],
        "whole_information_receipt": {"version": VERSION, "reference_private_receipt": old,
            "public_received": True, "public_residual_raw_shift_bps": residual,
            "public_market_raw_shift_bps": market, "combined_raw_shift_bps": combined,
            "was_capped": combined != applied, "received_risk_budget": dict(row["whole_information_budget"])}}


def verify_receipt(actual, row, parameters, stock, owner):
    if actual != expected_receipt(row, parameters, stock, owner) or type(actual["received"]) is not bool:
        raise ValueError("whole received message or reference mask differs")
    detail = actual.get("whole_information_receipt")
    if detail is not None:
        reference = detail["reference_private_receipt"]
        if (type(reference["received"]) is not bool or type(detail["public_received"]) is not bool
                or type(detail["was_capped"]) is not bool
                or not reference["received"] and reference["valuation_shift_bps"] is not None):
            raise ValueError("whole receipt flags or original missingness differs")


def verify_prepared(cfg, saved, risk, features, seed):
    legacy = independent_paths(cfg, risk, features, seed)
    windows = independent_windows(cfg, features)
    ready = read(ROOT / cfg["residual_rank_preparation_path"])
    require(ready["status"] == "PASS_FULL_PAST_ONLY_RESIDUAL_RANK_PREPARATION"
        and ready["windows"] == windows, "whole coverage past rank preparation differs")
    date_seed = cfg["residual_common_seed_namespace"] + seed["innovation_seed"]
    choices = [independent_selection(w, date_seed) for w in windows]
    settings = {"version": "past-only-global-residual-rank-coupling-v1", "common_date_seed": date_seed,
        "universe_stocks": sorted(features), "windows": choices}
    counts = {"independently_rebuilt_residual_rank_windows": len(cfg["stocks"]) * len(cfg["dates"]),
        "independently_verified_common_date_draws": len(cfg["dates"]),
        "independently_verified_coupled_innovations": 0, "independently_rebuilt_budget_rows": 0,
        "independently_integrated_whole_public_budget_rows": 0}
    required = set()
    for dependence in ("independent", "historical_rank"):
        for mode in ("market_independent", "market_shared"):
            key = dependence + "_" + mode + "_whole_public"
            required.add(key)
            expected = legacy[mode + "_masked"]
            if dependence == "historical_rank":
                expected = {**expected, "residual_coupling_parameters": settings,
                    "by_stock": {s: [independent_row(r, w, c, s, expected["parameters"])
                        for r, w, c in zip(rows, windows, choices, strict=True)]
                        for s, rows in expected["by_stock"].items()}}
            public = saved[key]
            require(public["whole_information_coverage_parameters"] == {"version": VERSION, "delivery": "whole_public"},
                "whole coverage header differs")
            plain = {k: v for k, v in public.items() if k != "whole_information_coverage_parameters"}
            plain["by_stock"] = {s: [{k: v for k, v in r.items() if k != "whole_information_budget"} for r in rows]
                for s, rows in public["by_stock"].items()}
            require(plain == expected, "whole coverage altered known source, amplitudes or original innovations")
            for rows in public["by_stock"].values():
                for row in rows:
                    verify_budget(row, public["parameters"])
                    counts["independently_rebuilt_budget_rows"] += 1
                    counts["independently_integrated_whole_public_budget_rows"] += 1
                    counts["independently_verified_coupled_innovations"] += int(dependence == "historical_rank")
    require(set(saved) == required, "extra or duplicate whole coverage path")
    return counts
