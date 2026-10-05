import copy
import json
import tempfile
import unittest
from pathlib import Path

from test_assistant_review import fixture
from test_semantic_historical_replay import ObservedFixtureSDK
from test_semantic_auction_experiment import PARAMETERS, VENUE, INVENTORY, sample_steps
from research.data_pipeline.fetch_baostock import fetch_baostock
from research.data_pipeline.market_data import import_market
from research.semantic.assistant_review import finalize_review
from research.semantic.compare_holdout import compare_holdout
from research.semantic.agent_signal_adapter import run_adapter
from research.simulation.agents import AgentParameters
from research.simulation.feedback_auction import price_observation, simulate_feedback, arrival_order, validate_scenario
from research.simulation.feedback_experiment import run_experiment, load_summary, load_config
from research.simulation.audit_feedback import check_feedback, audit_directory, load_audit
from research.simulation.semantic_memory_sensitivity import canonical_hash
from research.simulation.semantic_auction_experiment import simulate_agents

ROOT = Path(__file__).parents[1]
CONFIG = json.loads((ROOT / "research/configs/semantic_feedback_all126_2020.json").read_text(encoding="utf-8"))
FEEDBACK = CONFIG["feedback_parameters"]


def make_fixture(root):
    _, _, decisions = fixture(root)
    finalize_review(root / "pack", decisions, root / "review")
    compare_holdout(root / "pack", None, None, root / "raw", root / "normalized", root / "keyword", root / "scored", ai_review_dir=root / "review")
    run_adapter(root / "pack", root / "normalized/model_predictions.jsonl", root / "scored", root / "signals")
    fetch_baostock(["sz.000001", "sz.000002"], "sh.000300", "2020-06-25", "2020-07-10", root / "download", sdk=ObservedFixtureSDK())
    import_market(root / "download/market_import.json", root / "prepared")
    base = {"run_id": "fixture", "data_kind": "observed", "market_manifest": "prepared/market_manifest.json",
            "download_manifest": "download/download_manifest.json", "signal_directory": "signals", "selection_rule": "all_signal_stocks",
            "stock_codes": ["000001", "000002"], "start_date": "2020-07-03", "end_date": "2020-07-10", "momentum_sessions": 2,
            "volatility_sessions": 2, "transaction_cost_rate": 0.001, "agents": PARAMETERS}
    (root / "base.json").write_text(json.dumps(base), encoding="utf-8")
    parent = {"run_id": "auction_fixture", "base_config": "base.json", "initial_inventory_per_role": INVENTORY,
              "memory_scenario": {"scenario_id": "cumulative_zero", "memory_mode": "cumulative", "memory_sessions": None, "lag_days": 0},
              "venue": {key: value for key, value in VENUE.items() if key != "quote_response_bps"}, "quote_response_bps": [0, 200, 500]}
    (root / "auction.json").write_text(json.dumps(parent), encoding="utf-8")
    cfg = {**CONFIG, "auction_config": "auction.json", "run_id": "feedback_fixture"}
    path = root / "feedback.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


