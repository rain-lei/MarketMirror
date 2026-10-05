import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from research.baselines.run_experiments import load_experiment as load_event_experiment, visibility_anchor
from research.simulation.agents import AgentParameters
from research.simulation.observed_counterfactual import (compare_scenarios, load_config,
                                                         scenario_steps)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "research/configs/observed_counterfactual_2018.json"
AGENTS = ROOT / "research/configs/historical_replay_2018.json"


def example_steps():
    return [{"trade_date": f"2020-01-{index + 3:02d}",
             "execution_reference_date": f"2020-01-{index + 2:02d}",
             "signal_cutoff_date": f"2020-01-{index + 1:02d}",
             "observed_return": [0.01, -0.02, 0.015, 0.0, -0.01, 0.02][index],
             "market_signal": 0.05, "estimated_volatility": 0.02}
            for index in range(6)]


class ObservedCounterfactualTest(unittest.TestCase):
    def setUp(self):
        self.steps = example_steps()
        self.agents = [AgentParameters(**raw) for raw in json.loads(AGENTS.read_text(encoding="utf-8"))["agents"]]

    def compare(self, steps=None):
        return compare_scenarios(steps or self.steps, self.agents, 0.001, 300000.0, 0.03,
                                 [0.0, 0.01], 0.9, 0.1, 2, "2020-01-03")

    def test_visibility_gate_and_temporal_order(self):
        signal, timing = scenario_steps(self.steps, "2020-01-03", 0.9, 0.1, 2)
        self.assertEqual(timing["first_signal_trade_date"], "2020-01-05")
        self.assertEqual([row["text_signal"] for row in signal], [0, 0, 0.9, 0.9, 0, 0])
        with self.assertRaisesRegex(ValueError, "no complete"):
            scenario_steps(self.steps, "2020-01-06", 0.9, 0.1, 3)
        invalid = copy.deepcopy(self.steps)
        invalid[1]["signal_cutoff_date"] = invalid[1]["execution_reference_date"]
        with self.assertRaisesRegex(ValueError, "order"):
            scenario_steps(invalid, "2020-01-03", 0.9, 0.1, 2)

    def test_wuhan_date_only_and_effective_time_proxy_have_distinct_agent_signal_days(self):
        event_config = load_event_experiment(ROOT / "research/configs/observed_pilot_2020.json")
        events = {event["event_id"]: event for event in event_config["events"]}
        date_only = events["wuhan_date_only_conservative"]
        effective_proxy = events["wuhan_effective_time_upper_bound"]
        timestamp_config = load_event_experiment(ROOT / "research/configs/observed_pilot_2020_xinhua_timestamp.json")
        page_timestamp = timestamp_config["events"][0]
        self.assertEqual(str(visibility_anchor(date_only)[0]), "2020-01-24")
        self.assertEqual(str(visibility_anchor(effective_proxy)[0]), "2020-01-23")
        self.assertEqual(str(visibility_anchor(page_timestamp)[0]), "2020-01-23")
        self.assertIn("03:15:55", page_timestamp["visible_at"])

        steps = [
            {"trade_date": trade, "signal_cutoff_date": cutoff, "execution_reference_date": reference,
             "observed_return": 0.0, "market_signal": 0.0, "estimated_volatility": 0.02}
            for trade, cutoff, reference in (
                ("2020-01-23", "2020-01-21", "2020-01-22"),
                ("2020-02-03", "2020-01-22", "2020-01-23"),
                ("2020-02-04", "2020-01-23", "2020-02-03"),
                ("2020-02-05", "2020-02-03", "2020-02-04"),
                ("2020-02-06", "2020-02-04", "2020-02-05"),
            )
        ]
        _, date_timing = scenario_steps(steps, str(visibility_anchor(date_only)[0]), -0.8, 0.6, 2)
        _, proxy_timing = scenario_steps(steps, str(visibility_anchor(effective_proxy)[0]), -0.8, 0.6, 2)
        _, page_timing = scenario_steps(steps, str(visibility_anchor(page_timestamp)[0]), -0.8, 0.6, 2)
        self.assertEqual(date_timing["first_signal_trade_date"], "2020-02-05")
        self.assertEqual(proxy_timing["first_signal_trade_date"], "2020-02-04")
        self.assertEqual(page_timing["first_signal_trade_date"], "2020-02-04")

    def test_zero_impact_and_scenario_ablation(self):
        result = self.compare()
        self.assertEqual(len(result["paths"]), 4)
        no_signal, scenario, impacted_control, impacted_scenario = result["paths"]
        observed = 100 * math.prod(1 + step["observed_return"] for step in self.steps)
        self.assertAlmostEqual(result["observed_return_only_price_index"], observed)
        self.assertAlmostEqual(no_signal["final_price_index"], observed)
        self.assertAlmostEqual(scenario["final_price_index"], observed)
        self.assertAlmostEqual(result["paired_effects"][0]["event_end_price_delta"], 0)
        self.assertAlmostEqual(result["paired_effects"][0]["terminal_price_delta"], 0)
        self.assertTrue(all(row["price_impact"] == 0 for row in scenario["trace"]))
        self.assertEqual(no_signal["trace"][:2], scenario["trace"][:2])
        self.assertEqual(impacted_control["trace"][:2], impacted_scenario["trace"][:2])
        decision = scenario["trace"][2]["agents"]["aggressive"]["decision"]
        self.assertIn("text_signal", decision["reason_codes"])
        self.assertNotEqual(no_signal["trace"][2], scenario["trace"][2])
        self.assertTrue(all(row["signal_cutoff_date"] < row["execution_reference_date"] < row["trade_date"]
                            for row in impacted_scenario["trace"]))

    def test_future_return_does_not_change_earlier_state(self):
        baseline = self.compare()
        revised = copy.deepcopy(self.steps)
        revised[-1]["observed_return"] = -0.2
        changed = self.compare(revised)
        for before, after in zip(baseline["paths"], changed["paths"], strict=True):
            self.assertEqual(before["trace"][:-1], after["trace"][:-1])
            self.assertNotEqual(before["final_price_index"], after["final_price_index"])

    def test_config_rejects_unlabelled_or_invalid_assumptions(self):
        source = json.loads(CONFIG.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            for changed in ({**source, "data_kind": "observed"},
                            {**source, "impact_coefficients": [0.01]},
                            {**source, "impact_coefficients": [0.0, 0.0]},
                            {**source, "signal_sessions": True},
                            {**source, "scenario_signal": 0.0, "scenario_uncertainty": 0.0}):
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)
        self.assertEqual(load_config(CONFIG)["data_kind"], "observed_return_counterfactual")


if __name__ == "__main__":
    unittest.main()
