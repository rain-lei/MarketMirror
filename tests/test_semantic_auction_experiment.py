import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from test_assistant_review import fixture
from test_semantic_historical_replay import ObservedFixtureSDK
from research.data_pipeline.fetch_baostock import fetch_baostock
from research.data_pipeline.market_data import import_market
from research.semantic.assistant_review import finalize_review
from research.semantic.compare_holdout import compare_holdout
from research.semantic.agent_signal_adapter import run_adapter
from research.simulation.agents import AgentParameters
from research.simulation.semantic_auction_experiment import simulate_agents, run_experiment, load_auction_summary
from research.simulation.audit_auction import audit_directory, load_audit


ROOT = Path(__file__).parents[1]
PARAMETERS = json.loads((ROOT / "research/configs/synthetic_stress_v1.json").read_text(encoding="utf-8"))["agents"]
VENUE = {"price_start_minor": 10000, "lot_size": 100, "tick_minor": 1,
         "fee_bps": 10, "price_band_bps": 1000, "quote_spread_bps": 10, "quote_response_bps": 200}
INVENTORY = [0, 300, 800, 1600]


def sample_steps():
    return [{"trade_date": f"2020-07-{day:02d}", "signal_cutoff_date": f"2020-07-{day-2:02d}",
             "execution_reference_date": f"2020-07-{day-1:02d}", "market_signal": 0.25,
             "estimated_volatility": 0.02, "text_signal": 0.8, "text_uncertainty": 0.1,
             "text_evidence": "semantic:fixture", "observed_return": 0.01,
             "execution_available": day != 4} for day in (3, 4, 5, 6)]


