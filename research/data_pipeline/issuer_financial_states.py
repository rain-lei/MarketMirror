"""Company balance-sheet states with explicit applicability and availability."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext
import re

RATIOS = {
    "total_liabilities_to_assets": ("liabilities", "assets"),
    "money_funds_to_assets": ("money_funds", "assets"),
    "current_assets_to_current_liabilities": ("current_assets", "current_liabilities"),
    "money_funds_to_current_liabilities": ("money_funds", "current_liabilities"),
    "short_term_borrowings_to_assets": ("short_term_borrowings", "assets"),
    "long_term_borrowings_to_assets": ("long_term_borrowings", "assets"),
    "noncurrent_liabilities_due_within_one_year_to_assets": ("noncurrent_liabilities_due_within_one_year", "assets"),
    "trade_receivables_to_assets": ("trade_receivables", "assets"),
    "inventories_to_assets": ("inventories", "assets"),
}


def observed_ratio(amounts: dict, numerator: str, denominator: str, applicable: bool,
                   inapplicable_status: str = "NOT_APPLICABLE_FINANCIAL_INSTITUTION") -> dict:
    result = {"numerator_field": numerator, "denominator_field": denominator, "value": None}
    if not applicable:
        return {**result, "status": inapplicable_status}
    if any(amounts[field]["status"] != "NUMERIC" for field in (numerator, denominator)):
        return {**result, "status": "MISSING_SOURCE_AMOUNT"}
    n, d = (Decimal(amounts[field]["value_yuan"]) for field in (numerator, denominator))
    if not n.is_finite() or not d.is_finite() or n < 0 or d <= 0:
        return {**result, "status": "INVALID_AMOUNT_OR_DENOMINATOR"}
    with localcontext() as context:
        context.prec = 50
        value = str(n / d)
    return {**result, "status": "OBSERVED_RATIO", "value": value}


def company_state(company: dict, audit_record: dict) -> dict:
    if (company["status"] != "UNIQUE_RECONCILED_CANDIDATE" or audit_record["status"] != "PASS" or
            audit_record["stock_code"] != company["stock_code"] or
            audit_record["source_pdf_sha256"] != company["source_pdf_sha256"] or
            audit_record["selected_candidate_index"] != company["selected_candidate_index"]):
        raise ValueError("a company state requires independent agreement on the same issuer/source/table")
    candidate = company["candidates"][company["selected_candidate_index"]]
    current = candidate["current_column_index"]
    column = candidate["column_mapping"]["columns"][current]
    if column["scope"] == "parent" or column["period_end"] != company["report_metadata"]["report_end_date"]:
        raise ValueError("current company state must not be a parent or another period's column")
    amounts = {field: {key: data["cells"][current][key] for key in ("status", "reported_value", "value_yuan")}
               for field, data in candidate["fields"].items()}
    nonfinancial = not company["industry_code"].startswith("J")
    applicable = nonfinancial and column["scope"] == "consolidated"
    reason = "NOT_APPLICABLE_UNVERIFIED_CONSOLIDATED_SCOPE" if nonfinancial and not applicable else "NOT_APPLICABLE_FINANCIAL_INSTITUTION"
    ratios = {name: observed_ratio(amounts, numerator, denominator, applicable, reason)
              for name, (numerator, denominator) in RATIOS.items()}
    unit_context = re.sub(r"\s+", "", "".join(row["text"] for row in candidate["unit_evidence"]))
    currencies = re.findall(r"币种[:：]([^，,;；（）()]+)", unit_context)
    if any(not currency.startswith("人民币") for currency in currencies):
        raise ValueError("a declared non-RMB reporting currency cannot be silently normalized to CNY")
    currency_basis = "explicit_RMB_unit_or_currency_declaration" if "人民币" in unit_context else "yuan_unit_in_official_PRC_statutory_report_convention_CNY"
    return {"stock_code": company["stock_code"], "historical_short_name": company["historical_short_name"],
            "industry_code": company["industry_code"], "status": "INDEPENDENT_EXTRACTION_AGREEMENT",
            "scope": column["scope"], "period_end": column["period_end"],
            "available_at_proxy": company["report_metadata"]["available_at_proxy"],
            "report_metadata": company["report_metadata"], "source_pdf_path": company["source_pdf_path"],
            "source_pdf_sha256": company["source_pdf_sha256"], "reported_unit": candidate["reported_unit"],
            "multiplier_to_yuan": candidate["multiplier_to_yuan"], "currency": "CNY", "currency_basis": currency_basis,
            "reported_unit_evidence": candidate["unit_evidence"],
            "amounts": amounts, "ratios": ratios, "generic_nonfinancial_ratios_applicable": applicable,
            "field_source_evidence": {field: data["rows"] for field, data in candidate["fields"].items()},
            "column_header_evidence": candidate["column_mapping"],
            "original_pdf_bytes_certified_unchanged_historically": False,
            "comparison_columns_used_as_earlier_publication_snapshots": False,
            "unrestricted_money_funds_verified": False, "complete_interest_bearing_debt_verified": False,
            "agent_signal_enabled": False}


def state_asof(state: dict, asof: str) -> dict | None:
    moment = datetime.fromisoformat(asof)
    available = datetime.fromisoformat(state["available_at_proxy"])
    if moment.utcoffset() is None or available.utcoffset() is None:
        raise ValueError("company-state as-of queries require timezone-aware timestamps")
    if state["status"] != "INDEPENDENT_EXTRACTION_AGREEMENT" or available > moment:
        return None
    return state
