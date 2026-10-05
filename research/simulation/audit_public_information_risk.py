"""Independent convolution integral and receipt reconstruction; no production imports."""
from decimal import Decimal, localcontext
from functools import lru_cache
import math

from .audit_issuer_valuation import expected_receipt as original_receipt

VERSION = "public-information-matched-received-risk-v1"


def independent_second_moment(first, second, cap):
    """Integrate x² below the cap and the probability mass above it in Decimal."""
    for value in (first, second, cap):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid independent integral input")
    if cap == 0:
        raise ValueError("positive independent cap required")
    with localcontext() as context:
        context.prec = 70
        a, b, c = (Decimal.from_float(float(value)) for value in (first, second, cap))
        h, low = sorted((Decimal(3).sqrt() * a, Decimal(3).sqrt() * b), reverse=True)
        if not h:
            return 0.0
        if not low:
            top = min(c, h)
            integral = top ** 3 / (6 * h)
            tail = max(Decimal(0), (h - c) / (2 * h))
        else:
            plateau, end = h - low, h + low
            top = min(c, plateau)
            integral = top ** 3 / (6 * h)
            if c > plateau:
                right = min(c, end)
                integral += (end * (right ** 3 - plateau ** 3) / 3
                             - (right ** 4 - plateau ** 4) / 4) / (4 * h * low)
            if c <= plateau:
                tail = Decimal('0.5') - c / (2 * h)
            else:
                tail = max(Decimal(0), end - c) ** 2 / (8 * h * low)
        return float(2 * integral + 2 * c * c * tail)


@lru_cache(maxsize=20000)
def verify_budget_numbers(residual, market, probability, cap, multiplier,
                          declared_baseline, declared_public, declared_error, declared_tolerance):
    values = (residual, market, probability, cap, multiplier, declared_baseline,
              declared_public, declared_error, declared_tolerance)
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("invalid declared received budget")
    if probability > 1 or multiplier > 1 or cap == 0:
        raise ValueError("received budget probability, scale or cap differs")
    baseline = probability * independent_second_moment(residual, market, cap)
    scaled = multiplier * market
    public = (probability * independent_second_moment(residual, scaled, cap)
              + (1 - probability) * independent_second_moment(0, scaled, cap))
    tolerance = max(1e-300, baseline) * 5e-12 + 16 * math.ulp(baseline)
    if (not math.isclose(declared_tolerance, tolerance, rel_tol=5e-12, abs_tol=1e-310)
            or abs(declared_baseline - baseline) > tolerance
            or abs(declared_public - public) > tolerance
            or abs(public - baseline) > tolerance
            or declared_error != abs(declared_public - declared_baseline)):
        raise ValueError("independent post-cap received variance failed")
    return {"independent_baseline_bps2": baseline, "independent_public_bps2": public,
            "independent_matching_error_bps2": abs(public - baseline)}


def verify_budget(row, parameters):
    b, actual = row['risk_budget'], row['public_information_budget']
    required = {'public_market_multiplier', 'private_receipt_probability',
                'baseline_received_second_moment_bps2', 'public_received_second_moment_bps2',
                'matching_absolute_error_bps2', 'matching_tolerance_bps2'}
    if set(actual) != required or actual['private_receipt_probability'] != parameters['information_probability_bps'] / 10000:
        raise ValueError('received budget schema/probability differs')
    return verify_budget_numbers(b['residual_amplitude_bps'], abs(b['signed_market_amplitude_bps']),
        actual['private_receipt_probability'], parameters['max_shift_bps'], actual['public_market_multiplier'],
        actual['baseline_received_second_moment_bps2'], actual['public_received_second_moment_bps2'],
        actual['matching_absolute_error_bps2'], actual['matching_tolerance_bps2'])


def expected_receipt(row, parameters, stock, owner):
    original = original_receipt(row, parameters, stock, owner)
    if 'public_information_budget' not in row:
        return original
    budget = row['public_information_budget']
    residual = row['risk_budget']['residual_raw_shift_bps'] if original['received'] else None
    public = budget['public_market_multiplier'] * row['risk_budget']['market_raw_shift_bps']
    combined = (residual if residual is not None else 0.0) + public
    shift = min(parameters['max_shift_bps'], max(-parameters['max_shift_bps'], combined))
    return {'received': True, 'receipt_sha256': original['receipt_sha256'],
        'information_probability_bps': parameters['information_probability_bps'],
        'valuation_shift_bps': shift, 'applied_valuation_shift_bps': shift,
        'applied_belief_signal': shift / parameters['belief_scale_bps'],
        'public_information_receipt': {'version': VERSION, 'private_received': original['received'],
            'private_raw_shift_bps': residual, 'public_received': True, 'public_raw_shift_bps': public,
            'combined_raw_shift_bps': combined, 'was_capped': combined != shift,
            'received_risk_budget': dict(budget)}}


def verify_receipt(actual, row, parameters, stock, owner):
    if actual != expected_receipt(row, parameters, stock, owner):
        raise ValueError('public receipt or private unknown value differs')
    if 'public_information_budget' in row:
        detail = actual['public_information_receipt']
        if (type(detail['private_received']) is not bool or type(detail['public_received']) is not bool
                or type(detail['was_capped']) is not bool
                or not detail['private_received'] and detail['private_raw_shift_bps'] is not None):
            raise ValueError('private missingness or public receipt flags differ')
