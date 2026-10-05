import unittest

from research.simulation.audit_pre_wuhan_issuer_signed_response_2019 import summarize


def row(after):
    return {"price_before_minor": 10000, "price_after_minor": after, "hypothetical_issuer_valuation_bps": 25.0,
            "accepted_buy": 200, "accepted_sell": 500, "strategy_net": -100, "background_net": -200,
            "filled_buy": 100, "filled_sell": 100, "matched_volume": 100,
            "strategy_filled_net": -100, "background_filled_net": 100}


class IssuerSignedResponseTest(unittest.TestCase):
    def test_prices_determine_signed_means_and_empty_groups_remain_undefined(self):
        result = summarize([row(10100), row(10000), row(9800)])
        self.assertAlmostEqual(result["mean_return_bps"], -100 / 3)
        self.assertEqual(result["positive_return_fraction"], 1 / 3)
        self.assertEqual(result["zero_return_fraction"], 1 / 3)
        self.assertEqual(result["strategy_accepted_net"] + result["background_accepted_net"], result["accepted_buy"] - result["accepted_sell"])
        missing = summarize([])
        self.assertIsNone(missing["mean_return_bps"])
        self.assertIsNone(missing["zero_return_fraction"])

    def test_unbalanced_executed_or_accepted_flow_is_rejected(self):
        bad = row(10100)
        bad["background_net"] += 100
        with self.assertRaisesRegex(ValueError, "does not balance"):
            summarize([bad])
        bad = row(10100)
        bad["filled_sell"] += 100
        with self.assertRaisesRegex(ValueError, "does not balance"):
            summarize([bad])


if __name__ == "__main__":
    unittest.main()
