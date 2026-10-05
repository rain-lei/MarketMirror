import copy
import hashlib
import math
import unittest

from research.baselines.issuer_financial_increment import build_panel
from research.baselines.issuer_financing_increment import (
    BLOCKS, INTERACTIONS, RESTRICTION_FEATURE, RESTRICTION_SOURCE_FIELD,
    RATIO_FEATURES, extend_panel, fit_block,
)
from tests.test_issuer_financial_increment import fixture


def financing_fixture():
    args = fixture()
    rows, split = build_panel(*args)
    for row in rows:
        for k in ("trade_date", "signal_cutoff_date", "signal_cutoff_at", "execution_reference_date"):
            row[k] = row[k].replace("2019-", "2020-")
    for key, value in split.items():
        split[key] = [v.replace("2019-", "2020-") for v in value] if isinstance(value, list) else value.replace("2019-", "2020-")
    states = {}
    for i, code in enumerate(args[1]):
        states[code] = {
            "stock_code": code, "industry_code": args[4][code]["industry_code"], "agent_signal_enabled": False,
            "status": "LIMITED_SAME_PERIOD_FINANCING_FACTS_AVAILABLE", "balance_scope": "consolidated",
            "financial_sector_generic_ratios_excluded": False, "literal_reader_status": "PASS_LITERAL_PAGE_MAPS",
            "independent_balance_status": "PASS", "source_pdf_sha256": hashlib.sha256(("H1" + code).encode()).hexdigest(),
            "report_metadata": {"stock_code": code, "report_end_date": "2019-06-30", "eligible": True,
                                "available_at_proxy": "2019-08-31T00:00:00+08:00"},
            RESTRICTION_SOURCE_FIELD: str(.15 + .09 * math.sin((i + 1) * .83)),
            "balance_ratios": {name: {"status": "CALCULATED_FROM_VERIFIED_SAME_PERIOD_SOURCE_VALUES",
                                      "value": str(.25 + .1 * math.sin((i + 1) * (.41 + j * .27)))}
                               for j, name in enumerate(RATIO_FEATURES.values())},
        }
    return {"parent_stock_codes": args[1], "rows": rows, "split": split}, states


