"""Same-source June balance and limited restriction relationships only."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext


CASH_KINDS = {"RESTRICTED_MONEY_FUNDS_AS_REPORTED", "RESTRICTED_CASH_SUBCATEGORY_AS_REPORTED"}
DEBT_FIELDS = ("short_term_borrowings", "noncurrent_liabilities_due_within_one_year",
               "long_term_borrowings", "bonds_payable", "lease_liabilities")


def proportion(numerator, denominator) -> dict:
    if numerator is None or denominator is None:
        return {"status": "MISSING_SOURCE_VALUE_NOT_ZERO", "value": None}
    n, d = Decimal(numerator), Decimal(denominator)
    if not n.is_finite() or not d.is_finite() or n < 0 or d <= 0:
        return {"status": "INVALID_SIGN_OR_NONPOSITIVE_DENOMINATOR", "value": None}
    with localcontext() as ctx:
        ctx.prec = 50
        value = n / d
    return {"status": "CALCULATED_FROM_VERIFIED_SAME_PERIOD_SOURCE_VALUES", "value": str(value)}


def strict_issuer_map(companies: list[dict]) -> dict:
    mapping = {c["stock_code"]: c for c in companies}
    if len(mapping) != len(companies):
        raise ValueError("duplicate financing source issuer association")
    return mapping


def assert_same_source(company: dict, checked: dict, restricted: dict) -> None:
    if len({c["stock_code"] for c in (company, checked, restricted)}) != 1:
        raise ValueError("financing issuer association differs")
    if len({c["source_pdf_sha256"] for c in (company, checked, restricted)}) != 1:
        raise ValueError("financing source PDF association differs")
    if company["source_pdf_path"] != restricted["source_pdf_path"] or company["report_metadata"] != restricted["report_metadata"]:
        raise ValueError("financing original report or period association differs")
    if checked["selected_candidate_index"] != company["selected_candidate_index"]:
        raise ValueError("financing independent balance index differs")
    if company["literal_reader_status"] != restricted["literal_reader_status"] or company["literal_reader_status"] != checked["literal_reader_status"]:
        raise ValueError("financing source reader association differs")
    metadata = company["report_metadata"]
    if metadata is not None and (metadata["stock_code"] != company["stock_code"] or metadata["report_end_date"] != "2019-06-30"):
        raise ValueError("financing inputs are not the same issuer June period")


def restriction_currency(table: dict, row: dict, audited_presentations: list[dict]) -> dict:
    unit = table["unit"]
    if unit["currency"] == "CNY_EXPLICIT" and unit["reported_unit"] is not None:
        return {"status": "LOCAL_EXPLICIT_CNY", "reported_unit": unit["reported_unit"], "evidence": unit["evidence"]}
    if unit["currency"] != "CURRENCY_UNSPECIFIED" or unit["reported_unit"] is None or unit["scale"] is None:
        return {"status": "SOURCE_UNIT_OR_CURRENCY_UNKNOWN", "evidence": None}
    page = row["source_cell"]["pdf_page"]
    earlier = [item for item in audited_presentations if item["status"] == "PASS_EXPLICIT_CNY_DECLARATION"
               and item["reported_unit"] == unit["reported_unit"]
               and item["pdf_page"] < page]
    if not earlier:
        return {"status": "NO_MATCHING_EXPLICIT_NOTE_CURRENCY", "evidence": None}
    chosen = max(earlier, key=lambda item: (item["pdf_page"], item["source_rectangle"][1]))
    return {"status": "MATCHING_INDEPENDENTLY_VERIFIED_NOTE_PRESENTATION", "reported_unit": unit["reported_unit"], "evidence": chosen}


def build_company(company: dict, checked: dict, restricted: dict) -> dict:
    assert_same_source(company, checked, restricted)
    result = {k: company[k] for k in ("stock_code", "historical_short_name", "industry_code", "report_metadata",
                                      "source_pdf_path", "source_pdf_sha256", "literal_reader_status")}
    result.update(balance_source_status=company["status"], independent_balance_status=checked["status"],
                  balance_fields={}, balance_ratios={}, debt_components={}, restricted_cash_rows=[],
                  max_verified_restricted_money_fund_row_to_money_funds=None,
                  max_verified_cash_subcategory_row_to_money_funds=None,
                  unrestricted_money_funds=None, cash_available_for_trading=None,
                  total_restricted_money_funds=None, complete_interest_bearing_debt=None,
                  cash_to_complete_interest_bearing_debt=None,
                  all_company_restrictions_completely_disclosed_verified=False,
                  financing_impact_coefficient=None, agent_signal_enabled=False,
                  original_pdf_bytes_certified_unchanged_historically=False)
    if company["source_pdf_path"] is None:
        return {**result, "status": "SOURCE_MISSING_NOT_ZERO"}
    if company["literal_reader_status"] != "PASS_LITERAL_PAGE_MAPS":
        return {**result, "status": "SOURCE_READER_QC_NOT_ADOPTED"}
    if company["status"] != "UNIQUE_RECONCILED_CANDIDATE" or checked["status"] != "PASS":
        return {**result, "status": "BALANCE_SOURCE_OR_INDEPENDENT_READ_QC_NOT_ADOPTED"}
    candidate = company["candidates"][company["selected_candidate_index"]]
    column = candidate["column_mapping"]["columns"][candidate["current_column_index"]]
    if column["period_end"] != "2019-06-30" or column["scope"] != "consolidated":
        return {**result, "status": "NONCONSOLIDATED_OR_OTHER_PERIOD_NOT_ADOPTED"}
    cny = candidate["currency_declaration"]["status"] == "CNY_EXPLICIT"
    if cny and (not checked.get("independent_currency_declarations") or
                any(item["status"] != "PASS_EXPLICIT_CNY_DECLARATION" for item in checked["independent_currency_declarations"])):
        raise ValueError("financing explicit balance currency lacks independent evidence")
    result.update(balance_scope=column["scope"], balance_reported_unit=candidate["reported_unit"],
                  balance_currency_status=candidate["currency_declaration"]["status"],
                  balance_currency_evidence=candidate["currency_declaration"]["evidence"],
                  financial_sector_generic_ratios_excluded=company["industry_code"].startswith("J"))
    amounts = {}
    for field, data in candidate["fields"].items():
        cell = data["cells"][candidate["current_column_index"]]
        accepted = cell["status"] == "NUMERIC" and data["status"] == "EXACT_LABEL_ROW"
        result["balance_fields"][field] = {"source_status": cell["status"], "reported_value": cell["reported_value"],
                                           "source_number_independently_verified": accepted,
                                           "verified_value_reported_units": cell["reported_value"] if accepted else None,
                                           "verified_value_at_reported_unit_scale": cell["value_yuan"] if accepted else None,
                                           "verified_value_yuan": cell["value_yuan"] if accepted and cny else None,
                                           "source_rows": data["rows"]}
        amounts[field] = cell["reported_value"] if accepted else None
    if result["financial_sector_generic_ratios_excluded"]:
        return {**result, "status": "VERIFIED_BALANCE_FINANCIAL_SECTOR_RATIOS_EXCLUDED"}
    for name, num, den in (("money_funds_to_assets", "money_funds", "assets"),
                           ("liabilities_to_assets", "liabilities", "assets"),
                           ("current_assets_to_current_liabilities", "current_assets", "current_liabilities"),
                           ("money_funds_to_current_liabilities", "money_funds", "current_liabilities"),
                           ("short_term_borrowings_to_assets", "short_term_borrowings", "assets"),
                           ("due_within_one_year_noncurrent_liabilities_to_assets", "noncurrent_liabilities_due_within_one_year", "assets"),
                           ("long_term_borrowings_to_assets", "long_term_borrowings", "assets"),
                           ("bonds_payable_to_assets", "bonds_payable", "assets")):
        result["balance_ratios"][name] = {**proportion(amounts[num], amounts[den]),
                                          "numerator_field": num, "denominator_field": den,
                                          "unit_cancels_within_same_printed_statement": True,
                                          "complete_interest_bearing_debt_ratio": False}
    result["debt_components"] = {field: result["balance_fields"][field] for field in DEBT_FIELDS}
    for table in restricted["tables"]:
        for i, source in enumerate(table["rows"]):
            if source["component_kind"] not in CASH_KINDS:
                continue
            item = {"table_index": table["table_index"], "row_index": i, "label_as_printed": source["label_as_printed"],
                    "component_kind": source["component_kind"], "reason_as_printed": source["reason_as_printed"],
                    "source_cell": source["source_cell"], "source_status": source["source_status"],
                    "restricted_row_to_money_funds": None, "restricted_row_to_assets": None,
                    "verified_restricted_value_yuan": None, "status": "RESTRICTION_ROW_NOT_ADOPTED"}
            result["restricted_cash_rows"].append(item)
            if not source["source_number_independently_verified"] or not table["source_number_checks_accepted"]:
                item["status"] = "RESTRICTION_VALUE_MISSING_OR_UNVERIFIED_NOT_ZERO"
                continue
            if table["scope"] != "CONSOLIDATED_EXPLICIT":
                item["status"] = "RESTRICTION_SCOPE_UNVERIFIED_NOT_JOINED"
                continue
            currency = restriction_currency(table, source, checked.get("independent_note_presentation_declarations", []))
            item["restriction_currency_evidence"] = currency
            if not cny or currency["status"] not in {"LOCAL_EXPLICIT_CNY", "MATCHING_INDEPENDENTLY_VERIFIED_NOTE_PRESENTATION"}:
                item["status"] = "MATCHING_CURRENCY_OR_UNIT_NOT_ESTABLISHED"
                continue
            value = source["verified_ending_value_reported_units"]
            if value is None or table["unit"]["scale"] is None:
                item["status"] = "RESTRICTION_VALUE_OR_SCALE_UNKNOWN"
                continue
            value_yuan = Decimal(value) * table["unit"]["scale"]
            cash = result["balance_fields"]["money_funds"]["verified_value_yuan"]
            assets = result["balance_fields"]["assets"]["verified_value_yuan"]
            ratio = proportion(str(value_yuan), cash)
            if ratio["value"] is None:
                item["status"] = ratio["status"]
                continue
            if Decimal(ratio["value"]) > 1:
                item["status"] = "RESTRICTION_ROW_EXCEEDS_TOTAL_MONEY_FUNDS_REVIEW_REQUIRED"
                item["unadopted_source_fraction"] = ratio["value"]
                continue
            item.update(status="VERIFIED_SAME_SOURCE_PERIOD_SCOPE_ROW_RELATIONSHIP",
                        restricted_row_to_money_funds=ratio["value"],
                        restricted_row_to_assets=proportion(str(value_yuan), assets)["value"],
                        verified_restricted_value_yuan=str(value_yuan))
    for field, kind in (("max_verified_restricted_money_fund_row_to_money_funds", "RESTRICTED_MONEY_FUNDS_AS_REPORTED"),
                         ("max_verified_cash_subcategory_row_to_money_funds", "RESTRICTED_CASH_SUBCATEGORY_AS_REPORTED")):
        values = [Decimal(r["restricted_row_to_money_funds"]) for r in result["restricted_cash_rows"]
                  if r["component_kind"] == kind and r["restricted_row_to_money_funds"] is not None]
        result[field] = str(max(values)) if values else None
    result["status"] = "LIMITED_SAME_PERIOD_FINANCING_FACTS_AVAILABLE"
    return result


def state_asof(company: dict, asof: str) -> dict | None:
    moment = datetime.fromisoformat(asof)
    metadata = company["report_metadata"]
    if metadata is None:
        return None
    available = datetime.fromisoformat(metadata["available_at_proxy"])
    if moment.utcoffset() is None or available.utcoffset() is None:
        raise ValueError("financing visibility requires aware timestamps")
    if moment < available or company["status"] not in {"LIMITED_SAME_PERIOD_FINANCING_FACTS_AVAILABLE", "VERIFIED_BALANCE_FINANCIAL_SECTOR_RATIOS_EXCLUDED"}:
        return None
    return company
