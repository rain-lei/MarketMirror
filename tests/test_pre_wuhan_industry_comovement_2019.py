import copy
import unittest

from research.simulation.audit_pre_wuhan_industry_comovement_2019 import diagnose


def panel_fixture():
    values = {"000001": [0.1, -0.1, 0.1, -0.1], "000002": [0.1, -0.1, 0.1, -0.1],
              "600003": [0.1, 0.1, -0.1, -0.1], "600004": [0.1, 0.1, -0.1, -0.1]}
    rows = [{"stock_code": code, "trade_date": f"2019-11-{index + 1:02d}", "observed_return": value,
             "price_before_minor": 10000, "price_after_minor": 10000 + int(value * 10000)}
            for code, returns in values.items() for index, value in enumerate(returns)]
    groups = {"000001": "C19", "000002": "C19", "600003": "J66", "600004": "J66"}
    baskets = {"000001": 0, "000002": 1, "600003": 0, "600004": 1}
    return rows, groups, baskets


class IndustryComovementTest(unittest.TestCase):
    def test_known_industry_structure_and_panel_removal(self):
        metrics, pairs, codes, dates = diagnose(*panel_fixture())
        self.assertEqual(len(codes), 4)
        self.assertEqual(len(dates), 4)
        raw = metrics["observed"]["raw"]
        self.assertEqual(raw["same_industry"]["total_pairs"], 2)
        self.assertEqual(raw["different_industry"]["total_pairs"], 4)
        self.assertAlmostEqual(raw["same_industry"]["mean_correlation"], 1)
        self.assertAlmostEqual(raw["different_industry"]["mean_correlation"], 0)
        self.assertAlmostEqual(metrics["observed"]["stock_minus_same_day_panel_mean"]["within_minus_between"], 2)
        self.assertEqual(len(pairs["observed"]), 6)
        self.assertEqual(raw["by_industry_and_basket"]["same_industry_different_basket"]["total_pairs"], 2)
        self.assertEqual(raw["by_industry_and_basket"]["same_industry_same_basket"]["mean_correlation"], None)

    def test_zero_variance_and_singletons_remain_undefined(self):
        rows, groups, baskets = panel_fixture()
        for row in rows:
            row["observed_return"] = 0.0
            row["price_after_minor"] = row["price_before_minor"]
        metrics, pairs, _, _ = diagnose(rows, groups, baskets)
        self.assertEqual(metrics["observed"]["raw"]["all"]["undefined_pairs"], 6)
        self.assertIsNone(metrics["observed"]["raw"]["within_minus_between"])
        self.assertTrue(all(row["raw"] is None for row in pairs["observed"]))
        rows, _, baskets = panel_fixture()
        groups = {code: f"C{13 + index}" for index, code in enumerate(baskets)}
        metrics, _, _, _ = diagnose(rows, groups, baskets)
        self.assertIsNone(metrics["observed"]["raw"]["same_industry"]["mean_correlation"])
        self.assertEqual(metrics["observed"]["raw"]["industries_with_defined_within_pairs"], 0)

    def test_duplicates_missing_dates_and_unknown_industry_are_rejected(self):
        rows, groups, baskets = panel_fixture()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            diagnose(rows + [copy.deepcopy(rows[0])], groups, baskets)
        with self.assertRaisesRegex(ValueError, "complete coverage"):
            diagnose(rows[:-1], groups, baskets)
        groups["000001"] = None
        with self.assertRaisesRegex(ValueError, "null labels"):
            diagnose(rows, groups, baskets)

    def test_invalid_outcomes_and_mismatched_grouping_cannot_be_silently_dropped(self):
        rows, groups, baskets = panel_fixture()
        for invalid in (float("nan"), True, -1.1):
            changed = copy.deepcopy(rows)
            changed[0]["observed_return"] = invalid
            with self.assertRaisesRegex(ValueError, "invalid observed"):
                diagnose(changed, groups, baskets)
        groups.pop("000001")
        with self.assertRaisesRegex(ValueError, "cover every stock"):
            diagnose(rows, groups, baskets)


if __name__ == "__main__":
    unittest.main()
