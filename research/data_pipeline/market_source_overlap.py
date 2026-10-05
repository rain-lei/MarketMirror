"""Cross-provider raw-field comparisons, separate from model input approval."""
from decimal import Decimal, InvalidOperation


def decimal(value):
    if not isinstance(value, str) or value in {"", "-"}:
        raise ValueError("comparison requires an observed numeric field string")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("comparison field is not numeric") from error
    if not number.is_finite():
        raise ValueError("comparison field is not finite")
    return number


def quantum(value):
    number = decimal(value)
    return Decimal(1).scaleb(number.as_tuple().exponent)


def compare_field(candidate, reference, candidate_scale="1", reference_is_exact_integer=False):
    """Report a rounding hypothesis, never silently authorize a unit conversion."""
    scale = decimal(candidate_scale)
    if scale <= 0:
        raise ValueError("comparison scale must be positive")
    value, old = decimal(candidate) * scale, decimal(reference)
    if reference_is_exact_integer and old != old.to_integral_value():
        raise ValueError("reference share count is not an integer")
    tolerance = quantum(candidate) * scale / 2
    if not reference_is_exact_integer:
        tolerance += quantum(reference) / 2
    difference = value - old
    return {"candidate_raw": candidate, "reference_raw": reference,
            "candidate_unit_scale_hypothesis": candidate_scale,
            "candidate_scaled_value": str(value), "signed_difference": str(difference),
            "absolute_difference": str(abs(difference)), "rounding_tolerance_hypothesis": str(tolerance),
            "within_rounding_hypothesis": abs(difference) <= tolerance,
            "ratio_when_reference_nonzero": str(value / old) if old != 0 else None,
            "unit_or_return_basis_certified": False}


def stock_comparison(candidate_fields, reference):
    if reference["adjustflag"] != "1" or reference["tradestatus"] not in {"0", "1"}:
        raise ValueError("reference must retain its original adjustment and trade status")
    if reference["tradestatus"] == "0":
        if decimal(reference["volume"]) != 0 or decimal(reference["close"]) != decimal(reference["preclose"]):
            raise ValueError("reference suspension disagrees with observed volume/price")
    return {
        "provider_return_percent": compare_field(candidate_fields["f59"], reference["pctChg"]),
        "volume_shares_under_100_shares_per_field_unit": compare_field(candidate_fields["f56"], reference["volume"], "100", True),
        "amount_under_same_currency_unit": compare_field(candidate_fields["f57"], reference["amount"]),
        "reference_adjustflag": reference["adjustflag"], "reference_tradestatus": reference["tradestatus"],
        "stock_price_levels_compared": False,
        "stock_price_level_reason": "Different declared forward/unadjusted candidate and backward reference adjustments; levels are not treated as interchangeable.",
        "model_eligible": False,
    }
