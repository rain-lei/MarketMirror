import copy
import math
import unittest

from test_agent_decision_trace import fixture
from research.simulation.run_agent_decision_probes import run_one
from research.simulation.prototype_risk_metrics import derive_path_metrics, level_metrics, ratio
from research.simulation.audit_prototype_risk_diagnostics import reconstruct, equivalent


class PrototypeRiskMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        case = {"case_id": "high_risk", "signal": -0.6, "uncertainty": 0.05,
                "scope": "public", "volatility_floor": 0.30}
        cls.raw = run_one(fixture(), case, 7, True)
        cls.metrics = derive_path_metrics(cls.raw)

    def test_return_drawdown_and_volatility_include_initial_wealth(self):
        result = level_metrics([100, 110, 90, 99])
        self.assertAlmostEqual(result["total_return"], -0.01)
        self.assertAlmostEqual(result["max_close_drawdown"], 2 / 11)
        self.assertAlmostEqual(result["step_return_sample_stdev"], 31 / (110 * math.sqrt(3)))
        self.assertEqual(result["steps"], 3)
        for values in ([100, 110], [100, 0, 99], [100, float("nan"), 99]):
            with self.assertRaises(ValueError):
                level_metrics(values)

    def test_no_accepted_orders_is_unidentified_fraction_not_zero_fill_success(self):
        self.assertIsNone(ratio(0, 0))
        self.assertEqual(ratio(0, 100), 0)
        self.assertEqual(ratio(50, 100), 0.5)
        with self.assertRaises(ValueError):
            ratio(-1, 100)

    def test_risk_liquidation_requests_do_not_guarantee_instant_risk_compliance(self):
        for role in ("aggressive", "conservative", "institutional"):
            counts = self.metrics["roles"][role]["counts"]
            self.assertGreater(counts["risk_liquidation_requested_steps"], 0)
        self.assertGreater(sum(r["counts"]["post_close_risk_breach_steps"] for r in self.metrics["roles"].values()), 0)
        self.assertEqual(self.metrics["scope"]["account_closes"], 18 * 48)

    def test_archived_wealth_fees_and_quantity_damage_are_rejected(self):
        wrong = copy.deepcopy(self.raw)
        wrong["model_result"]["summary"]["accounts"]["aggressive_00"]["final_wealth_minor"] += 1
        with self.assertRaises(ValueError):
            derive_path_metrics(wrong)

        wrong = copy.deepcopy(self.raw)
        wrong["model_result"]["trace"][0]["portfolio_auction"]["fee_pool_minor"] += 1
        with self.assertRaises(ValueError):
            derive_path_metrics(wrong)

        wrong = copy.deepcopy(self.raw)
        order = wrong["model_result"]["trace"][0]["portfolio_auction"]["asset_calls"]["A"]["orders"][0]
        order["filled_quantity"] = order["accepted_quantity"] + 1
        with self.assertRaises(ValueError):
            derive_path_metrics(wrong)

    def test_missing_covariance_is_rejected_instead_of_becoming_zero_risk(self):
        wrong = copy.deepcopy(self.raw)
        wrong["model_result"]["trace"][0]["covariance"]["A"]["A"] = float("nan")
        with self.assertRaises(ValueError):
            derive_path_metrics(wrong)

    def test_metrics_ignore_unapplied_observed_returns_and_do_not_mutate_raw_path(self):
        before = copy.deepcopy(self.raw)
        self.assertEqual(derive_path_metrics(self.raw), self.metrics)
        self.assertEqual(self.raw, before)
        wrong = copy.deepcopy(self.raw)
        for day in wrong["model_result"]["trace"]:
            day["observed_return"] = -0.99
        self.assertEqual(derive_path_metrics(wrong), self.metrics)

    def test_separate_rational_auditor_matches_metrics_and_rejects_fabricated_no_breaches(self):
        expected = reconstruct(self.raw)
        equivalent(self.metrics, expected)
        wrong = copy.deepcopy(self.metrics)
        role = next(r for r in wrong["roles"] if wrong["roles"][r]["counts"]["post_close_risk_breach_steps"])
        wrong["roles"][role]["counts"]["post_close_risk_breach_steps"] = 0
        with self.assertRaises(ValueError):
            equivalent(wrong, expected)


if __name__ == "__main__":
    unittest.main()
