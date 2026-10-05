import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from research.simulation.agents import AgentParameters, AgentState, Observation, decide
from research.simulation.stress_market import run_stress, simulate
from research.data_pipeline.provenance import file_sha256


CONFIG = Path(__file__).resolve().parents[1] / "research/configs/synthetic_stress_v1.json"


class AgentStressTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_synthetic_pressure_ledger_risk_and_role_differences(self):
        result = simulate(self.config)
        self.assertEqual(result, simulate(self.config))
        self.assertEqual(len(result["trace"]), 16)
        self.assertEqual({v["role"] for v in result["summary"].values()}, {"aggressive", "conservative", "institutional"})
        self.assertLess(result["summary"]["institutional"]["trades"], result["summary"]["aggressive"]["trades"])
        self.assertGreater(result["summary"]["aggressive"]["cumulative_turnover"], result["summary"]["conservative"]["cumulative_turnover"])
        self.assertTrue(all(0 <= row["max_drawdown"] < 1 and row["risk_budget_breach_steps"] >= 0
                            for row in result["summary"].values()))
        for step in result["trace"]:
            agents = step["agents"]
            self.assertAlmostEqual(sum(a["cash"] for a in agents.values()) + step["external_cash"] + step["fee_pool"],
                                   result["initial_cash"], places=6)
            self.assertAlmostEqual(sum(a["shares"] for a in agents.values()) + step["external_shares"], 0, places=8)
            self.assertLessEqual(step["gross_requested_notional"] * step["liquidity_fill_fraction"],
                                 self.config["liquidity_notional"] + 1e-7)
            self.assertLessEqual(abs(step["price_impact"]), self.config["max_impact"] + 1e-12)
            self.assertTrue(all(a["cash"] >= 0 and a["shares"] >= 0 and a["closing_wealth"] > 0 for a in agents.values()))
            self.assertTrue(all(a["decision"]["action"] in {"buy", "sell", "hold"} for a in agents.values()))

    def test_large_fractional_share_ledger_tolerates_relative_float_roundoff(self):
        config = copy.deepcopy(self.config)
        config["liquidity_notional"] = 1e15
        for agent in config["agents"]:
            agent["initial_cash"] = 1e12
        source_steps = config["steps"][3:14]
        config["steps"] = [
            {**step, "evidence_id": f"large-ledger-{cycle}-{offset}"}
            for cycle in range(8) for offset, step in enumerate(source_steps)
        ]
        result = simulate(config)
        residuals = []
        for step in result["trace"]:
            agent_shares = sum(agent["shares"] for agent in step["agents"].values())
            residuals.append(abs(agent_shares + step["external_shares"]))
            self.assertTrue(math.isclose(agent_shares, -step["external_shares"],
                                         rel_tol=1e-12, abs_tol=1e-8))
        self.assertGreater(max(residuals), 1e-8)

    def test_text_ablation_changes_only_information_channel_at_initial_decision(self):
        with_text = simulate(self.config)
        no_text = simulate({**self.config, "use_text": False})
        first = 3
        for agent in ("aggressive", "institutional"):
            a = with_text["trace"][first]["agents"][agent]["decision"]
            b = no_text["trace"][first]["agents"][agent]["decision"]
            self.assertNotIn("synthetic-text:positive-shock-03", b["evidence"])
            self.assertGreater(a["target_weight"], b["target_weight"])
        conservative_first = with_text["trace"][first]["agents"]["conservative"]["decision"]
        self.assertIn("confirmation_wait", conservative_first["reason_codes"])
        conservative_confirmed = with_text["trace"][first + 1]["agents"]["conservative"]["decision"]
        conservative_control = no_text["trace"][first + 1]["agents"]["conservative"]["decision"]
        self.assertGreater(conservative_confirmed["target_weight"], conservative_control["target_weight"])
        self.assertNotEqual(with_text["final_price"], no_text["final_price"])
        self.assertTrue(all("text_signal" not in entry["reason_codes"] for step in no_text["trace"]
                            for entry in (a["decision"] for a in step["agents"].values())))

    def test_future_exogenous_change_cannot_change_earlier_state(self):
        baseline = simulate(self.config)
        changed = copy.deepcopy(self.config)
        changed["steps"][-1]["exogenous_return"] = -0.2
        changed["steps"][-1]["text_signal"] = -1
        later = simulate(changed)
        self.assertEqual(baseline["trace"][:-1], later["trace"][:-1])
        self.assertNotEqual(baseline["trace"][-1], later["trace"][-1])

    def test_zero_impact_price_is_exogenous_and_text_cannot_move_it(self):
        config = copy.deepcopy(self.config)
        config["impact_coefficient"] = 0.0
        text = simulate(config)
        control = simulate({**config, "use_text": False})
        expected = config["price_start"]
        for step, own, no_text in zip(config["steps"], text["trace"], control["trace"]):
            expected *= 1 + step["exogenous_return"]
            self.assertAlmostEqual(own["price_after"], expected)
            self.assertAlmostEqual(no_text["price_after"], expected)
            self.assertEqual(own["price_impact"], 0)
            self.assertEqual(no_text["price_impact"], 0)

    def test_zero_transaction_cost_keeps_fee_pool_zero(self):
        config = copy.deepcopy(self.config)
        config["transaction_cost_rate"] = 0.0
        result = simulate(config)
        self.assertTrue(all(step["fee_pool"] == 0 for step in result["trace"]))

    def test_risk_liquidation_overrides_confirmation_and_calendar(self):
        raw = copy.deepcopy(self.config["agents"][1])
        raw["max_turnover"] = 0.01
        raw["min_trade_weight"] = 0.001
        parameters = AgentParameters(**raw)
        state = AgentState(100, shares=8)
        obs = Observation(1, 100, 0.5, 0.5, 0.5, 0.0, "market", "text")
        decision = decide(parameters, state, obs)
        self.assertEqual(decision.action, "sell")
        self.assertIn("risk_liquidation_priority", decision.reason_codes)
        self.assertIn("risk_liquidation_overrides_turnover", decision.reason_codes)
        self.assertAlmostEqual(decision.target_weight, parameters.risk_budget / obs.estimated_volatility)

    def test_invalid_parameters_signals_and_config_fail(self):
        changed = copy.deepcopy(self.config)
        changed["agents"][0]["max_weight"] = float("nan")
        with self.assertRaises(ValueError):
            simulate(changed)
        changed = copy.deepcopy(self.config)
        changed["steps"][0]["exogenous_return"] = 0.8
        with self.assertRaises(ValueError):
            simulate(changed)
        changed = copy.deepcopy(self.config)
        changed["agents"][2]["role"] = "aggressive"
        with self.assertRaises(ValueError):
            simulate(changed)
        changed = copy.deepcopy(self.config)
        changed["data_kind"] = "observed"
        with self.assertRaises(ValueError):
            simulate(changed)

    def test_runner_artifact_hashes_and_input_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "first"
            result = run_stress(CONFIG, output)
            manifest = json.loads((output / "stress_manifest.json").read_text(encoding="utf-8"))
            for name, info in manifest["artifacts"].items():
                self.assertEqual(file_sha256(output / name), info["sha256"])
            self.assertIn("stress_no_text_results.json", manifest["artifacts"])
            self.assertEqual(result, run_stress(CONFIG, root / "second"))
            with self.assertRaisesRegex(ValueError, "new empty"):
                run_stress(CONFIG, output)


if __name__ == "__main__":
    unittest.main()
