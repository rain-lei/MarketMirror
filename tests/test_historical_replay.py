import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from research.baselines.event_study import DailyObservation
from research.simulation.agents import AgentParameters
from research.simulation.historical_replay import load_config, prepare_steps, replay_path


CONFIG = Path(__file__).resolve().parents[1] / "research/configs/historical_replay_pilot_2020.json"


class HistoricalReplayTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG)
        self.agents = [AgentParameters(**raw) for raw in self.config["agents"]]
        self.rows = [DailyObservation(date(2020, 1, 1) + timedelta(days=i),
                                      (i % 9 - 4) * 0.002, (i % 7 - 3) * 0.003)
                     for i in range(70)]

    def test_signal_and_volatility_exclude_execution_and_realization_days(self):
        first = self.rows[25].trade_date.isoformat()
        last = self.rows[45].trade_date.isoformat()
        original = prepare_steps(self.rows, first, last, 5, 20)
        self.assertEqual(original[0]["signal_cutoff_date"], self.rows[23].trade_date.isoformat())
        self.assertEqual(original[0]["execution_reference_date"], self.rows[24].trade_date.isoformat())
        changed = list(self.rows)
        current = changed[30]
        changed[30] = DailyObservation(current.trade_date, -0.3, 0.2)
        shifted = prepare_steps(changed, first, last, 5, 20)
        self.assertEqual(original[:5], shifted[:5])
        self.assertEqual(original[5]["market_signal"], shifted[5]["market_signal"])
        self.assertEqual(original[6]["market_signal"], shifted[6]["market_signal"])
        self.assertNotEqual(original[7]["market_signal"], shifted[7]["market_signal"])

    def test_price_path_ledger_and_control_are_deterministic(self):
        steps = prepare_steps(self.rows, self.rows[25].trade_date.isoformat(),
                              self.rows[45].trade_date.isoformat(), 5, 20)
        active = replay_path(steps, self.agents, 0.001, True)
        control = replay_path(steps, self.agents, 0.001, False)
        self.assertEqual(active, replay_path(steps, self.agents, 0.001, True))
        self.assertEqual(active["price_index_end"], control["price_index_end"])
        for row in active["trace"]:
            self.assertAlmostEqual(sum(agent["cash"] for agent in row["agents"].values())
                                   + row["external_cash"] + row["fee_pool"],
                                   sum(agent.initial_cash for agent in self.agents), places=6)
            self.assertAlmostEqual(sum(agent["shares"] for agent in row["agents"].values())
                                   + row["external_shares"], 0, places=8)
            self.assertTrue(all(agent["cash"] >= 0 and agent["shares"] >= 0
                                for agent in row["agents"].values()))
            self.assertTrue(all("text_signal" not in agent["decision"]["reason_codes"]
                                for agent in row["agents"].values()))
        self.assertEqual([r["price_index_after"] for r in active["trace"]],
                         [r["price_index_after"] for r in control["trace"]])

    def test_future_realized_return_does_not_change_same_day_decisions(self):
        first, last = self.rows[25].trade_date.isoformat(), self.rows[45].trade_date.isoformat()
        baseline = replay_path(prepare_steps(self.rows, first, last, 5, 20), self.agents, 0.001, True)
        changed = list(self.rows)
        changed[30] = DailyObservation(changed[30].trade_date, -0.3, 0.2)
        perturbed = replay_path(prepare_steps(changed, first, last, 5, 20), self.agents, 0.001, True)
        self.assertEqual(baseline["trace"][:5], perturbed["trace"][:5])
        self.assertEqual({name: row["decision"] for name, row in baseline["trace"][5]["agents"].items()},
                         {name: row["decision"] for name, row in perturbed["trace"][5]["agents"].items()})
        self.assertNotEqual(baseline["trace"][5]["price_index_after"],
                            perturbed["trace"][5]["price_index_after"])

    def test_invalid_history_and_returns_fail(self):
        with self.assertRaisesRegex(ValueError, "strictly lagged"):
            prepare_steps(self.rows, self.rows[20].trade_date.isoformat(),
                          self.rows[30].trade_date.isoformat(), 5, 20)
        changed = list(self.rows)
        changed[30] = DailyObservation(changed[30].trade_date, -1.0, 0.0)
        with self.assertRaisesRegex(ValueError, "strictly above"):
            prepare_steps(changed, self.rows[25].trade_date.isoformat(),
                          self.rows[45].trade_date.isoformat(), 5, 20)

    def test_config_requires_observed_data_and_valid_lookbacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for key, value in (("data_kind", "synthetic"), ("momentum_sessions", 21),
                               ("stock_codes", ["000001", "000001"])):
                changed = copy.deepcopy(self.config)
                changed[key] = value
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
