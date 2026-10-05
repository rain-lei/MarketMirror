import unittest

from research.data_pipeline.market_source_overlap import compare_field, decimal, stock_comparison


class MarketSourceOverlapTests(unittest.TestCase):
    def test_percent_rounding_bound_and_real_disagreement(self):
        self.assertTrue(compare_field("0.40", "0.397900")["within_rounding_hypothesis"])
        self.assertFalse(compare_field("0.40", "0.390000")["within_rounding_hypothesis"])

    def test_volume_scale_is_only_a_hypothesis_and_retains_difference(self):
        row = compare_field("813643", "81364258", "100", True)
        self.assertEqual(row["signed_difference"], "42")
        self.assertEqual(row["rounding_tolerance_hypothesis"], "50")
        self.assertTrue(row["within_rounding_hypothesis"])
        self.assertIs(row["unit_or_return_basis_certified"], False)

    def test_amount_disagreement_is_not_hidden_by_relative_size(self):
        row = compare_field("1228342736.00", "1228342741.9500")
        self.assertFalse(row["within_rounding_hypothesis"])
        self.assertEqual(row["absolute_difference"], "5.9500")

    def test_zero_reference_keeps_ratio_unknown(self):
        self.assertIsNone(compare_field("0", "0", "100", True)["ratio_when_reference_nonzero"])

    def test_unknown_nonfinite_and_fractional_reference_share_counts_rejected(self):
        for value in ["", "-", "NaN", "Infinity", "abc"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                decimal(value)
        with self.assertRaises(ValueError):
            compare_field("1", "1.5", "100", True)

    def test_backward_prices_and_suspension_are_not_inferred_from_candidate(self):
        fields = {"f59": "0.00", "f56": "0", "f57": "0.00"}
        reference = {"pctChg": "0.000000", "volume": "0", "amount": "0.0000",
                     "adjustflag": "1", "tradestatus": "0", "close": "100.00", "preclose": "100.00"}
        row = stock_comparison(fields, reference)
        self.assertFalse(row["stock_price_levels_compared"])
        self.assertEqual(row["reference_tradestatus"], "0")
        with self.assertRaises(ValueError):
            stock_comparison(fields, {**reference, "volume": "100"})


if __name__ == "__main__":
    unittest.main()
