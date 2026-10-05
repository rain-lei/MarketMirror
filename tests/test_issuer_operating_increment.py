import copy
import hashlib
import math
import unittest

from research.baselines.issuer_financial_increment import build_panel
from research.baselines.issuer_operating_increment import BLOCKS, extend_panel, fit_block
from tests.test_issuer_financial_increment import fixture


def operating_fixture(late=False):
    args = fixture()
    calendar, codes, _, _, balances, _, _ = args
    if late:
        balances[codes[0]]["available_at_proxy"] = calendar[27] + "T00:00:00+08:00"
    operating = {}
    for k, code in enumerate(codes):
        balance = balances[code]
        balance.update(scope="consolidated", source_pdf_sha256=hashlib.sha256(code.encode()).hexdigest(),
                       report_metadata={"report_year": 2019, "issuer": code})
        ratios = {name: {"status": "OBSERVED_SIGNED_RATIO" if "net_" in name else "OBSERVED_RATIO",
                         "value": str(.1 * (math.sin((k + 1) * (.37 + j * .23)) + (1.1 if j >= 4 else 0)))}
                  for j, name in enumerate(dict.fromkeys(f for block in BLOCKS.values() for f in block))}
        operating[code] = {
            **{name: balance[name] for name in ("stock_code", "industry_code", "scope", "source_pdf_sha256",
                                               "report_metadata", "available_at_proxy")},
            "status": "INDEPENDENT_OPERATING_EXTRACTION_AGREEMENT", "agent_signal_enabled": False,
            "generic_nonfinancial_ratios_applicable": True, "ratios": ratios,
        }
    rows, split = build_panel(*args)
    return {"parent_stock_codes": codes, "rows": rows, "split": split}, operating, balances


