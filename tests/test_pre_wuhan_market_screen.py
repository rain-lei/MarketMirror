import copy
import json
import unittest
from pathlib import Path

from research.simulation.screen_pre_wuhan_market_2019 import (
    POLICY, _validate_policy, _verify_daily_rows, evaluate,
)
from research.simulation.audit_synthetic_observed_returns import compare_pairs


class PreWuhanMarketScreenTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads(Path(POLICY).read_text(encoding="utf-8"))

    def test_a_single_matching_frequency_does_not_advance_an_uncalibrated_market(self):
        pairs = [(0.0, 0.0), (0.001, 0.02), (-0.001, -0.02), (0.0, 0.0)]
        comparison = compare_pairs(pairs)
        policy = {**self.policy, "pairs_per_variant": len(pairs)}
        result = evaluate(comparison, policy, True)
        self.assertTrue(result["checks"]["zero_return_frequency"])
        self.assertFalse(result["checks"]["volatility_scale"])
        self.assertFalse(result["development_screen_pass"])
        self.assertFalse(evaluate(comparison, policy, False)["checks"]["company_day_trace"])

    def test_frozen_policy_rejects_threshold_or_sample_changes(self):
        _validate_policy(self.policy)
        for changed in ({**self.policy, "volatility_ratio_min": 0.01},
                        {**self.policy, "companies": 122},
                        {**self.policy, "archives": list(reversed(self.policy["archives"]))}):
            with self.assertRaises(ValueError):
                _validate_policy(changed)

    def test_daily_trace_rejects_role_imbalance(self):
        rows = [{"stock_code": "000001", "trade_date": "2019-11-01",
                 "price_before_minor": 10000, "price_after_minor": 10100,
                 "observed_return": 0.02, "strategy_requested_net": -100,
                 "strategy_net": -100, "strategy_filled_net": -100,
                 "strategy_role_flow": {"conservative": {"requested_net": -100,
                                                          "accepted_net": -100, "filled_net": -100}}},
                {"stock_code": "000001", "trade_date": "2019-11-04",
                 "price_before_minor": 10100, "price_after_minor": 10000,
                 "observed_return": -0.02, "strategy_requested_net": 0,
                 "strategy_net": 0, "strategy_filled_net": 0,
                 "strategy_role_flow": {}}]
        variant = {"daily_asset_rows": rows,
                   "comparison": compare_pairs([(row["price_after_minor"] / row["price_before_minor"] - 1,
                                                  row["observed_return"]) for row in rows])}
        policy = {**self.policy, "pairs_per_variant": 2, "sessions": 2}
        self.assertTrue(_verify_daily_rows(variant, ["000001"], policy))
        altered = copy.deepcopy(variant)
        altered["daily_asset_rows"][0]["strategy_role_flow"]["conservative"]["requested_net"] = 0
        with self.assertRaisesRegex(ValueError, "role requested flow"):
            _verify_daily_rows(altered, ["000001"], policy)


if __name__ == "__main__":
    unittest.main()
