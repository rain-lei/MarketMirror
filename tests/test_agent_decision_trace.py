import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from research.simulation.agent_decision_trace import explain_decision, explain_path, audit_explanations
from research.simulation.agents import AgentParameters
from research.simulation.feedback_auction import NEUTRAL
from research.simulation.portfolio_auction import PortfolioAccount
from research.simulation.portfolio_market import initial_portfolio, portfolio_decision, simulate_portfolio
from research.simulation.run_agent_decision_probes import make_inputs, run_one, summarize_pair

ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = json.loads((ROOT / "research/configs/synthetic_stress_v1.json").read_text(encoding="utf-8"))["agents"]


def fixture():
    return {
        "agent_parameters": copy.deepcopy(PARAMETERS), "sessions": 18, "assets": ["A", "B", "C"],
        "pulse_start": 3, "pulse_stop": 9,
        "core": {"scenario_id": "decision_probe", "feedback_mode": "endogenous",
                 "profile_mode": "homogeneous", "order_mode": "hashed", "inventory": [500, 500, 500, 500]},
        "background": {"case_id": "active_probe", "mode": "active", "participants": 12,
                       "initial_cash": 50000, "initial_shares": 500, "target_range_lots": 4,
                       "max_order_lots": 4, "urgency_bps": 25},
        "feedback": {"momentum_sessions": 5, "volatility_sessions": 20},
        "portfolio_case": {"case_id": "shared_institution35", "cash_mode": "shared", "institutional_asset_cap": 0.35},
        "venue": {"price_start_minor": 10000, "lot_size": 100, "tick_minor": 1, "fee_bps": 10,
                  "price_band_bps": 1000, "quote_spread_bps": 25, "quote_response_bps": 200},
    }


POSITIVE = {"case_id": "positive_public", "signal": 0.6, "uncertainty": 0.05,
            "scope": "public", "volatility_floor": 0.01}
NEUTRAL_CASE = {**POSITIVE, "case_id": "neutral", "signal": 0, "uncertainty": 0}


def decision_inputs():
    prices = dict.fromkeys(["A", "B", "C"], 10000)
    account = PortfolioAccount({"shared": 30000000}, dict.fromkeys(prices, 500), dict.fromkeys(prices, 500))
    obs = {a: {"market_signal": 0.1, "text_signal": 0.6, "text_uncertainty": 0.3,
               "text_evidence": "synthetic:test:" + a} for a in prices}
    cov = {a: {b: 0.0004 if a == b else 0 for b in prices} for a in prices}
    case = fixture()["portfolio_case"]
    return account, prices, obs, cov, case


