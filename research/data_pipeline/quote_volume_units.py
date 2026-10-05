"""Declared vendor quote/volume units with explicit rounding uncertainty.

Conversions follow the archived external adapter's unit declaration. Internal
price-range compatibility supports that declaration; it cannot by itself prove
absolute physical units, total returns, or historical point-in-time availability.
"""
from decimal import Decimal, InvalidOperation, localcontext

VERSION = "declared-quote-volume-units-with-uncertainty-v1"


def number(raw):
    if not isinstance(raw, str) or not raw or raw.strip() != raw:
        raise ValueError("raw unit field must be an observed numeric string")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError("raw unit field is not numeric") from error
    if not value.is_finite():
        raise ValueError("raw unit field is not finite")
    return value


def quote_interval(raw):
    value = number(raw)
    half = Decimal(1).scaleb(value.as_tuple().exponent) / 2
    return value - half, value + half


def declared_quote_units(fields, fqt, volume_scale="100", amount_scale="1"):
    """Return observations and scale diagnostics without a model return.

    Source volume is a rounded quote in hands, not an exact count of shares.
    Keep the raw fields, central converted estimate and assumed rounding interval
    separate. Nonpositive adjusted prices remain observations but cannot be
    execution quotes or positive VWAP range references.
    """
    if type(fqt) is not int or fqt not in {0, 1}:
        raise ValueError("unit diagnostic request parameter invalid")
    if not isinstance(fields, dict) or set(fields) != {f"f{n}" for n in range(51, 62)}:
        raise ValueError("complete provider field contract required")
    scale_v, scale_a = number(volume_scale), number(amount_scale)
    if scale_v <= 0 or scale_a <= 0:
        raise ValueError("declared unit scales must be positive")
    with localcontext() as context:
        context.prec = 50
        prices = [number(fields[f"f{n}"]) for n in range(52, 56)]
        volume, amount, turnover = [number(fields[f"f{n}"]) for n in (56, 57, 61)]
        if volume < 0 or amount < 0 or turnover < 0:
            raise ValueError("negative volume, amount or turnover")
        low, high = prices[3], prices[2]
        if not (low <= prices[0] <= high and low <= prices[1] <= high):
            raise ValueError("reported OHLC price range inconsistent")
        vi = tuple(v * scale_v for v in quote_interval(fields["f56"]))
        ai = tuple(v * scale_a for v in quote_interval(fields["f57"]))
        result = {"source_volume_raw": fields["f56"], "source_amount_raw": fields["f57"],
            "source_turnover_percent_raw": fields["f61"],
            "declared_shares_per_source_volume_unit": str(scale_v),
            "declared_currency_units_per_source_amount_unit": str(scale_a),
            "quoted_volume_shares_estimate": str(volume * scale_v),
            "quoted_volume_rounding_interval_shares": [str(v) for v in vi],
            "quoted_amount_currency_estimate": str(amount * scale_a),
            "quoted_amount_rounding_interval_currency": [str(v) for v in ai],
            "turnover_decimal": str(turnover / 100),
            "vwap_currency_per_share_estimate": None, "vwap_rounding_interval": None,
            "unadjusted_price_range_rounding_interval": None,
            "vwap_rounding_interval_overlaps_price_range": None,
            "entire_vwap_rounding_interval_inside_price_range": None,
            "source_volume_is_exact_share_count": False,
            "rounding_hypothesis": "Source numeric value plus/minus half its serialized decimal quantum; not an independently certified physical tick.",
            "economic_return": None, "model_eligible": False,
            "execution_quote_candidate": fqt == 0 and all(price > 0 for price in prices),
            "price_range_role": "UNADJUSTED_EXECUTION_QUOTE_CANDIDATE" if fqt == 0 else "ADJUSTED_LEVEL_NOT_EXECUTION_QUOTE",
        }
        if volume == 0:
            if amount != 0:
                raise ValueError("positive amount with zero volume")
            result["comparison_status"] = "ZERO_VOLUME_NO_VWAP_NO_TRADE_STATUS_INFERENCE"
            return result
        if amount == 0:
            raise ValueError("zero amount with positive volume")
        result["vwap_currency_per_share_estimate"] = str(amount * scale_a / (volume * scale_v))
        if fqt != 0:
            result["comparison_status"] = "ADJUSTED_PRICE_LEVEL_NOT_A_PHYSICAL_VWAP_REFERENCE"
            return result
        if vi[0] <= 0 or ai[0] < 0 or not all(price > 0 for price in prices):
            result["comparison_status"] = "POSITIVE_QUOTE_INTERVAL_UNSUPPORTED"
            return result
        vwap = ai[0] / vi[1], ai[1] / vi[0]
        price_range = quote_interval(fields["f55"])[0], quote_interval(fields["f54"])[1]
        overlap = max(vwap[0], price_range[0]) <= min(vwap[1], price_range[1])
        result.update(vwap_rounding_interval=[str(v) for v in vwap],
            unadjusted_price_range_rounding_interval=[str(v) for v in price_range],
            vwap_rounding_interval_overlaps_price_range=overlap,
            entire_vwap_rounding_interval_inside_price_range=price_range[0] <= vwap[0] <= vwap[1] <= price_range[1],
            comparison_status="DECLARED_SCALE_RANGE_COMPATIBLE" if overlap else "DECLARED_SCALE_RANGE_INCOMPATIBLE")
        return result
