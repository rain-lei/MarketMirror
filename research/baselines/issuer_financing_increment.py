"""Fixed H1 financing blocks on the existing source-visible development grid."""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal

from .issuer_financial_increment import (
    FINANCIAL_FEATURES, MARKET_FEATURES, aware, comparison, fit_linear,
    fit_models, metrics, predict,
)

RATIO_FEATURES = {
    "h1_money_funds_to_assets": "money_funds_to_assets",
    "h1_short_term_borrowings_to_assets": "short_term_borrowings_to_assets",
    "h1_due_within_one_year_noncurrent_liabilities_to_assets": "due_within_one_year_noncurrent_liabilities_to_assets",
    "h1_money_funds_to_current_liabilities": "money_funds_to_current_liabilities",
}
RESTRICTION_FEATURE = "h1_max_verified_restricted_money_fund_row_to_money_funds"
RESTRICTION_SOURCE_FIELD = "max_verified_restricted_money_fund_row_to_money_funds"
INTERACTIONS = {
    "h1_restricted_row_x_benchmark_volatility_20": (RESTRICTION_FEATURE, "benchmark_volatility_20"),
    "h1_restricted_row_x_log_amount_surprise_20": (RESTRICTION_FEATURE, "log_amount_surprise_20"),
}
BLOCKS = {
    "primary_restricted_cash_buffer": [RESTRICTION_FEATURE, "h1_money_funds_to_assets"],
    "secondary_maturity_cash_coverage": [
        "h1_short_term_borrowings_to_assets",
        "h1_due_within_one_year_noncurrent_liabilities_to_assets",
        "h1_money_funds_to_current_liabilities",
    ],
    "secondary_restriction_market_interactions": [
        RESTRICTION_FEATURE, "h1_money_funds_to_assets", *INTERACTIONS,
    ],
}
ACCEPTED = "LIMITED_SAME_PERIOD_FINANCING_FACTS_AVAILABLE"
STATUSES = {ACCEPTED, "VERIFIED_BALANCE_FINANCIAL_SECTOR_RATIOS_EXCLUDED",
            "BALANCE_SOURCE_OR_INDEPENDENT_READ_QC_NOT_ADOPTED",
            "SOURCE_READER_QC_NOT_ADOPTED", "SOURCE_MISSING_NOT_ZERO"}


def source_decimal(state, feature):
    """Missing is not zero; H1 source units cancel only inside its own statement."""
    if feature == RESTRICTION_FEATURE:
        value = state[RESTRICTION_SOURCE_FIELD]
    else:
        ratio = state["balance_ratios"][RATIO_FEATURES[feature]]
        value = ratio["value"]
        if value is not None and ratio["status"] != "CALCULATED_FROM_VERIFIED_SAME_PERIOD_SOURCE_VALUES":
            raise ValueError("financing ratio lacks verified same-period source status")
    if value is None:
        return None
    number = Decimal(value)
    if not number.is_finite() or number < 0 or not math.isfinite(float(number)):
        raise ValueError("financing source feature is nonfinite or negative")
    if feature == RESTRICTION_FEATURE and number > 1:
        raise ValueError("single restricted row exceeds its same-source money-funds denominator")
    return value


def validate_company(state, original):
    if (state["stock_code"] != original["stock_code"] or state["industry_code"] != original["industry_code"]
            or state["agent_signal_enabled"] is not False or state["status"] not in STATUSES):
        raise ValueError("financing issuer/industry/source status association differs")
    metadata = state["report_metadata"]
    if metadata is not None:
        if (metadata["stock_code"] != original["stock_code"] or metadata["report_end_date"] != "2019-06-30"
                or metadata["eligible"] is not True):
            raise ValueError("financing report identity or H1 period differs")
        aware(metadata["available_at_proxy"])
    if state["status"] == ACCEPTED and (
            metadata is None or state["balance_scope"] != "consolidated"
            or state["financial_sector_generic_ratios_excluded"] is not False
            or state["literal_reader_status"] != "PASS_LITERAL_PAGE_MAPS"
            or state["independent_balance_status"] != "PASS"
            or original["industry_code"].startswith("J")):
        raise ValueError("accepted financing source lacks verified nonfinancial consolidated H1 scope")


