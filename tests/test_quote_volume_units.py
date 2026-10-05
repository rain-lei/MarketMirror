from copy import deepcopy
from decimal import Decimal
import unittest

from research.data_pipeline.quote_volume_units import declared_quote_units


def fields(volume="10", amount="10000.00", low="9.90", high="10.10"):
    line = f"2021-01-04,10.00,10.00,{high},{low},{volume},{amount},2,0,0,0.10"
    return dict(zip([f"f{n}" for n in range(51, 62)], line.split(",")))


class QuoteVolumeUnitTests(unittest.TestCase):
    def test_lots_currency_and_percent_have_distinct_conversions(self):
        source = fields()
        original = deepcopy(source)
        result = declared_quote_units(source, 0)
        self.assertEqual(source, original)
        self.assertEqual(Decimal(result["quoted_volume_shares_estimate"]), 1000)
        self.assertEqual(Decimal(result["quoted_amount_currency_estimate"]), Decimal("10000.00"))
        self.assertEqual(Decimal(result["turnover_decimal"]), Decimal(".001"))
        self.assertTrue(result["vwap_rounding_interval_overlaps_price_range"])
        self.assertFalse(result["source_volume_is_exact_share_count"])
        self.assertIsNone(result["economic_return"])
        self.assertFalse(result["model_eligible"])

    def test_incompatible_volume_scale_is_a_diagnostic_failure(self):
        result = declared_quote_units(fields(), 0, volume_scale="1")
        self.assertEqual(result["comparison_status"], "DECLARED_SCALE_RANGE_INCOMPATIBLE")
        self.assertFalse(result["vwap_rounding_interval_overlaps_price_range"])

    def test_interval_intersection_does_not_claim_full_containment(self):
        result = declared_quote_units(fields(volume="1", amount="1000.00", low="10.00", high="10.00"), 0)
        self.assertTrue(result["vwap_rounding_interval_overlaps_price_range"])
        self.assertFalse(result["entire_vwap_rounding_interval_inside_price_range"])

    def test_zero_volume_is_not_suspension_or_a_zero_return(self):
        result = declared_quote_units(fields(volume="0", amount="0.00"), 0)
        self.assertEqual(result["comparison_status"], "ZERO_VOLUME_NO_VWAP_NO_TRADE_STATUS_INFERENCE")
        self.assertIsNone(result["vwap_currency_per_share_estimate"])
        self.assertIsNone(result["economic_return"])

    def test_adjusted_nonpositive_levels_never_enter_execution(self):
        source = fields()
        source.update(f52="-1.00", f53="-1.00", f54="-0.90", f55="-1.10")
        result = declared_quote_units(source, 1)
        self.assertFalse(result["execution_quote_candidate"])
        self.assertIsNone(result["vwap_rounding_interval_overlaps_price_range"])
        self.assertEqual(result["comparison_status"], "ADJUSTED_PRICE_LEVEL_NOT_A_PHYSICAL_VWAP_REFERENCE")

    def test_missing_nonfinite_and_inconsistent_values_are_rejected(self):
        cases = [{"f56": None}, {"f57": "NaN"}, {"f61": "-1"}, {"f54": "9"},
                 {"f56": "0", "f57": "1"}, {"f56": "1", "f57": "0"}]
        for update in cases:
            with self.subTest(update=update), self.assertRaises(ValueError):
                declared_quote_units({**fields(), **update}, 0)

    def test_invalid_scales_and_boolean_parameter_are_rejected(self):
        for scale in ["0", "-1", "Inf", None]:
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                declared_quote_units(fields(), 0, volume_scale=scale)
        with self.assertRaises(ValueError):
            declared_quote_units(fields(), False)


if __name__ == "__main__":
    unittest.main()
