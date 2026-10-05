import copy
import unittest

from research.simulation.temporal_warmup_metrics import evaluation_accounts


class TemporalWarmupEvaluationTests(unittest.TestCase):
    def fixture(self):
        specs = {"strategy": {"kind": "strategy", "parameters": {
            "role": "conservative", "risk_budget": 1, "max_weight": 1}}}
        trace = []
        for i, price in enumerate((100, 90, 99)):
            trace.append({"trade_date": f"2021-01-0{i + 1}", "covariance": {"stock": {"stock": 0.01}},
                "decisions": {"strategy": {"asset_weight_cap": 1}}, "portfolio_auction": {
                    "prices_before_minor": {"stock": (100, 100, 90)[i]}, "fee_pool_minor": (123, 128, 133)[i],
                    "asset_calls": {"stock": {"price_after_minor": price}},
                    "accounts": {"strategy": {"wallets": {"stock": 100}, "shares": {"stock": 10}, "sellable": {"stock": 10}}}}})
        return trace, specs

    def test_warmup_gains_and_fees_are_excluded_but_opening_resources_are_carried(self):
        trace, specs = self.fixture()
        result = evaluation_accounts(trace, specs, 1)
        account = result["accounts"]["strategy"]
        self.assertEqual(account["opening_evaluation_wealth_minor"], 1100)
        self.assertEqual(account["final_wealth_minor"], 1090)
        self.assertAlmostEqual(account["evaluation_wealth_multiple"], 1090 / 1100)
        self.assertAlmostEqual(account["evaluation_max_drawdown"], 1 - 1000 / 1100)
        self.assertEqual(result["evaluation_fee_increment_minor"], 10)
        self.assertEqual(result["evaluation_sessions"], 2)
        self.assertEqual(account["risk_breach_sessions"], 0)

    def test_reset_price_or_missing_preperiod_is_rejected(self):
        trace, specs = self.fixture()
        for offset in (0, 3, True):
            with self.assertRaises(ValueError):
                evaluation_accounts(trace, specs, offset)
        changed = copy.deepcopy(trace)
        changed[1]["portfolio_auction"]["prices_before_minor"]["stock"] = 10000
        with self.assertRaisesRegex(ValueError, "reset"):
            evaluation_accounts(changed, specs, 1)


if __name__ == "__main__":
    unittest.main()
