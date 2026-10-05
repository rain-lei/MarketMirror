"""A green state audit must detect incorrect ratios, zeros and release flags."""

import unittest

from research.data_pipeline.audit_issuer_half_financing_states import audit_company
from research.data_pipeline.half_year_financing_states import build_company
from tests.test_half_year_financing_states import fixture


def audited_fixture():
    candidate, read, restricted = fixture()
    numeric = {"assets": ("100.00", "90.00"), "liabilities": ("60.00", "50.00"),
               "equity": ("40.00", "40.00"), "liabilities_and_equity": ("100.00", "90.00"),
               "money_funds": ("10.00", "20.00")}
    fields = {key: [{"cells": [{"status": "NUMERIC", "reported_value": value} for value in numeric[key]]}]
              if key in numeric else [] for key in candidate["candidates"][0]["fields"]}
    read["independent_reading"] = {"fields": fields}
    state = build_company(candidate, read, restricted)
    return state, candidate, read, restricted


class IndependentFinancingStateAuditTests(unittest.TestCase):
    def test_consistent_limited_state_passes_independent_arithmetic(self):
        value = audit_company(*audited_fixture())
        self.assertEqual(value["status"], "PASS_STATE_CONTRACT")
        self.assertEqual(value["counts"]["current_balance_cells_checked"], 16)

    def test_incorrect_restricted_fraction_is_detected(self):
        values = audited_fixture()
        values[0]["restricted_cash_rows"][0]["restricted_row_to_money_funds"] = "0.21"
        self.assertEqual(audit_company(*values)["status"], "FAIL_STATE_CONTRACT")

    def test_missing_debt_cannot_become_numeric_zero(self):
        values = audited_fixture()
        values[0]["balance_fields"]["bonds_payable"]["verified_value_reported_units"] = "0"
        self.assertEqual(audit_company(*values)["status"], "FAIL_STATE_CONTRACT")

    def test_unsupported_free_cash_or_agent_release_is_detected(self):
        for key, value in (("unrestricted_money_funds", "8"), ("agent_signal_enabled", True)):
            values = audited_fixture()
            values[0][key] = value
            self.assertEqual(audit_company(*values)["status"], "FAIL_STATE_CONTRACT")

    def test_max_row_statistic_cannot_become_total_sum(self):
        values = audited_fixture()
        values[0]["max_verified_restricted_money_fund_row_to_money_funds"] = "0.4"
        self.assertEqual(audit_company(*values)["status"], "FAIL_STATE_CONTRACT")

    def test_qc_gate_cannot_leave_accepted_cash_values(self):
        values = audited_fixture()
        for record in values:
            record["literal_reader_status"] = "READER_DIAGNOSTIC_REVIEW_REQUIRED"
        self.assertEqual(audit_company(*values)["status"], "FAIL_STATE_CONTRACT")


if __name__ == "__main__":
    unittest.main()
