"""Publication-dated operating states with signed, explicitly defined ratios."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext
import re

RATIOS = {
    "operating_net_cashflow_to_period_end_assets": ("operating_net_cashflow", "assets", True),
    "ytd_net_profit_to_period_end_assets": ("net_profit", "assets", True),
    "ytd_net_profit_to_selected_revenue": ("net_profit", "selected_revenue", True),
    "operating_net_cashflow_to_selected_revenue": ("operating_net_cashflow", "selected_revenue", True),
    "cash_equivalent_closing_to_assets": ("cash_equivalent_closing", "assets", False),
    "trading_financial_assets_to_assets": ("trading_financial_assets", "assets", False),
    "other_current_assets_to_assets": ("other_current_assets", "assets", False),
}


def ratio(amounts, numerator, denominator, applicable, allow_negative):
    result = {"numerator_field": numerator, "denominator_field": denominator, "value": None}
    if not applicable:
        return {**result, "status": "NOT_APPLICABLE_GENERIC_NONFINANCIAL_RATIO"}
    if any(amounts[f]["status"] != "NUMERIC" for f in (numerator, denominator)):
        return {**result, "status": "MISSING_SOURCE_AMOUNT"}
    n, d = (Decimal(amounts[f]["value_yuan"]) for f in (numerator, denominator))
    if not n.is_finite() or not d.is_finite() or d <= 0 or (not allow_negative and n < 0):
        return {**result, "status": "INVALID_AMOUNT_OR_DENOMINATOR"}
    with localcontext() as context:
        context.prec = 50
        value = str(n / d)
    return {**result, "status": "OBSERVED_SIGNED_RATIO" if allow_negative else "OBSERVED_RATIO", "value": value}


def current_part(statement, metadata):
    if statement["status"] != "UNIQUE_SOURCE_DATED_YTD_CANDIDATE":
        raise ValueError("operating state requires a unique audited YTD table")
    chosen = statement["candidates"][statement["selected_candidate_index"]]
    if chosen["status"] != "SOURCE_DATED_OPERATING_STATEMENT":
        raise ValueError("operating candidate itself did not pass required source checks")
    index = chosen["current_ytd_column_index"]
    column = chosen["column_mapping"]["columns"][index]
    expected_start = metadata["report_end_date"][:4] + "-01-01"
    if (column["scope"] == "parent" or column["period_start"] != expected_start or
            column["period_end"] != metadata["report_end_date"]):
        raise ValueError("operating state cannot mix a parent, quarterly or another year's column")
    unit_text = re.sub(r"\s+", "", "".join(r["text"] for r in chosen["unit_evidence"]))
    currencies = re.findall(r"币种[:：]([^，,;；（）()]+)", unit_text)
    if any(not c.startswith("人民币") for c in currencies):
        raise ValueError("operating currency cannot be silently converted from non-RMB")
    return {"scope": column["scope"], "period_start": column["period_start"], "period_end": column["period_end"],
            "period_basis": column["period_basis"], "reported_unit": chosen["reported_unit"],
            "amounts": {f: {k: spec["cells"][index][k] for k in ("status", "reported_value", "value_yuan")} for f, spec in chosen["fields"].items()},
            "field_source_evidence": {f: spec["rows"] for f, spec in chosen["fields"].items()},
            "column_header_evidence": chosen["column_mapping"], "reported_unit_evidence": chosen["unit_evidence"],
            "currency": "CNY", "currency_basis": "explicit_RMB" if "人民币" in unit_text else "statutory_yuan_CNY_convention",
            "source_signed_presentation_retained": True}


def company_state(company, audit, balance):
    if (audit["status"] != "PASS" or audit["stock_code"] != company["stock_code"] or
            audit["source_pdf_sha256"] != company["source_pdf_sha256"] or
            set(audit["statements"]) != {"利润表", "现金流量表", "additional_assets"} or
            any(s["status"] != "PASS" for s in audit["statements"].values()) or
            balance["status"] != "INDEPENDENT_EXTRACTION_AGREEMENT" or
            any(company[k] != balance[k] for k in ("stock_code", "source_pdf_sha256", "report_metadata", "industry_code"))):
        raise ValueError("operating export requires full independent agreement on the same issuer, PDF and dated balance")
    profit = current_part(company["flows"]["利润表"], company["report_metadata"])
    cashflow = current_part(company["flows"]["现金流量表"], company["report_metadata"])
    if any(audit["statements"][kind]["selected_candidate_index"] != company["flows"][kind]["selected_candidate_index"] for kind in ("利润表", "现金流量表")):
        raise ValueError("operating audit selected another table")
    assets = company["additional_assets"]
    index = assets["current_column_index"]
    column = assets["column_mapping"]["columns"][index]
    if column["scope"] != balance["scope"] or column["period_end"] != balance["period_end"]:
        raise ValueError("additional asset rows do not match the audited balance scope/period")
    if any(part["scope"] != balance["scope"] for part in (profit, cashflow)):
        raise ValueError("profit/cashflow and balance scope cannot be silently combined")
    extra = {f: {k: spec["cells"][index][k] for k in ("status", "reported_value", "value_yuan")} for f, spec in assets["fields"].items()}
    amounts = {**balance["amounts"], **profit["amounts"], **cashflow["amounts"], **extra}
    revenue = next((f for f in ("operating_total_revenue", "operating_revenue") if amounts[f]["status"] == "NUMERIC"), None)
    amounts["selected_revenue"] = amounts[revenue] if revenue else {"status": "MISSING_SOURCE_AMOUNT", "value_yuan": None}
    applicable = not company["industry_code"].startswith("J") and balance["scope"] == "consolidated"
    ratios = {name: ratio(amounts, n, d, applicable, negative) for name, (n, d, negative) in RATIOS.items()}
    return {k: balance[k] for k in ("stock_code", "historical_short_name", "industry_code", "scope", "available_at_proxy", "report_metadata",
                                   "source_pdf_path", "source_pdf_sha256")} | {
        "status": "INDEPENDENT_OPERATING_EXTRACTION_AGREEMENT", "profit": profit, "cashflow": cashflow,
        "additional_assets": {"period_end": column["period_end"], "scope": column["scope"], "amounts": extra,
                              "field_source_evidence": {f: spec["rows"] for f, spec in assets["fields"].items()}},
        "ratios": ratios, "selected_revenue_source_field": revenue, "generic_nonfinancial_ratios_applicable": applicable,
        "balance_amounts_source": "independently_audited_current_balance_states_same_report",
        "flow_to_period_end_stock_ratios_are_not_annualized_or_standard_ROA": True,
        "cash_equivalents_are_not_assumed_equal_to_money_funds": True,
        "unrestricted_cash_or_financial_asset_liquidity_verified": False,
        "comparison_columns_used_as_earlier_publications": False,
        "original_pdf_bytes_certified_unchanged_historically": False, "agent_signal_enabled": False}


def state_asof(state, asof):
    moment, available = datetime.fromisoformat(asof), datetime.fromisoformat(state["available_at_proxy"])
    if moment.utcoffset() is None or available.utcoffset() is None:
        raise ValueError("operating as-of queries require timezone-aware timestamps")
    return state if state["status"] == "INDEPENDENT_OPERATING_EXTRACTION_AGREEMENT" and available <= moment else None
