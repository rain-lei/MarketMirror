"""Prespecified operating/asset blocks on source-visible paired company dates."""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal

from .issuer_financial_increment import aware, fit_models, fit_linear, predict, metrics, comparison, MARKET_FEATURES, FINANCIAL_FEATURES

BLOCKS = {
    "primary_operating_assets": ["operating_net_cashflow_to_period_end_assets", "ytd_net_profit_to_period_end_assets"],
    "secondary_revenue_margins": ["operating_net_cashflow_to_selected_revenue", "ytd_net_profit_to_selected_revenue"],
    "secondary_asset_structure": ["cash_equivalent_closing_to_assets", "trading_financial_assets_to_assets", "other_current_assets_to_assets"],
}


def extend_panel(base_panel, operating, balances, block):
    if block not in BLOCKS:
        raise ValueError("operating block is not prespecified")
    codes = base_panel["parent_stock_codes"]
    if set(codes) != set(operating) or set(codes) != set(balances):
        raise ValueError("operating and frozen base cohort do not agree")
    rows = []
    for original in base_panel["rows"]:
        code = original["stock_code"]
        state, balance = operating[code], balances[code]
        if (state["status"] != "INDEPENDENT_OPERATING_EXTRACTION_AGREEMENT" or state["agent_signal_enabled"] is not False or
                any(state[k] != balance[k] for k in ("stock_code", "industry_code", "scope", "source_pdf_sha256", "report_metadata", "available_at_proxy")) or
                original["financial_available_at_proxy"] != state["available_at_proxy"]):
            raise ValueError("operating issuer/source/scope/date association differs from the baseline")
        reason = original["paired_exclusion_reason"]
        decimal = None
        features = None
        if reason is None:
            if aware(state["available_at_proxy"]) > aware(original["signal_cutoff_at"]):
                reason = "OPERATING_SOURCE_NOT_YET_AVAILABLE"
            elif not state["generic_nonfinancial_ratios_applicable"]:
                reason = "OPERATING_SCOPE_NOT_APPLICABLE"
            elif any(state["ratios"][f]["status"] not in ("OBSERVED_RATIO", "OBSERVED_SIGNED_RATIO") for f in BLOCKS[block]):
                reason = "MISSING_PRESPECIFIED_OPERATING_BLOCK"
            else:
                decimal = {f: state["ratios"][f]["value"] for f in BLOCKS[block]}
                values = {f: float(Decimal(v)) for f, v in decimal.items()}
                if any(not math.isfinite(v) for v in values.values()):
                    raise ValueError("operating feature is nonfinite")
                features = {**original["features"], **values}
        rows.append({**original, "base_paired_exclusion_reason": original["paired_exclusion_reason"],
                     "paired_exclusion_reason": reason, "features": features,
                     "target_absolute_return": original["target_absolute_return"] if reason is None else None,
                     "financial_decimal_inputs": original["financial_decimal_inputs"] if reason is None else None,
                     "operating_decimal_inputs": decimal, "operating_source_pdf_sha256": state["source_pdf_sha256"],
                     "operating_available_at_proxy": state["available_at_proxy"], "block": block})
    return rows


def fit_block(panel, split, block):
    if block not in BLOCKS:
        raise ValueError("unknown fixed operating block")
    result = fit_models(panel, split)
    eligible = [r for r in panel if r["paired_exclusion_reason"] is None]
    train = [r for r in eligible if r["partition"] == "train"]
    check = [r for r in eligible if r["partition"] == "evaluation"]
    indicators = ["industry_category_" + c for c in result["industry_control_categories"][1:]]

    def decorate(rows):
        return [{**r, "features": {**r["features"], **{name: float(r["industry_category"] == name[-1]) for name in indicators}}} for r in rows]

    features = [*MARKET_FEATURES, *indicators, *FINANCIAL_FEATURES, *BLOCKS[block]]
    fit = fit_linear(decorate(train), features)
    predictions = predict(fit, decorate(check))
    name = "market_industry_financial_operating"
    result["models"][name] = {"fit": fit, "metrics": metrics(check, predictions)}
    for row, value in zip(result["evaluation_predictions"], predictions, strict=True):
        row["predictions"][name] = value
    result["operating_comparison"] = comparison(result["models"]["market_industry_financial"], result["models"][name])
    result.update(block=block, operating_features=BLOCKS[block],
                  block_exclusions=dict(Counter(r["paired_exclusion_reason"] for r in panel if r["paired_exclusion_reason"])),
                  pairing_is_within_each_block=True, across_block_metrics_use_different_samples=block == "secondary_asset_structure",
                  static_operating_features_absorbed_by_company_fixed_effects=True)
    return result
