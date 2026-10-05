"""Independently check financing relationships against already re-read cells."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path

from .fetch_issuer_disclosures import ROOT, digest, serialize, validate_bindings

CONFIG = ROOT / "research/configs/issuer_half_financing_state_audit_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_half_financing_state_audit_2019_v1.json"
SCALES = {"元": 1, "千元": 1000, "万元": 10000, "百万元": 1000000, "亿元": 100000000}
FORBIDDEN = ("unrestricted_money_funds", "cash_available_for_trading", "total_restricted_money_funds",
             "complete_interest_bearing_debt", "cash_to_complete_interest_bearing_debt", "financing_impact_coefficient")
RATIOS = {"money_funds_to_assets": ("money_funds", "assets"), "liabilities_to_assets": ("liabilities", "assets"),
          "current_assets_to_current_liabilities": ("current_assets", "current_liabilities"),
          "money_funds_to_current_liabilities": ("money_funds", "current_liabilities"),
          "short_term_borrowings_to_assets": ("short_term_borrowings", "assets"),
          "due_within_one_year_noncurrent_liabilities_to_assets": ("noncurrent_liabilities_due_within_one_year", "assets"),
          "long_term_borrowings_to_assets": ("long_term_borrowings", "assets"),
          "bonds_payable_to_assets": ("bonds_payable", "assets")}


def quotient(n, d):
    if n is None or d is None:
        return None
    a, b = Decimal(n), Decimal(d)
    if not a.is_finite() or not b.is_finite() or a < 0 or b <= 0:
        return None
    with localcontext() as context:
        context.prec = 50
        return str(a / b)


def map_issuers(companies):
    result = {c["stock_code"]: c for c in companies}
    if len(result) != len(companies):
        raise ValueError("independent financing duplicate issuer")
    return result


def audit_company(state, candidate, read, restricted):
    issues, counts = [], Counter()
    def require(condition, issue):
        if not condition:
            issues.append(issue)
    for original in (candidate, read, restricted):
        require(state["stock_code"] == original["stock_code"] and state["source_pdf_sha256"] == original["source_pdf_sha256"],
                "SOURCE_ISSUER_OR_PDF_ASSOCIATION")
    require(state["report_metadata"] == candidate["report_metadata"] == restricted["report_metadata"], "REPORT_METADATA_ASSOCIATION")
    require(state["literal_reader_status"] == candidate["literal_reader_status"] == read["literal_reader_status"] == restricted["literal_reader_status"],
            "SOURCE_READER_ASSOCIATION")
    require(all(state[k] is None for k in FORBIDDEN) and state["agent_signal_enabled"] is False
            and state["all_company_restrictions_completely_disclosed_verified"] is False, "UNSUPPORTED_FINANCING_RELEASE")
    if candidate["source_pdf_path"] is None:
        expected_status = "SOURCE_MISSING_NOT_ZERO"
    elif candidate["literal_reader_status"] != "PASS_LITERAL_PAGE_MAPS":
        expected_status = "SOURCE_READER_QC_NOT_ADOPTED"
    elif read["status"] != "PASS" or candidate["status"] != "UNIQUE_RECONCILED_CANDIDATE":
        expected_status = "BALANCE_SOURCE_OR_INDEPENDENT_READ_QC_NOT_ADOPTED"
    else:
        expected_status = "VERIFIED_BALANCE_FINANCIAL_SECTOR_RATIOS_EXCLUDED" if candidate["industry_code"].startswith("J") else "LIMITED_SAME_PERIOD_FINANCING_FACTS_AVAILABLE"
    require(state["status"] == expected_status, "STATE_STATUS_OR_GATE")
    if expected_status not in {"LIMITED_SAME_PERIOD_FINANCING_FACTS_AVAILABLE", "VERIFIED_BALANCE_FINANCIAL_SECTOR_RATIOS_EXCLUDED"}:
        require(not state["balance_fields"] and not state["balance_ratios"] and not state["restricted_cash_rows"], "QC_STATE_HAS_ACCEPTED_VALUES")
        counts["preserved_qc_or_missing_companies"] += 1
        return {"stock_code": state["stock_code"], "status": "PASS_STATE_CONTRACT" if not issues else "FAIL_STATE_CONTRACT", "issues": issues, "counts": dict(counts)}
    t = candidate["candidates"][candidate["selected_candidate_index"]]
    i = t["current_column_index"]
    require(t["column_mapping"]["columns"][i]["period_end"] == "2019-06-30" and t["column_mapping"]["columns"][i]["scope"] == "consolidated", "BALANCE_PERIOD_OR_SCOPE")
    require(read["selected_candidate_index"] == candidate["selected_candidate_index"], "INDEPENDENT_CANDIDATE_INDEX")
    cny = t["currency_declaration"]["status"] == "CNY_EXPLICIT"
    unit = t["reported_unit"]
    independent, in_yuan = {}, {}
    for field, value in state["balance_fields"].items():
        found = read["independent_reading"]["fields"][field]
        if not found:
            status, number = "ROW_ABSENT", None
        elif len(found) == 1:
            status, number = found[0]["cells"][i]["status"], found[0]["cells"][i]["reported_value"]
        else:
            status, number = "MULTIPLE_INDEPENDENT_ROWS", None
        numeric = status == "NUMERIC"
        independent[field] = number if numeric else None
        scaled = str(Decimal(number) * SCALES[unit]) if numeric else None
        in_yuan[field] = scaled if cny else None
        require(value["source_status"] == status and value["source_number_independently_verified"] == numeric, field + ":SOURCE_STATUS")
        require(value["verified_value_reported_units"] == independent[field] and value["verified_value_at_reported_unit_scale"] == scaled
                and value["verified_value_yuan"] == in_yuan[field], field + ":VALUE_OR_CURRENCY")
        counts["current_balance_cells_checked"] += 1
    if expected_status == "VERIFIED_BALANCE_FINANCIAL_SECTOR_RATIOS_EXCLUDED":
        require(not state["balance_ratios"] and not state["restricted_cash_rows"], "FINANCIAL_SECTOR_GENERIC_RATIO")
        counts["financial_sector_exclusions"] += 1
    else:
        require(set(state["balance_ratios"]) == set(RATIOS), "BALANCE_RATIO_KEYS")
        for key, (numerator, denominator) in RATIOS.items():
            expected = quotient(independent[numerator], independent[denominator])
            actual = state["balance_ratios"].get(key, {})
            require(actual.get("value") == expected and actual.get("numerator_field") == numerator
                    and actual.get("denominator_field") == denominator, key + ":INDEPENDENT_RATIO")
            counts["balance_ratio_values_and_missingness_checked"] += 1
        original_rows = {(t["table_index"], i): (t, row) for t in restricted["tables"] for i, row in enumerate(t["rows"])
                         if row["component_kind"] in {"RESTRICTED_MONEY_FUNDS_AS_REPORTED", "RESTRICTED_CASH_SUBCATEGORY_AS_REPORTED"}}
        actual_rows = {(r["table_index"], r["row_index"]): r for r in state["restricted_cash_rows"]}
        require(len(actual_rows) == len(state["restricted_cash_rows"]) and set(actual_rows) == set(original_rows), "RESTRICTION_ROW_MEMBERSHIP")
        for key, actual in actual_rows.items():
            table, original = original_rows[key]
            require(actual["source_cell"] == original["source_cell"] and actual["component_kind"] == original["component_kind"], "RESTRICTION_ROW_ASSOCIATION")
            amount, fraction, asset_fraction = None, None, None
            expected_row_status = "RESTRICTION_VALUE_MISSING_OR_UNVERIFIED_NOT_ZERO"
            if original["source_number_independently_verified"] and table["source_number_checks_accepted"]:
                if table["scope"] != "CONSOLIDATED_EXPLICIT":
                    expected_row_status = "RESTRICTION_SCOPE_UNVERIFIED_NOT_JOINED"
                else:
                    unit_info = table["unit"]
                    declaration = actual.get("restriction_currency_evidence", {})
                    good_currency = False
                    if declaration.get("status") == "LOCAL_EXPLICIT_CNY":
                        good_currency = unit_info["currency"] == "CNY_EXPLICIT" and declaration["evidence"] == unit_info["evidence"]
                    elif declaration.get("status") == "MATCHING_INDEPENDENTLY_VERIFIED_NOTE_PRESENTATION":
                        item = declaration["evidence"]
                        good_currency = (unit_info["currency"] == "CURRENCY_UNSPECIFIED" and item in read["independent_note_presentation_declarations"]
                                         and item["status"] == "PASS_EXPLICIT_CNY_DECLARATION" and item["reported_unit"] == unit_info["reported_unit"]
                                         and item["pdf_page"] < original["source_cell"]["pdf_page"])
                    expected_row_status = "MATCHING_CURRENCY_OR_UNIT_NOT_ESTABLISHED"
                    if cny and good_currency:
                        source_value = original["verified_ending_value_reported_units"]
                        amount = str(Decimal(source_value) * SCALES[unit_info["reported_unit"]])
                        fraction = quotient(amount, in_yuan["money_funds"])
                        asset_fraction = quotient(amount, in_yuan["assets"])
                        if fraction is None:
                            expected_row_status = "MISSING_SOURCE_VALUE_NOT_ZERO" if in_yuan["money_funds"] is None else "INVALID_SIGN_OR_NONPOSITIVE_DENOMINATOR"
                            amount, asset_fraction = None, None
                        elif Decimal(fraction) > 1:
                            expected_row_status = "RESTRICTION_ROW_EXCEEDS_TOTAL_MONEY_FUNDS_REVIEW_REQUIRED"
                            amount, fraction, asset_fraction = None, None, None
                        else:
                            expected_row_status = "VERIFIED_SAME_SOURCE_PERIOD_SCOPE_ROW_RELATIONSHIP"
            require(actual["status"] == expected_row_status and actual["restricted_row_to_money_funds"] == fraction
                    and actual["restricted_row_to_assets"] == asset_fraction and actual["verified_restricted_value_yuan"] == amount,
                    f"RESTRICTION_ROW_{key}:INDEPENDENT_RELATIONSHIP")
            counts["restricted_cash_row_values_and_missingness_checked"] += 1
        for field, kind in (("max_verified_restricted_money_fund_row_to_money_funds", "RESTRICTED_MONEY_FUNDS_AS_REPORTED"),
                             ("max_verified_cash_subcategory_row_to_money_funds", "RESTRICTED_CASH_SUBCATEGORY_AS_REPORTED")):
            values = [Decimal(r["restricted_row_to_money_funds"]) for r in actual_rows.values()
                      if r["component_kind"] == kind and r["restricted_row_to_money_funds"] is not None]
            require(state[field] == (str(max(values)) if values else None), field + ":MAX_SINGLE_ROW_NOT_TOTAL")
    return {"stock_code": state["stock_code"], "status": "PASS_STATE_CONTRACT" if not issues else "FAIL_STATE_CONTRACT",
            "issues": issues, "counts": dict(counts)}


def compute():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    validate_bindings(cfg["inputs"])
    if cfg["agent_signal_enabled"] is not False:
        raise ValueError("independent financing state gate differs")
    values = [json.loads((ROOT / cfg[key]).read_text(encoding="utf-8"))
              for key in ("states_path", "candidates_path", "audit_path", "restricted_states_path")]
    states, candidates, audit, restricted = values
    maps = [map_issuers(v["companies"]) for v in values]
    if len(maps[0]) != cfg["expected_companies"] or any(set(m) != set(maps[0]) for m in maps[1:]):
        raise ValueError("independent financing state cohort differs")
    bindings = {**states["inputs"], **states["code_sha256"], **cfg["inputs"], CONFIG.relative_to(ROOT).as_posix(): digest(CONFIG)}
    code = {f"research/data_pipeline/{name}.py": digest(ROOT / f"research/data_pipeline/{name}.py")
            for name in ("audit_issuer_half_financing_states", "fetch_issuer_disclosures")}
    validate_bindings({**bindings, **code})
    companies = [audit_company(*[m[c] for m in maps]) for c in sorted(maps[0])]
    counts = Counter()
    for row in companies:
        counts.update(row["counts"])
    validate_bindings({**bindings, **code})
    return {"pipeline_version": cfg["version"], "companies": companies,
            "summary": {"companies": len(companies), "status_counts": dict(Counter(c["status"] for c in companies)), **dict(counts)},
            "status": "PASS_STATE_CONTRACT" if all(c["status"] == "PASS_STATE_CONTRACT" for c in companies) else "FAIL_STATE_CONTRACT",
            "inputs": dict(sorted(bindings.items())), "code_sha256": code, "agent_signal_enabled": False,
            "interpretation": cfg["interpretation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    value = compute()
    raw = serialize(value)
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("independent financing state output must be directly under research_outputs")
    if args.audit_existing:
        if output.read_bytes() != raw:
            raise ValueError("independent financing state reconstruction differs")
        print("Independent half-year financing state audit rebuilt byte-identically.")
    else:
        with output.open("xb") as handle:
            handle.write(raw)
    print(json.dumps(value["summary"], ensure_ascii=False))
    if value["status"] != "PASS_STATE_CONTRACT":
        for company in value["companies"]:
            if company["issues"]:
                print(json.dumps(company, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