class OperatingIncrementTest(unittest.TestCase):
    def test_only_fixed_blocks_are_accepted(self):
        base, operating, balances = operating_fixture()
        with self.assertRaisesRegex(ValueError, "prespecified"):
            extend_panel(base, operating, balances, "best_after_evaluation")

    def test_missing_asset_removes_both_sides_but_not_other_blocks(self):
        base, operating, balances = operating_fixture()
        code = base["parent_stock_codes"][0]
        operating[code]["ratios"]["trading_financial_assets_to_assets"] = {
            "status": "MISSING_SOURCE_AMOUNT", "value": None}
        assets = extend_panel(base, operating, balances, "secondary_asset_structure")
        affected = [r for r in assets if r["stock_code"] == code]
        self.assertEqual(len(assets), len(base["rows"]))
        self.assertTrue(all(r["paired_exclusion_reason"] == "MISSING_PRESPECIFIED_OPERATING_BLOCK"
                            and all(r[key] is None for key in ("features", "target_absolute_return",
                                                              "financial_decimal_inputs", "operating_decimal_inputs"))
                            for r in affected))
        primary = extend_panel(base, operating, balances, "primary_operating_assets")
        self.assertTrue(all(r["paired_exclusion_reason"] is None for r in primary if r["stock_code"] == code))
        self.assertTrue(all(r["features"] is not None for r in base["rows"]))

    def test_original_exclusion_cannot_be_reinstated_by_new_information(self):
        base, operating, balances = operating_fixture()
        row = base["rows"][0]
        row.update(paired_exclusion_reason="INDUSTRY_SOURCE_NOT_YET_AVAILABLE", features=None,
                   target_absolute_return=None, financial_decimal_inputs=None)
        result = extend_panel(base, operating, balances, "primary_operating_assets")[0]
        self.assertEqual(result["paired_exclusion_reason"], "INDUSTRY_SOURCE_NOT_YET_AVAILABLE")
        self.assertIsNone(result["operating_decimal_inputs"])

    def test_negative_cashflow_and_actual_zero_keep_their_values(self):
        base, operating, balances = operating_fixture()
        code = base["parent_stock_codes"][0]
        operating[code]["ratios"][BLOCKS["primary_operating_assets"][0]] = {
            "status": "OBSERVED_SIGNED_RATIO", "value": "-0.125"}
        operating[code]["ratios"][BLOCKS["primary_operating_assets"][1]] = {
            "status": "OBSERVED_SIGNED_RATIO", "value": "0"}
        row = extend_panel(base, operating, balances, "primary_operating_assets")[0]
        self.assertIsNone(row["paired_exclusion_reason"])
        self.assertEqual(row["features"][BLOCKS["primary_operating_assets"][0]], -.125)
        self.assertEqual(row["operating_decimal_inputs"][BLOCKS["primary_operating_assets"][1]], "0")

    def test_later_revision_stays_unavailable_until_its_own_cutoff(self):
        base, operating, balances = operating_fixture(late=True)
        code = base["parent_stock_codes"][0]
        rows = [r for r in extend_panel(base, operating, balances, "primary_operating_assets")
                if r["stock_code"] == code]
        self.assertEqual([r["paired_exclusion_reason"] for r in rows[:3]],
                         ["FINANCIAL_SOURCE_NOT_YET_AVAILABLE", "FINANCIAL_SOURCE_NOT_YET_AVAILABLE", None])
        self.assertIsNone(rows[0]["operating_decimal_inputs"])
        self.assertIsNotNone(rows[2]["operating_decimal_inputs"])

    def test_issuer_scope_pdf_and_publication_mismatches_are_rejected(self):
        for field, value in (("stock_code", "999999"), ("scope", "parent"),
                             ("source_pdf_sha256", "different"), ("report_metadata", {}),
                             ("available_at_proxy", "2099-01-01T00:00:00+08:00")):
            with self.subTest(field=field):
                base, operating, balances = operating_fixture()
                operating[base["parent_stock_codes"][0]][field] = value
                with self.assertRaisesRegex(ValueError, "association"):
                    extend_panel(base, operating, balances, "primary_operating_assets")

    def test_nonfinite_observed_ratio_is_rejected(self):
        for value in ("NaN", "Infinity", "1E10000"):
            with self.subTest(value=value):
                base, operating, balances = operating_fixture()
                operating[base["parent_stock_codes"][0]]["ratios"][BLOCKS["primary_operating_assets"][0]]["value"] = value
                with self.assertRaisesRegex(ValueError, "nonfinite"):
                    extend_panel(base, operating, balances, "primary_operating_assets")

    def test_all_eight_models_rebuild_on_the_asset_intersection(self):
        base, operating, balances = operating_fixture()
        code = base["parent_stock_codes"][0]
        operating[code]["ratios"]["other_current_assets_to_assets"] = {
            "status": "MISSING_SOURCE_AMOUNT", "value": None}
        panel = extend_panel(base, operating, balances, "secondary_asset_structure")
        result = fit_block(panel, base["split"], "secondary_asset_structure")
        expected = [r for r in panel if r["partition"] == "evaluation" and r["paired_exclusion_reason"] is None]
        self.assertEqual(len(result["models"]), 8)
        self.assertEqual({m["metrics"]["rows"] for m in result["models"].values()}, {33})
        self.assertEqual([(r["stock_code"], r["trade_date"]) for r in result["evaluation_predictions"]],
                         [(r["stock_code"], r["trade_date"]) for r in expected])
        self.assertNotIn(code, result["company_training_row_counts"])
        self.assertEqual(result["models"]["market_industry_financial"]["fit"]["training_rows"], 66)
        self.assertFalse(result["agent_signal_enabled"])

    def test_new_fits_never_use_purged_or_evaluation_targets(self):
        base, operating, balances = operating_fixture()
        for block in BLOCKS:
            with self.subTest(block=block):
                panel = extend_panel(base, operating, balances, block)
                original = fit_block(panel, base["split"], block)
                changed = copy.deepcopy(panel)
                for row in changed:
                    if row["partition"] != "train" and row["paired_exclusion_reason"] is None:
                        row["target_absolute_return"] = .987
                after = fit_block(changed, base["split"], block)
                self.assertEqual(original["models"]["market_industry_financial_operating"]["fit"],
                                 after["models"]["market_industry_financial_operating"]["fit"])
                self.assertEqual([r["predictions"] for r in original["evaluation_predictions"]],
                                 [r["predictions"] for r in after["evaluation_predictions"]])


if __name__ == "__main__":
    unittest.main()
