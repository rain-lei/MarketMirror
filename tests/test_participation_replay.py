import copy
import unittest

from research.simulation.agents import AgentParameters
from research.simulation.participation_replay import simulate_path


def agent():
    return AgentParameters(name="aggressive", role="aggressive", initial_cash=100.0,
                           base_weight=0.3, max_weight=1.0, max_turnover=0.4,
                           risk_budget=0.04, market_sensitivity=0.7,
                           text_sensitivity=0.9, uncertainty_aversion=0.1,
                           confirmation_steps=1, rebalance_interval=1,
                           min_trade_weight=0.01)


def step():
    return {"trade_date": "2020-01-03", "signal_cutoff_date": "2020-01-01",
            "execution_reference_date": "2020-01-02", "estimated_volatility": 0.1,
            "market_signal": 0.5, "observed_return": -0.02}


def activity(cutoff_amount=100.0, reference_status="trading"):
    return {("000001", "2020-01-01"): {"amount_cny": cutoff_amount, "trading_status": "trading"},
            ("000001", "2020-01-02"): {"amount_cny": 1000000.0 if reference_status == "trading" else 0.0,
                                          "trading_status": reference_status}}


class ParticipationReplayTest(unittest.TestCase):
    def test_cap_uses_only_cutoff_amount_and_preserves_ledger(self):
        baseline = simulate_path([step()], [agent()], activity(), "000001", 0.001, True, None)
        capped = simulate_path([step()], [agent()], activity(), "000001", 0.001, True, 0.01)
        self.assertAlmostEqual(baseline["trace"][0]["fill_fraction"], 1.0)
        self.assertLess(capped["trace"][0]["fill_fraction"], 1.0)
        self.assertEqual(capped["trace"][0]["planning_cap_cny"], 1.0)
        self.assertLessEqual(capped["trace"][0]["gross_filled_cny"], 1.0)
        self.assertEqual(capped["binding_days"], 1)
        self.assertAlmostEqual(capped["trace"][0]["external_cash"]
                               + capped["trace"][0]["agents"]["aggressive"]["cash"]
                               + capped["trace"][0]["fee_pool"], 100.0)
        # The return date is deliberately absent from activity: no same-day turnover is read.
        changed_reference = activity()
        changed_reference[("000001", "2020-01-02")]["amount_cny"] *= 100
        same = simulate_path([step()], [agent()], changed_reference, "000001", 0.001, True, 0.01)
        self.assertEqual(capped["trace"][0]["fill_fraction"], same["trace"][0]["fill_fraction"])
        smaller_cutoff = simulate_path([step()], [agent()], activity(10.0), "000001", 0.001, True, 0.01)
        self.assertLess(smaller_cutoff["trace"][0]["fill_fraction"], capped["trace"][0]["fill_fraction"])

    def test_suspended_reference_blocks_fills_and_future_cutoff_is_rejected(self):
        suspended = simulate_path([step()], [agent()], activity(reference_status="suspended"),
                                  "000001", 0.001, True, 0.05)
        self.assertEqual(suspended["trace"][0]["gross_filled_cny"], 0.0)
        self.assertEqual(suspended["suspended_reference_days"], 1)
        bad = copy.deepcopy(step())
        bad["signal_cutoff_date"] = bad["trade_date"]
        with self.assertRaisesRegex(ValueError, "strictly prior"):
            simulate_path([bad], [agent()], activity(), "000001", 0.001, True, 0.05)


if __name__ == "__main__":
    unittest.main()