class AgentDecisionTraceTest(unittest.TestCase):
    def test_exact_rule_result_and_no_input_or_memory_mutation(self):
        agent = AgentParameters(**PARAMETERS[0])
        account, prices, obs, cov, case = decision_inputs()
        memory = {"sign": 0, "streak": 0}
        before = copy.deepcopy((account, prices, obs, cov, case, memory))
        expected_memory = copy.deepcopy(memory)
        expected = portfolio_decision(agent, NEUTRAL, account, prices, obs, cov, case,
                                      expected_memory, 0, True)
        trace = explain_decision(agent, NEUTRAL, account, prices, obs, cov, case, memory, 0, True, expected)
        self.assertEqual(trace["decision"], expected)
        self.assertEqual(trace["explanation"]["memory_after"], expected_memory)
        self.assertEqual((account, prices, obs, cov, case, memory), before)
        self.assertEqual(trace, explain_decision(agent, NEUTRAL, account, prices, obs, cov, case, memory, 0, True))

    def test_disabled_text_removes_signal_and_uncertainty_and_active_evidence(self):
        agent = AgentParameters(**PARAMETERS[0])
        account, prices, obs, cov, case = decision_inputs()
        memory = {"sign": 0, "streak": 0}
        disabled = explain_decision(agent, NEUTRAL, account, prices, obs, cov, case, memory, 0, False)
        poisoned = copy.deepcopy(obs)
        for row in poisoned.values():
            row.update(text_signal=-0.9, text_uncertainty=1.0, text_evidence="different-unused-text")
        again = explain_decision(agent, NEUTRAL, account, prices, poisoned, cov, case, memory, 0, False)
        self.assertEqual(disabled["decision"], again["decision"])
        for term in disabled["explanation"]["belief_terms"].values():
            self.assertEqual((term["text_contribution"], term["uncertainty_contribution"]), (0, 0))
            self.assertIsNone(term["text_evidence_used"])

    def test_liquidation_overrides_wait_and_turnover_with_same_state(self):
        agent = replace(AgentParameters(**PARAMETERS[2]), confirmation_steps=10,
                        max_turnover=0.01, min_trade_weight=0.005)
        account, prices, obs, cov, case = decision_inputs()
        account = PortfolioAccount({"shared": 1000}, {"A": 8000, "B": 0, "C": 0},
                                   {"A": 8000, "B": 0, "C": 0})
        trace = explain_decision(agent, NEUTRAL, account, prices, obs, cov, case,
                                 {"sign": 0, "streak": 0}, 1, True)
        gates, decision = trace["explanation"]["gates"], trace["decision"]
        self.assertTrue(gates["confirmation_wait_condition"])
        self.assertTrue(gates["rebalance_wait_condition"])
        self.assertTrue(gates["risk_liquidation_priority"])
        self.assertFalse(gates["waiting_applied"])
        self.assertFalse(gates["turnover_cap_applied"])
        self.assertGreater(-decision["order_weight_changes"]["A"], agent.max_turnover)
        self.assertLessEqual(max(decision["desired_weights"].values()), case["institutional_asset_cap"])

    def test_institutional_cap_is_explained_in_same_parameter_counterfactual(self):
        agent = replace(AgentParameters(**PARAMETERS[2]), risk_budget=0.1, max_weight=1.0)
        account, prices, obs, cov, case = decision_inputs()
        account = PortfolioAccount({"shared": 10000000}, {"A": 5000, "B": 0, "C": 0},
                                   {"A": 5000, "B": 0, "C": 0})
        inst = explain_decision(agent, NEUTRAL, account, prices, obs, cov, case, {"sign": 0, "streak": 0}, 0, True)
        other = explain_decision(replace(agent, role="aggressive"), NEUTRAL, account, prices, obs, cov,
                                 case, {"sign": 0, "streak": 0}, 0, True)
        self.assertTrue(inst["decision"]["risk_liquidation"])
        self.assertFalse(other["decision"]["risk_liquidation"])
        self.assertEqual(inst["decision"]["asset_weight_cap"], 0.35)
        self.assertEqual(other["decision"]["asset_weight_cap"], 1.0)

    def test_future_outcomes_do_not_change_entire_simulated_path(self):
        plan = fixture()
        agents = [AgentParameters(**p) for p in plan["agent_parameters"]]
        core, background = {**plan["core"], "seed": 7}, {**plan["background"], "seed": 7}
        feedback = {**plan["feedback"], "volatility_floor": 0.01}
        inputs = make_inputs(plan, POSITIVE)
        original = simulate_portfolio(inputs, agents, core, background, plan["venue"], feedback, plan["portfolio_case"], True)
        poisoned = copy.deepcopy(inputs)
        for steps in poisoned.values():
            for step in steps:
                step.update(observed_return=9.0, future_target_price=1e12, evaluation_label="future-bad")
        repeated = simulate_portfolio(poisoned, agents, core, background, plan["venue"], feedback, plan["portfolio_case"], True)
        self.assertEqual(original, repeated)

    def test_neutral_control_and_requested_vs_actual_trade_separation(self):
        plan = fixture()
        no_text = run_one(plan, NEUTRAL_CASE, 7, False)
        with_text = run_one(plan, NEUTRAL_CASE, 7, True)
        pair = summarize_pair(no_text, with_text)
        self.assertFalse(pair["behavior_changed"])
        self.assertTrue(any(
            e["requested_quantity"] != e["filled_quantity"]
            for row in with_text["decision_explanations"] for e in row["execution"].values()))
        self.assertEqual(with_text["audit"]["decisions"], 18 * 12)

    def test_audit_rejects_wrong_terms_evidence_missing_rows_and_false_fills(self):
        run = run_one(fixture(), POSITIVE, 7, True)
        rows, result = run["decision_explanations"], run["model_result"]
        for damage in ("term", "evidence", "fill", "memory", "wait", "desired", "request",
                       "missing", "duplicate"):
            wrong = copy.deepcopy(rows)
            if damage == "term":
                wrong[0]["belief_terms"]["A"]["market_contribution"] += 0.1
            elif damage == "evidence":
                wrong[0]["belief_terms"]["A"]["text_evidence_used"] = "wrong-source"
            elif damage == "fill":
                wrong[0]["execution"]["A"]["filled_quantity"] += 100
            elif damage == "memory":
                wrong[0]["memory_after"]["streak"] += 1
            elif damage == "wait":
                wrong[0]["gates"]["waiting_applied"] = not wrong[0]["gates"]["waiting_applied"]
            elif damage == "desired":
                wrong[0]["desired_weights"]["A"] += 0.1
            elif damage == "request":
                wrong[0]["requested_orders"]["A"]["whole_lot_quantity"] += 100
            elif damage == "missing":
                wrong.pop()
            else:
                wrong.append(copy.deepcopy(wrong[0]))
            with self.subTest(damage=damage), self.assertRaises(ValueError):
                audit_explanations(wrong, result)

    def test_actor_specific_receipts_are_not_silently_explained_as_public_information(self):
        plan = fixture()
        run = run_one(plan, POSITIVE, 7, True)
        core, bg = {**plan["core"], "seed": 7}, {**plan["background"], "seed": 7}
        agents = [AgentParameters(**p) for p in plan["agent_parameters"]]
        _, accounts, _, _ = initial_portfolio(agents, core, bg, plan["assets"], plan["portfolio_case"], plan["venue"])
        result = copy.deepcopy(run["model_result"])
        result["trace"][0]["issuer_information_receipts"] = {"not-public": {}}
        with self.assertRaisesRegex(ValueError, "actor-specific"):
            explain_path(result, accounts, plan["portfolio_case"], plan["venue"], True)


if __name__ == "__main__":
    unittest.main()
