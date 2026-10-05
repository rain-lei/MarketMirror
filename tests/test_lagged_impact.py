import copy
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from research.simulation.agents import AgentParameters
from research.simulation.lagged_impact import compare_scenarios, load_config, simulate_path
from research.simulation.observed_counterfactual import scenario_steps


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "research/configs/lagged_impact_2018.json"
REPLAY = ROOT / "research/configs/historical_replay_2018.json"


def example_steps():
    return [{"trade_date": f"2020-01-{index + 3:02d}",
             "execution_reference_date": f"2020-01-{index + 2:02d}",
             "signal_cutoff_date": f"2020-01-{index + 1:02d}",
             "observed_return": [0.01, -0.02, 0.015, 0.0, -0.01, 0.02][index],
             "market_signal": 0.05, "estimated_volatility": 0.02}
            for index in range(6)]


class LaggedImpactTest(unittest.TestCase):
    def setUp(self):
        self.steps = example_steps()
        self.agents = [replace(AgentParameters(**raw), initial_cash=1e9)
                       for raw in json.loads(REPLAY.read_text(encoding="utf-8"))["agents"]]
        self.activity = {("000001", f"2020-01-{day:02d}"):
                         {"amount_cny": 1e9, "trading_status": "trading"}
                         for day in range(1, 9)}

    def compare(self, steps=None, activity=None):
        return compare_scenarios(steps or self.steps, self.agents, activity or self.activity,
                                 "000001", 0.001, [0.01, 0.05], [0.01, 0.05],
                                 [0.0, 0.03], 0.03, -0.8, 0.6, 2, "2020-01-03")

    def test_capacity_impact_and_zero_coefficient_controls(self):
        result = self.compare()
        self.assertEqual(len(result["paths"]), 16)
        observed = 100 * math.prod(1 + step["observed_return"] for step in self.steps)
        for path in result["paths"]:
            if path["impact_coefficient"] == 0:
                self.assertAlmostEqual(path["final_price_index"], observed)
            for row in path["trace"]:
                self.assertLessEqual(row["gross_filled_cny"], row["planning_cap_cny"] + 1e-5)
                self.assertEqual(row["planning_cap_cny"], row["cutoff_observed_amount_cny"]
                                 * path["participation_rate"])
                self.assertEqual(row["assumed_impact_depth_cny"], row["cutoff_observed_amount_cny"]
                                 * path["impact_depth_fraction"])
                self.assertLessEqual(abs(row["price_impact"]), 0.03)
                self.assertAlmostEqual(row["external_cash"] + row["fee_pool"]
                                       + sum(agent["cash"] for agent in row["agents"].values()), 3e9)
        self.assertEqual(result["timing"]["first_signal_trade_date"], "2020-01-05")
        self.assertEqual(result["paired_effects"][0]["event_end_price_delta"], 0)

    def test_depth_fraction_changes_price_without_changing_capacity(self):
        result = self.compare()
        shallow = next(path for path in result["paths"] if
                       path["participation_rate"] == 0.01 and path["impact_depth_fraction"] == 0.01
                       and path["impact_coefficient"] == 0.03 and path["scenario_enabled"])
        deep = next(path for path in result["paths"] if
                    path["participation_rate"] == 0.01 and path["impact_depth_fraction"] == 0.05
                    and path["impact_coefficient"] == 0.03 and path["scenario_enabled"])
        self.assertNotEqual(shallow["trace"][0]["price_impact"], deep["trace"][0]["price_impact"])
        self.assertEqual(shallow["trace"][0]["planning_cap_cny"], deep["trace"][0]["planning_cap_cny"])

    def test_no_future_activity_or_return_leakage(self):
        baseline = self.compare()
        revised_steps = copy.deepcopy(self.steps)
        revised_steps[-1]["observed_return"] = -0.2
        revised_activity = copy.deepcopy(self.activity)
        revised_activity[("000001", "2020-01-06")]["amount_cny"] = 2e9
        changed = self.compare(revised_steps, revised_activity)
        for before, after in zip(baseline["paths"], changed["paths"], strict=True):
            self.assertEqual(before["trace"][:5], after["trace"][:5])
            self.assertNotEqual(before["trace"][-1], after["trace"][-1])

    def test_suspension_zero_amount_and_missing_activity(self):
        signals, _ = scenario_steps(self.steps, "2020-01-03", -0.8, 0.6, 2)
        activity = copy.deepcopy(self.activity)
        activity[("000001", "2020-01-01")] = {"amount_cny": 0.0, "trading_status": "suspended"}
        activity[("000001", "2020-01-03")] = {"amount_cny": 0.0, "trading_status": "suspended"}
        result = simulate_path(self.steps, signals, self.agents, activity, "000001", 0.001,
                               0.01, 0.01, 0.03, 0.03)
        self.assertEqual(result["trace"][0]["gross_filled_cny"], 0)
        self.assertEqual(result["trace"][0]["price_impact"], 0)
        self.assertEqual(result["trace"][1]["gross_filled_cny"], 0)
        missing = copy.deepcopy(activity)
        del missing[("000001", "2020-01-01")]
        with self.assertRaisesRegex(ValueError, "lacks cutoff"):
            simulate_path(self.steps, signals, self.agents, missing, "000001", 0.001,
                          0.01, 0.01, 0.03, 0.03)

    def test_config_rejects_unlabelled_or_invalid_assumptions(self):
        source = json.loads(CONFIG.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            for changed in ({**source, "data_kind": "observed"},
                            {**source, "participation_rates": [0.0]},
                            {**source, "impact_depth_fractions": [0.05, 0.01]},
                            {**source, "impact_coefficients": [0.01]},
                            {**source, "signal_sessions": True}):
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)
        self.assertEqual(load_config(CONFIG)["data_kind"], "observed_return_sensitivity")


if __name__ == "__main__":
    unittest.main()
