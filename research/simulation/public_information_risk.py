"""Public delivery with matched expected, post-cap received message variance.

Private residual information retains its old receipt mask. This controls one
message component, not realized returns, wealth-weighted risk or policy causality.
"""
import math

from . import fixed_marginal_risk as fixed
from .issuer_valuation import message_receipt as private_receipt
from .issuer_valuation import strategy_quote_terms
from .issuer_valuation import respond_issuer_background as private_background

VERSION = "public-information-matched-received-risk-v1"


def number(value, name, lower=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < lower:
        raise ValueError("invalid " + name)
    return float(value)


def clipped_uniform_second_moment(first, second, cap):
    """E[min((a U + b V)^2,c^2)] for independent variance-one uniforms."""
    a, b = number(first, "first amplitude"), number(second, "second amplitude")
    c = number(cap, "cap")
    if c <= 0:
        raise ValueError("cap must be positive")
    scale = max(a, b, c)
    h, low = sorted((math.sqrt(3) * a / scale, math.sqrt(3) * b / scale), reverse=True)
    limit = c / scale
    if h == 0:
        return 0.0
    if limit >= h + low:
        moment = (h * h + low * low) / 3
    elif low == 0 or limit <= h - low:
        moment = limit * limit * (1 - 2 * limit / (3 * h))
    else:
        plateau = h - low
        width = limit - plateau
        tail = h + low - limit
        flat_integral = (limit * limit * plateau - plateau ** 3 / 3) / (2 * h)
        triangular_integral = (limit * tail * width ** 2 + (2 * limit - tail) * width ** 3 / 3
                               - width ** 4 / 4) / (4 * h * low)
        moment = limit * limit - 2 * (flat_integral + triangular_integral)
    result = (moment * scale) * scale
    if not math.isfinite(result) or result < 0:
        raise ValueError("received moment exceeds supported finite precision")
    return result


def received_budget(residual, market, probability, cap):
    b, a = number(residual, "residual amplitude"), number(market, "market amplitude")
    p = number(probability, "receipt probability")
    if p > 1:
        raise ValueError("receipt probability exceeds one")
    baseline = p * clipped_uniform_second_moment(b, a, cap)
    def proposed(scale):
        return p * clipped_uniform_second_moment(b, scale * a, cap) + (1 - p) * clipped_uniform_second_moment(0, scale * a, cap)
    if p == 1 or a == 0:
        multiplier = 1.0
    elif p == 0 or proposed(0) == baseline:
        multiplier = 0.0
    else:
        lower, upper = 0.0, 1.0
        if proposed(lower) > baseline or proposed(upper) < baseline:
            raise ValueError("received-risk target is not bracketed")
        for _ in range(72):
            midpoint = (lower + upper) / 2
            if proposed(midpoint) < baseline:
                lower = midpoint
            else:
                upper = midpoint
        multiplier = (lower + upper) / 2
    actual = proposed(multiplier)
    tolerance = max(1e-300, baseline) * 5e-12 + 16 * math.ulp(baseline)
    if abs(actual - baseline) > tolerance:
        raise ValueError("post-cap received-risk matching failed")
    return {"public_market_multiplier": multiplier, "private_receipt_probability": p,
            "baseline_received_second_moment_bps2": baseline,
            "public_received_second_moment_bps2": actual,
            "matching_absolute_error_bps2": abs(actual - baseline),
            "matching_tolerance_bps2": tolerance}


def build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed, delivery):
    if delivery not in {"masked", "public"}:
        raise ValueError("explicit information delivery required")
    path = fixed.build_path(risk, features, scenario_id, parameters, mode, common_innovation_seed)
    if delivery == "masked":
        return path
    return with_public_delivery(path)


def with_public_delivery(path):
    """Add coverage to a validated fixed-risk path without changing its source rows."""
    if not isinstance(path, dict) or "information_coverage_parameters" in path:
        raise ValueError("expected an unmodified fixed-risk path")
    parameters = path["parameters"]
    result = {**path, "information_coverage_parameters": {"version": VERSION, "delivery": "public"}}
    result["by_stock"] = {}
    for stock, rows in path["by_stock"].items():
        result["by_stock"][stock] = []
        for row in rows:
            budget = row["risk_budget"]
            if "public_information_budget" in row:
                raise ValueError("coverage budget already present")
            result["by_stock"][stock].append({**row, "public_information_budget": received_budget(
                budget["residual_amplitude_bps"], abs(budget["signed_market_amplitude_bps"]),
                parameters["information_probability_bps"] / 10000, parameters["max_shift_bps"])})
    return result


def validate_issuer_valuation(assets, calendar, path):
    if not isinstance(path, dict):
        raise ValueError("issuer path must be an object")
    if "information_coverage_parameters" not in path:
        return fixed.validate_issuer_valuation(assets, calendar, path)
    if path["information_coverage_parameters"] != {"version": VERSION, "delivery": "public"}:
        raise ValueError("public coverage contract differs")
    original = {key: value for key, value in path.items() if key != "information_coverage_parameters"}
    original["by_stock"] = {stock: [{key: value for key, value in row.items() if key != "public_information_budget"}
                                    for row in rows] for stock, rows in path["by_stock"].items()}
    fixed.validate_issuer_valuation(assets, calendar, original)
    for stock in assets:
        for row in path["by_stock"][stock]:
            budget = row["risk_budget"]
            expected = received_budget(budget["residual_amplitude_bps"], abs(budget["signed_market_amplitude_bps"]),
                path["parameters"]["information_probability_bps"] / 10000, path["parameters"]["max_shift_bps"])
            if row.get("public_information_budget") != expected:
                raise ValueError("public received-risk budget was changed")
    return path


def message_receipt(row, parameters, stock, owner):
    old = private_receipt(row, parameters, stock, owner)
    if "public_information_budget" not in row:
        return old
    budget = row["public_information_budget"]
    private = row["risk_budget"]["residual_raw_shift_bps"] if old["received"] else None
    public = budget["public_market_multiplier"] * row["risk_budget"]["market_raw_shift_bps"]
    combined = (private if private is not None else 0.0) + public
    cap = parameters["max_shift_bps"]
    applied = max(-cap, min(cap, combined))
    return {"received": True, "receipt_sha256": old["receipt_sha256"],
            "information_probability_bps": parameters["information_probability_bps"],
            "valuation_shift_bps": applied, "applied_valuation_shift_bps": applied,
            "applied_belief_signal": applied / parameters["belief_scale_bps"],
            "public_information_receipt": {"version": VERSION, "private_received": old["received"],
                "private_raw_shift_bps": private, "public_received": True, "public_raw_shift_bps": public,
                "combined_raw_shift_bps": combined, "was_capped": combined != applied,
                "received_risk_budget": dict(budget)}}


def respond_issuer_background(demand, receipt, price, bounds, venue, background, shared_shock, response):
    detail = receipt.get("public_information_receipt")
    if detail and not detail["private_received"] and detail["public_raw_shift_bps"] == 0:
        # A known zero public component must not add an otherwise absent repricing.
        receipt_for_action = {**receipt, "received": False}
        result = private_background(demand, receipt_for_action, price, bounds, venue, background, shared_shock, response)
        result["issuer_valuation_response"]["receipt"] = dict(receipt)
        return result
    return private_background(demand, receipt, price, bounds, venue, background, shared_shock, response)


def subset(path, assets):
    return fixed.subset(path, assets)