class FinancingIncrementTest(unittest.TestCase):
    def test_unknown_block_cannot_be_selected_after_results(self):
        base, states = financing_fixture()
        with self.assertRaisesRegex(ValueError, "prespecified"):
            extend_panel(base, states, "best_result")

    def test_absent_restriction_is_missing_for_both_models_and_keeps_grid(self):
        base, states = financing_fixture()
        code = base["parent_stock_codes"][0]
        states[code][RESTRICTION_SOURCE_FIELD] = None
        rows = extend_panel(base, states, "primary_restricted_cash_buffer")
        self.assertEqual(len(rows), len(base["rows"]))
        for row in [r for r in rows if r["stock_code"] == code]:
            self.assertEqual(row["paired_exclusion_reason"], "MISSING_PRESPECIFIED_FINANCING_BLOCK")
            self.assertTrue(all(row[k] is None for k in ("features", "target_absolute_return", "financial_decimal_inputs", "financing_decimal_inputs")))
        other = extend_panel(base, states, "secondary_maturity_cash_coverage")
        self.assertTrue(all(r["paired_exclusion_reason"] is None for r in other if r["stock_code"] == code))

    def test_blank_debt_is_not_zero_but_actual_zero_is_retained(self):
        base, states = financing_fixture()
        code = base["parent_stock_codes"][0]
        field = RATIO_FEATURES[BLOCKS["secondary_maturity_cash_coverage"][0]]
        states[code]["balance_ratios"][field] = {"status": "MISSING_SOURCE_VALUE_NOT_ZERO", "value": None}
        self.assertIsNone(extend_panel(base, states, "secondary_maturity_cash_coverage")[0]["features"])
        states[code]["balance_ratios"][field] = {"status": "CALCULATED_FROM_VERIFIED_SAME_PERIOD_SOURCE_VALUES", "value": "0"}
        row = extend_panel(base, states, "secondary_maturity_cash_coverage")[0]
        self.assertEqual(row["features"][BLOCKS["secondary_maturity_cash_coverage"][0]], 0)
        self.assertEqual(row["financing_decimal_inputs"][BLOCKS["secondary_maturity_cash_coverage"][0]], "0")

    def test_h1_revision_enters_only_at_its_own_cutoff(self):
        base, states = financing_fixture()
        code = base["parent_stock_codes"][0]
        own = [r for r in base["rows"] if r["stock_code"] == code]
        states[code]["report_metadata"]["available_at_proxy"] = own[2]["signal_cutoff_at"]
        rows = [r for r in extend_panel(base, states, "primary_restricted_cash_buffer") if r["stock_code"] == code]
        self.assertEqual([r["paired_exclusion_reason"] for r in rows[:3]],
                         ["FINANCING_SOURCE_NOT_YET_AVAILABLE", "FINANCING_SOURCE_NOT_YET_AVAILABLE", None])

    def test_base_exclusion_is_never_reinstated_by_h1_sources(self):
        base, states = financing_fixture()
        base["rows"][0].update(paired_exclusion_reason="FINANCIAL_SOURCE_NOT_YET_AVAILABLE", features=None,
                                target_absolute_return=None, financial_decimal_inputs=None)
        row = extend_panel(base, states, "primary_restricted_cash_buffer")[0]
        self.assertEqual(row["paired_exclusion_reason"], "FINANCIAL_SOURCE_NOT_YET_AVAILABLE")
        self.assertIsNone(row["financing_decimal_inputs"])

    def test_source_qc_and_missing_states_are_preserved(self):
        for status in ("SOURCE_MISSING_NOT_ZERO", "SOURCE_READER_QC_NOT_ADOPTED",
                       "BALANCE_SOURCE_OR_INDEPENDENT_READ_QC_NOT_ADOPTED",
                       "VERIFIED_BALANCE_FINANCIAL_SECTOR_RATIOS_EXCLUDED"):
            with self.subTest(status=status):
                base, states = financing_fixture()
                states[base["parent_stock_codes"][0]]["status"] = status
                row = extend_panel(base, states, "primary_restricted_cash_buffer")[0]
                self.assertEqual(row["paired_exclusion_reason"], "FINANCING_" + status)
                self.assertIsNone(row["features"])

    def test_parent_scope_wrong_identity_and_industry_cannot_enter(self):
        for field, value in (("balance_scope", "parent"), ("stock_code", "999999"), ("industry_code", "J66"),
                             ("agent_signal_enabled", True), ("literal_reader_status", "REVIEW_REQUIRED")):
            with self.subTest(field=field):
                base, states = financing_fixture()
                states[base["parent_stock_codes"][0]][field] = value
                with self.assertRaises(ValueError):
                    extend_panel(base, states, "primary_restricted_cash_buffer")

    def test_wrong_report_period_or_naive_clock_is_rejected(self):
        for field, value in (("report_end_date", "2019-09-30"), ("available_at_proxy", "2019-08-31T00:00:00")):
            base, states = financing_fixture()
            states[base["parent_stock_codes"][0]]["report_metadata"][field] = value
            with self.assertRaises(ValueError):
                extend_panel(base, states, "primary_restricted_cash_buffer")

    def test_invalid_or_excess_restriction_fraction_is_rejected(self):
        for value in ("NaN", "Infinity", "-0.1", "1.01"):
            base, states = financing_fixture()
            states[base["parent_stock_codes"][0]][RESTRICTION_SOURCE_FIELD] = value
            with self.assertRaises(ValueError):
                extend_panel(base, states, "primary_restricted_cash_buffer")

    def test_interaction_uses_lagged_features_and_is_unaffected_by_target(self):
        base, states = financing_fixture()
        block = "secondary_restriction_market_interactions"
        row = extend_panel(base, states, block)[0]
        base["rows"][0]["target_absolute_return"] = .99
        changed = extend_panel(base, states, block)[0]
        self.assertEqual(row["features"], changed["features"])
        for feature, (left, right) in INTERACTIONS.items():
            self.assertEqual(row["features"][feature], row["features"][left] * base["rows"][0]["features"][right])

    def test_h1_cash_keeps_own_ratio_instead_of_using_q3_cash(self):
        base, states = financing_fixture()
        row = extend_panel(base, states, "primary_restricted_cash_buffer")[0]
        self.assertNotEqual(row["features"]["h1_money_funds_to_assets"], row["features"]["money_funds_to_assets"])
        self.assertEqual(row["financing_available_at_proxy"], "2019-08-31T00:00:00+08:00")
        self.assertEqual(row["financial_available_at_proxy"], base["rows"][0]["financial_available_at_proxy"])

    def test_all_eight_models_use_same_reduced_company_dates(self):
        base, states = financing_fixture()
        states[base["parent_stock_codes"][0]][RESTRICTION_SOURCE_FIELD] = None
        rows = extend_panel(base, states, "primary_restricted_cash_buffer")
        result = fit_block(rows, base["split"], "primary_restricted_cash_buffer")
        self.assertEqual(len(result["models"]), 8)
        self.assertEqual(result["training_rows"], 11 * len(base["split"]["training_dates"]))
        self.assertEqual({m["metrics"]["rows"] for m in result["models"].values()}, {11 * 3})
        self.assertNotIn(base["parent_stock_codes"][0], result["company_training_row_counts"])
        self.assertFalse(result["agent_signal_enabled"])

    def test_constant_training_feature_fails_instead_of_being_silently_dropped(self):
        base, states = financing_fixture()
        for state in states.values():
            state[RESTRICTION_SOURCE_FIELD] = "0.1"
        rows = extend_panel(base, states, "primary_restricted_cash_buffer")
        with self.assertRaisesRegex(ValueError, "variation"):
            fit_block(rows, base["split"], "primary_restricted_cash_buffer")

    def test_building_blocks_does_not_modify_original_panel_or_states(self):
        base, states = financing_fixture()
        saved_base, saved_states = copy.deepcopy(base), copy.deepcopy(states)
        for block in BLOCKS:
            extend_panel(base, states, block)
        self.assertEqual(base, saved_base)
        self.assertEqual(states, saved_states)


if __name__ == "__main__":
    unittest.main()