class SemanticAuctionExperimentTest(unittest.TestCase):
    def test_observed_future_returns_do_not_set_prices_and_text_ablation_is_complete(self):
        agents = [AgentParameters(**row) for row in PARAMETERS]
        steps = sample_steps()
        active = simulate_agents(steps, agents, INVENTORY, VENUE, True)
        modified = copy.deepcopy(steps)
        for step in modified:
            step["observed_return"] = -0.4
        self.assertEqual(active, simulate_agents(modified, agents, INVENTORY, VENUE, True))
        control = simulate_agents(steps, agents, INVENTORY, VENUE, False)
        for step in modified:
            step.update(text_signal=-0.9, text_uncertainty=0.9, text_evidence="different")
        self.assertEqual(control, simulate_agents(modified, agents, INVENTORY, VENUE, False))
        self.assertGreater(active["summary"]["matched_volume"], 0)
        self.assertNotEqual(active["summary"]["final_price_minor"], control["summary"]["final_price_minor"])

    def test_finite_assets_halt_price_without_trade_and_zero_response_control(self):
        agents = [AgentParameters(**row) for row in PARAMETERS]
        path = simulate_agents(sample_steps(), agents, INVENTORY, VENUE, True)
        for day in path["trace"]:
            auction = day["auction"]
            self.assertEqual(auction["cash_total_minor"] + auction["fee_pool_minor"], path["summary"]["initial_cash_minor"])
            self.assertEqual(sum(row["shares"] for row in auction["accounts"].values()), path["summary"]["initial_shares"])
            if not auction["matched_volume"]:
                self.assertEqual(auction["price_before_minor"], auction["price_after_minor"])
            for order in auction["orders"]:
                self.assertLessEqual(order["filled_quantity"], order["accepted_quantity"])
                self.assertEqual(order["filled_quantity"] % VENUE["lot_size"], 0)
            self.assertTrue(all(row["cash_minor"] >= 0 and 0 <= row["sellable_shares"] <= row["shares"]
                                for row in auction["accounts"].values()))
        self.assertEqual(path["trace"][1]["auction"]["matched_volume"], 0)
        self.assertTrue(all(row["fee_minor"] == 0 for row in path["trace"][1]["auction"]["orders"]))
        zero = simulate_agents(sample_steps(), agents, INVENTORY, {**VENUE, "quote_response_bps": 0}, True)
        self.assertEqual(zero["summary"]["price_change_sessions"], 0)
        self.assertGreater(zero["summary"]["unfilled_quantity"], 0)

    def test_full_verified_source_chain_lossless_ledger_reexecution_and_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, decisions = fixture(root)
            finalize_review(root / "pack", decisions, root / "review")
            compare_holdout(root / "pack", None, None, root / "raw", root / "normalized", root / "keyword",
                            root / "scored", ai_review_dir=root / "review")
            run_adapter(root / "pack", root / "normalized/model_predictions.jsonl", root / "scored", root / "signals")
            fetch_baostock(["sz.000001", "sz.000002"], "sh.000300", "2020-06-25", "2020-07-10",
                           root / "download", sdk=ObservedFixtureSDK())
            import_market(root / "download/market_import.json", root / "prepared")
            base = {"run_id": "fixture", "data_kind": "observed", "market_manifest": "prepared/market_manifest.json",
                    "download_manifest": "download/download_manifest.json", "signal_directory": "signals",
                    "selection_rule": "all_signal_stocks", "stock_codes": ["000001", "000002"],
                    "start_date": "2020-07-03", "end_date": "2020-07-10", "momentum_sessions": 2,
                    "volatility_sessions": 2, "transaction_cost_rate": 0.001, "agents": PARAMETERS}
            (root / "base.json").write_text(json.dumps(base), encoding="utf-8")
            cfg = {"run_id": "auction_fixture", "base_config": "base.json", "initial_inventory_per_role": INVENTORY,
                   "memory_scenario": {"scenario_id": "cumulative_zero", "memory_mode": "cumulative", "memory_sessions": None, "lag_days": 0},
                   "venue": {key: value for key, value in VENUE.items() if key != "quote_response_bps"},
                   "quote_response_bps": [0, 200, 500]}
            config_path = root / "auction.json"
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            result = run_experiment(config_path, root / "output")
            repeated = run_experiment(config_path, root / "rerun")
            self.assertEqual(result, repeated)
            self.assertEqual(result, load_auction_summary(root / "output"))
            self.assertEqual(result["paths"], 12)
            self.assertEqual(result["ledger_rows"], 72)
            self.assertEqual(result["agents_per_market"], 12)
            manifests = [json.loads((root / name / "auction_manifest.json").read_text(encoding="utf-8")) for name in ("output", "rerun")]
            self.assertEqual(manifests[0]["artifacts"], manifests[1]["artifacts"])
            audited = audit_directory(root / "output", config_path, root / "audit")
            self.assertEqual(audited["ledger_rows"], 72)
            self.assertEqual(audited, load_audit(root / "output", root / "audit"))
            self.assertEqual(audited["halted_sessions"], 6)
            with gzip.open(root / "output/auction_ledger.jsonl.gz", "rt", encoding="utf-8") as stream:
                ledger = [json.loads(line) for line in stream]
            self.assertEqual(len(ledger), 72)
            for row in ledger:
                auction = row["auction"]
                self.assertEqual(auction["cash_total_minor"] + auction["fee_pool_minor"], 120000000)
                self.assertEqual(sum(account["shares"] for account in auction["accounts"].values()), 8100)
                if row["stock_code"] == "000002" and row["trade_date"] == "2020-07-03":
                    self.assertFalse(auction["execution_available"])
                    self.assertEqual(auction["matched_volume"], 0)
            with self.assertRaisesRegex(ValueError, "separate"):
                run_experiment(config_path, root)
            original = decisions.read_text(encoding="utf-8")
            decisions.write_text(original + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source changed"):
                load_auction_summary(root / "output")
            decisions.write_text(original, encoding="utf-8")
            artifact = root / "output/auction_summary.json"
            artifact.write_text(artifact.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                load_auction_summary(root / "output")


if __name__ == "__main__":
    unittest.main()