def extend_panel(base_panel, financing, block):
    if block not in BLOCKS:
        raise ValueError("financing block is not prespecified")
    codes = base_panel["parent_stock_codes"]
    if codes != sorted(set(codes)) or set(codes) != set(financing):
        raise ValueError("financing and frozen base cohort do not agree")
    static_features = [f for f in BLOCKS[block] if f not in INTERACTIONS]
    rows = []
    for original in base_panel["rows"]:
        state = financing[original["stock_code"]]
        validate_company(state, original)
        metadata = state["report_metadata"]
        available = metadata["available_at_proxy"] if metadata else None
        reason = original["paired_exclusion_reason"]
        decimal, features = None, None
        if reason is None:
            if state["status"] != ACCEPTED:
                reason = "FINANCING_" + state["status"]
            elif aware(available) > aware(original["signal_cutoff_at"]):
                reason = "FINANCING_SOURCE_NOT_YET_AVAILABLE"
            else:
                decimal = {f: source_decimal(state, f) for f in static_features}
                if any(v is None for v in decimal.values()):
                    reason, decimal = "MISSING_PRESPECIFIED_FINANCING_BLOCK", None
                else:
                    features = {**original["features"], **{f: float(Decimal(v)) for f, v in decimal.items()}}
                    for f in BLOCKS[block]:
                        if f in INTERACTIONS:
                            left, right = INTERACTIONS[f]
                            features[f] = features[left] * original["features"][right]
                    if any(not math.isfinite(v) for v in features.values()):
                        raise ValueError("financing feature or lagged interaction is nonfinite")
        rows.append({
            **original, "base_paired_exclusion_reason": original["paired_exclusion_reason"],
            "paired_exclusion_reason": reason, "features": features,
            "target_absolute_return": original["target_absolute_return"] if reason is None else None,
            "financial_decimal_inputs": original["financial_decimal_inputs"] if reason is None else None,
            "financing_decimal_inputs": decimal, "financing_source_pdf_sha256": state["source_pdf_sha256"],
            "financing_available_at_proxy": available, "financing_state_status": state["status"],
            "financing_report_end_date": metadata["report_end_date"] if metadata else None,
            "financing_balance_scope": state.get("balance_scope"), "block": block,
        })
    return rows


def fit_block(panel, split, block):
    if block not in BLOCKS:
        raise ValueError("unknown fixed financing block")
    result = fit_models(panel, split)
    eligible = [r for r in panel if r["paired_exclusion_reason"] is None]
    train, check = ([r for r in eligible if r["partition"] == part] for part in ("train", "evaluation"))
    categories = result["industry_control_categories"]
    indicators = ["industry_category_" + c for c in categories[1:]]

    def decorate(rows):
        return [{**r, "features": {**r["features"], **{
            "industry_category_" + c: float(r["industry_category"] == c) for c in categories[1:]}}} for r in rows]

    features = [*MARKET_FEATURES, *indicators, *FINANCIAL_FEATURES, *BLOCKS[block]]
    fit = fit_linear(decorate(train), features)
    predictions = predict(fit, decorate(check))
    name = "market_industry_financial_financing"
    result["models"][name] = {"fit": fit, "metrics": metrics(check, predictions)}
    for row, value in zip(result["evaluation_predictions"], predictions, strict=True):
        row["predictions"][name] = value
    result["financing_comparison"] = comparison(result["models"]["market_industry_financial"], result["models"][name])
    result.update(
        block=block, financing_features=BLOCKS[block],
        block_exclusions=dict(Counter(r["paired_exclusion_reason"] for r in panel if r["paired_exclusion_reason"])),
        references_rebuilt_on_same_company_dates=True,
        across_block_metrics_may_use_different_samples=True,
        restricted_feature_is_maximum_verified_single_row_not_total=True,
        h1_denominators_never_replaced_by_q3_values=True,
        company_fixed_market_is_reference_without_financing=True,
    )
    return result