class FeedbackAuctionTest(unittest.TestCase):
    def test_causal_price_window_and_independent_variance(self):
        settings = {"momentum_sessions": 2, "volatility_sessions": 3, "volatility_floor": 0.01}
        history = [{"trade_date": "2020-07-01", "price_before_minor": 100, "price_after_minor": 110},
                   {"trade_date": "2020-07-02", "price_before_minor": 110, "price_after_minor": 99},
                   {"trade_date": "2020-07-03", "price_before_minor": 99, "price_after_minor": 198}]
        result = price_observation(history, "2020-07-02", settings)
        self.assertAlmostEqual(result["estimated_volatility"], 0.1)
        self.assertAlmostEqual(result["market_signal"], 0.0)
        self.assertEqual(result["visible_closes"], 2)
        self.assertEqual(result["warmup_missing"], 1)
        record = {"signal_cutoff_date": "2020-07-02", "feedback": result}
        check_feedback(record, history, settings)
        for key in ("market_signal", "estimated_volatility", "visible_closes", "warmup_missing", "latest_close_date", "window_return_sha256"):
            altered = copy.deepcopy(record)
            altered["feedback"][key] = "tampered" if isinstance(result[key], (str, type(None))) else result[key] + 0.1
            with self.assertRaises((ValueError, TypeError)):
                check_feedback(altered, history, settings)
        changed = copy.deepcopy(history)
        changed[2]["price_after_minor"] = 1
        self.assertEqual(result, price_observation(changed, "2020-07-02", settings))
        flat = price_observation([], "2020-07-01", settings)
        self.assertEqual(flat["market_signal"], 0)
        self.assertEqual(flat["estimated_volatility"], 0.01)

    def test_conditioned_engine_exact_parity(self):
        agents = [AgentParameters(**a) for a in PARAMETERS]
        old = simulate_agents(sample_steps(), agents, INVENTORY, VENUE, True)
        result = simulate_feedback(sample_steps(), agents, CONFIG["scenarios"][0], VENUE, FEEDBACK, True)
        for field, value in old["summary"].items():
            if field != "trace_sha256":
                self.assertEqual(result["summary"][field], value)
        for a, b in zip(old["trace"], result["trace"], strict=True):
            for field in ("auction", "decisions", "quotes"):
                self.assertEqual(a[field], b[field])

    def test_endogenous_feedback_ignores_observed_inputs_and_ablates_all_text(self):
        agents = [AgentParameters(**a) for a in PARAMETERS]
        steps = sample_steps()
        scenario = CONFIG["scenarios"][4]
        result = simulate_feedback(steps, agents, scenario, VENUE, FEEDBACK, True)
        modified = copy.deepcopy(steps)
        for step in modified:
            step.update(observed_return=-0.9, market_signal=-0.9, estimated_volatility=0.7)
        self.assertEqual(result, simulate_feedback(modified, agents, scenario, VENUE, FEEDBACK, True))
        control = simulate_feedback(steps, agents, scenario, VENUE, FEEDBACK, False)
        for step in modified:
            step.update(text_signal=-0.95, text_uncertainty=0.99, text_evidence="replacement")
        self.assertEqual(control, simulate_feedback(modified, agents, scenario, VENUE, FEEDBACK, False))
        tail = copy.deepcopy(steps)
        tail[-1].update(text_signal=-0.9, text_uncertainty=0.9)
        changed = simulate_feedback(tail, agents, scenario, VENUE, FEEDBACK, True)
        self.assertEqual(result["trace"][:-1], changed["trace"][:-1])
        for index, day in enumerate(result["trace"]):
            history = [{"trade_date": p["trade_date"], "price_before_minor": p["auction"]["price_before_minor"],
                        "price_after_minor": p["auction"]["price_after_minor"]} for p in result["trace"][:index]]
            check_feedback(day, history, FEEDBACK)
            if day["feedback"]["latest_close_date"]:
                self.assertLessEqual(day["feedback"]["latest_close_date"], day["signal_cutoff_date"])
        self.assertTrue(any(day["feedback"]["market_signal"] != 0 for day in result["trace"]))
        self.assertGreater(result["summary"]["matched_volume"], 0)

    def test_cohort_arrival_inventory_and_zero_quote_constraints(self):
        names = ["a", "b", "c", "d", "e", "f"]
        self.assertEqual(arrival_order(names, "forward", 0, 0), names)
        self.assertEqual(arrival_order(names, "reverse", 0, 0), list(reversed(names)))
        hashed = arrival_order(names, "hashed", 7, 3)
        self.assertEqual(hashed, arrival_order(list(reversed(names)), "hashed", 7, 3))
        self.assertNotEqual(hashed, arrival_order(names, "hashed", 19, 3))
        agents = [AgentParameters(**a) for a in PARAMETERS]
        for scenario in CONFIG["scenarios"]:
            result = simulate_feedback(sample_steps(), agents, scenario, {**VENUE, "quote_response_bps": 0}, FEEDBACK, True)
            self.assertEqual(result["summary"]["price_change_sessions"], 0)
            self.assertEqual(result["summary"]["initial_shares"], 8100)
            self.assertEqual(sum(a["initial_wealth_minor"] for a in result["summary"]["agent_summary"].values()), 201000000)
            self.assertEqual(result["summary"]["trace_sha256"], canonical_hash(result["trace"]))
            self.assertEqual(result["trace"][1]["auction"]["matched_volume"], 0)
        invalid = copy.deepcopy(CONFIG["scenarios"][0])
        invalid["seed"] = 3
        with self.assertRaisesRegex(ValueError, "seed zero"):
            validate_scenario(invalid, 100)

    def test_verified_full_chain_rerun_feedback_audit_and_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = make_fixture(root)
            result = run_experiment(config, root / "output")
            self.assertEqual(result, run_experiment(config, root / "rerun"))
            self.assertEqual(result, load_summary(root / "output"))
            self.assertEqual(result["paths"], 56)
            self.assertEqual(result["ledger_rows"], 336)
            self.assertEqual(result["conditioned_baseline_parity_paths"], 8)
            manifests = [json.loads((root / p / "feedback_manifest.json").read_text(encoding="utf-8")) for p in ("output", "rerun")]
            self.assertEqual(manifests[0]["artifacts"], manifests[1]["artifacts"])
            audit = audit_directory(root / "output", config, root / "audit")
            self.assertEqual(audit, load_audit(root / "output", root / "audit"))
            self.assertEqual(audit["endogenous_input_rows"], 288)
            self.assertEqual(audit["halted_sessions"], 28)
            artifact = root / "output/feedback_summary.json"
            original = artifact.read_bytes()
            artifact.write_bytes(original + b"\n")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                load_summary(root / "output")
            artifact.write_bytes(original)
            cfg = json.loads(config.read_text(encoding="utf-8"))
            cfg["scenarios"][-1]["inventory"] = [700, 700, 700, 700]
            config.write_text(json.dumps(cfg), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "aggregate share supply"):
                load_config(config)
            with self.assertRaisesRegex(ValueError, "source changed"):
                load_summary(root / "output")


if __name__ == "__main__":
    unittest.main()
