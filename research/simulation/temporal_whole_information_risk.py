"""Public delivery of both raw components with matched ideal received risk.

Scaling precedes the cap. This changes information coverage, not the historical
source amplitudes, investor rules, residual dependence or realized price risk.
"""
from functools import lru_cache
import math

from . import temporal_residual_coupling as previous
from .public_information_risk import clipped_uniform_second_moment, number
from .issuer_valuation import message_receipt as private_receipt
from .issuer_valuation import respond_issuer_background as private_background
from .issuer_valuation import strategy_quote_terms

VERSION = "whole-information-matched-received-risk-v1"


@lru_cache(maxsize=20000)
def _matched_numbers(residual, market, probability, cap):
    b, a = number(residual, "residual amplitude"), number(market, "market amplitude")
    p, c = number(probability, "receipt probability"), number(cap, "cap")
    if p > 1 or c <= 0:
        raise ValueError("whole coverage requires probability in [0,1] and positive cap")
    baseline = p * clipped_uniform_second_moment(b, a, c)
    if p == 0 or baseline == 0:
        multiplier = 0.0
    elif p == 1:
        multiplier = 1.0
    else:
        lower, upper = 0.0, 1.0
        for _ in range(72):
            middle = (lower + upper) / 2
            value = clipped_uniform_second_moment(middle * b, middle * a, c)
            if value < baseline:
                lower = middle
            else:
                upper = middle
        multiplier = (lower + upper) / 2
    received = clipped_uniform_second_moment(multiplier * b, multiplier * a, c)
    tolerance = max(1e-300, baseline) * 5e-12 + 16 * math.ulp(baseline)
    if abs(received - baseline) > tolerance:
        raise ValueError("whole post-cap expected received risk failed to match")
    return {"whole_raw_multiplier": multiplier, "reference_private_receipt_probability": p,
        "baseline_received_second_moment_bps2": baseline,
        "whole_public_received_second_moment_bps2": received,
        "matching_absolute_error_bps2": abs(received - baseline),
        "matching_tolerance_bps2": tolerance}


def received_budget(residual, market, probability, cap):
    # Validate before the cache: bool and float have equal Python cache keys.
    args = [number(v, n) for v, n in zip((residual, market, probability, cap),
        ("residual amplitude", "market amplitude", "receipt probability", "cap"), strict=True)]
    return dict(_matched_numbers(*args))


def with_whole_delivery(path):
    if not isinstance(path, dict) or "risk_budget_parameters" not in path or any(
            k in path for k in ("information_coverage_parameters", "whole_information_coverage_parameters")):
        raise ValueError("whole coverage requires a plain temporal risk path")
    parameters = path["parameters"]
    result = {**path, "whole_information_coverage_parameters": {"version": VERSION, "delivery": "whole_public"}}
    result["by_stock"] = {}
    for stock, rows in path["by_stock"].items():
        added = []
        for row in rows:
            if "whole_information_budget" in row or "public_information_budget" in row:
                raise ValueError("message already contains a coverage budget")
            risk = row["risk_budget"]
            added.append({**row, "whole_information_budget": received_budget(risk["residual_amplitude_bps"],
                abs(risk["signed_market_amplitude_bps"]), parameters["information_probability_bps"] / 10000,
                parameters["max_shift_bps"])})
        result["by_stock"][stock] = added
    return result


def build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed, delivery,
               coupling_seed=None):
    if delivery in ("masked", "public"):
        return previous.build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed,
            delivery, coupling_seed)
    if delivery != "whole_public":
        raise ValueError("explicit whole or previous coverage required")
    plain = previous.build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed,
        "masked", coupling_seed)
    return with_whole_delivery(plain)


def validate_issuer_valuation(assets, calendar, path):
    if not isinstance(path, dict) or "whole_information_coverage_parameters" not in path:
        return previous.validate_issuer_valuation(assets, calendar, path)
    plain = {k: v for k, v in path.items() if k != "whole_information_coverage_parameters"}
    plain["by_stock"] = {s: [{k: v for k, v in r.items() if k != "whole_information_budget"} for r in rows]
        for s, rows in path["by_stock"].items()}
    previous.validate_issuer_valuation(assets, calendar, plain)
    for rows in path["by_stock"].values():
        for row in rows:
            declared = row.get("whole_information_budget")
            if not isinstance(declared, dict) or any(type(v) not in (int, float)
                    or not math.isfinite(v) for v in declared.values()):
                raise ValueError("whole budget requires finite nonboolean numbers")
    if path != with_whole_delivery(plain):
        raise ValueError("whole coverage source, schema or matched risk differs")
    return path


def message_receipt(row, parameters, stock, owner):
    if "whole_information_budget" not in row:
        return previous.message_receipt(row, parameters, stock, owner)
    reference = private_receipt(row, parameters, stock, owner)
    budget, risk = row["whole_information_budget"], row["risk_budget"]
    scale = budget["whole_raw_multiplier"]
    residual = scale * risk["residual_raw_shift_bps"]
    market = scale * risk["market_raw_shift_bps"]
    raw = residual + market
    cap = parameters["max_shift_bps"]
    shift = max(-cap, min(cap, raw))
    return {"received": True, "receipt_sha256": reference["receipt_sha256"],
        "information_probability_bps": parameters["information_probability_bps"],
        "valuation_shift_bps": shift, "applied_valuation_shift_bps": shift,
        "applied_belief_signal": shift / parameters["belief_scale_bps"],
        "whole_information_receipt": {"version": VERSION, "reference_private_receipt": reference,
            "public_received": True, "public_residual_raw_shift_bps": residual,
            "public_market_raw_shift_bps": market, "combined_raw_shift_bps": raw,
            "was_capped": raw != shift, "received_risk_budget": dict(budget)}}


def respond_issuer_background(demand, receipt, price, bounds, venue, background, shared_shock, response):
    detail = receipt.get("whole_information_receipt")
    if detail is None:
        return previous.respond_issuer_background(demand, receipt, price, bounds, venue, background,
            shared_shock, response)
    if not detail["reference_private_receipt"]["received"] and detail["combined_raw_shift_bps"] == 0:
        # A zero newly delivered message must not trigger absent baseline repricing.
        result = private_background(demand, {**receipt, "received": False}, price, bounds, venue,
            background, shared_shock, response)
        result["issuer_valuation_response"]["receipt"] = dict(receipt)
        return result
    return private_background(demand, receipt, price, bounds, venue, background, shared_shock, response)


subset = previous.subset
