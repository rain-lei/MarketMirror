import copy
import unittest

from research.baselines.audit_issuer_financing_increment import RESTRICTION, raw_financing_values


def raw_fixture():
    state = {"status": "LIMITED_SAME_PERIOD_FINANCING_FACTS_AVAILABLE", "balance_ratios": {"fake": "999"},
             "max_verified_restricted_money_fund_row_to_money_funds": "999"}
    candidate = {"selected_candidate_index": 0, "candidates": [{
        "current_column_index": 1, "reported_unit": "万元", "currency_declaration": {"status": "CNY_EXPLICIT"},
        "column_mapping": {"columns": [{"period_end": "2018-12-31", "scope": "consolidated"},
                                        {"period_end": "2019-06-30", "scope": "consolidated"}]},
    }]}
    fields = {f: [{"cells": [{"status": "NUMERIC", "reported_value": "999999"},
                             {"status": "NUMERIC", "reported_value": v}]}]
              for f, v in {"money_funds": "200", "assets": "1000", "short_term_borrowings": "40",
                           "noncurrent_liabilities_due_within_one_year": "0", "current_liabilities": "400"}.items()}
    read = {"selected_candidate_index": 0, "status": "PASS", "independent_reading": {"fields": fields},
            "independent_note_presentation_declarations": []}
    table = {"source_number_checks_accepted": True, "scope": "CONSOLIDATED_EXPLICIT",
             "unit": {"currency": "CNY_EXPLICIT", "reported_unit": "万元"},
             "rows": [{"component_kind": "RESTRICTED_MONEY_FUNDS_AS_REPORTED", "source_number_independently_verified": True,
                       "verified_ending_value_reported_units": v, "source_cell": {"pdf_page": 100}} for v in ("5", "8")]}
    return state, candidate, read, {"tables": [table]}


class FinancingIncrementIndependentAuditTest(unittest.TestCase):
    def test_current_physical_cells_override_exported_feature_and_comparison_column(self):
        result = raw_financing_values(*raw_fixture())
        self.assertEqual(result["h1_money_funds_to_assets"], "0.2")
        self.assertEqual(result["h1_short_term_borrowings_to_assets"], "0.04")
        self.assertEqual(result["h1_due_within_one_year_noncurrent_liabilities_to_assets"], "0")
        self.assertEqual(result[RESTRICTION], "0.04")

    def test_repeated_restriction_rows_use_maximum_not_sum_and_ignore_cash_subcategory(self):
        args = raw_fixture()
        rows = args[3]["tables"][0]["rows"]
        rows.append(copy.deepcopy(rows[-1]))
        rows.append({**copy.deepcopy(rows[-1]), "component_kind": "RESTRICTED_CASH_SUBCATEGORY_AS_REPORTED",
                     "verified_ending_value_reported_units": "100"})
        self.assertEqual(raw_financing_values(*args)[RESTRICTION], "0.04")

    def test_parent_restriction_or_unknown_currency_cannot_be_joined(self):
        for field, value in (("scope", "PARENT"), ("currency", "CURRENCY_UNSPECIFIED")):
            args = raw_fixture()
            table = args[3]["tables"][0]
            (table if field == "scope" else table["unit"])[field] = value
            self.assertIsNone(raw_financing_values(*args)[RESTRICTION])

    def test_note_currency_declaration_requires_earlier_page_and_same_scale(self):
        args = raw_fixture()
        args[3]["tables"][0]["unit"]["currency"] = "CURRENCY_UNSPECIFIED"
        d = {"status": "PASS_EXPLICIT_CNY_DECLARATION", "reported_unit": "万元", "pdf_page": 90}
        args[2]["independent_note_presentation_declarations"] = [d]
        self.assertEqual(raw_financing_values(*args)[RESTRICTION], "0.04")
        d["pdf_page"] = 100
        self.assertIsNone(raw_financing_values(*args)[RESTRICTION])
        d.update(pdf_page=90, reported_unit="元")
        self.assertIsNone(raw_financing_values(*args)[RESTRICTION])

    def test_actual_unit_scale_is_applied_to_restriction_and_balance_separately(self):
        args = raw_fixture()
        table = args[3]["tables"][0]
        table["unit"]["reported_unit"] = "元"
        for row in table["rows"]:
            row["verified_ending_value_reported_units"] = str(int(row["verified_ending_value_reported_units"]) * 10000)
        self.assertEqual(raw_financing_values(*args)[RESTRICTION], "0.04")

    def test_blank_debt_and_zero_or_negative_denominator_remain_missing(self):
        args = raw_fixture()
        args[2]["independent_reading"]["fields"]["short_term_borrowings"][0]["cells"][1] = {"status": "REPORTED_BLANK", "reported_value": None}
        self.assertIsNone(raw_financing_values(*args)["h1_short_term_borrowings_to_assets"])
        for value in ("0", "-1", "NaN"):
            args = raw_fixture()
            args[2]["independent_reading"]["fields"]["assets"][0]["cells"][1]["reported_value"] = value
            self.assertIsNone(raw_financing_values(*args)["h1_money_funds_to_assets"])

    def test_excess_single_row_is_not_clipped_to_one(self):
        args = raw_fixture()
        for row in args[3]["tables"][0]["rows"]:
            row["verified_ending_value_reported_units"] = "201"
        self.assertIsNone(raw_financing_values(*args)[RESTRICTION])

    def test_upstream_qc_or_wrong_period_cannot_use_numeric_candidates(self):
        args = raw_fixture()
        args[0]["status"] = "SOURCE_READER_QC_NOT_ADOPTED"
        self.assertTrue(all(v is None for v in raw_financing_values(*args).values()))
        args = raw_fixture()
        args[1]["candidates"][0]["column_mapping"]["columns"][1]["period_end"] = "2019-09-30"
        with self.assertRaisesRegex(ValueError, "current H1"):
            raw_financing_values(*args)


if __name__ == "__main__":
    unittest.main()
