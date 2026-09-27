import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.semantic.agent_signal_adapter import GATE_SCOPE, VERSION as ADAPTER_VERSION
from research.simulation.agents import AgentParameters
from research.simulation.semantic_replay import (
    VERSION, replay_joined_payload, replay_semantic_path, replay_signal_directory,
)
from research.simulation.semantic_signal_join import VERSION as JOIN_VERSION


ROOT = Path(__file__).resolve().parents[1]


def steps():
    return [
        {"trade_date": "2020-01-02", "signal_cutoff_date": "2020-01-01",
         "observed_return": 0.01, "market_signal": 0.0, "estimated_volatility": 0.02,
         "text_signal": 0.8, "text_uncertainty": 0.1, "text_evidence": "semantic:one"},
        {"trade_date": "2020-01-03", "signal_cutoff_date": "2020-01-02",
         "observed_return": -0.01, "market_signal": 0.0, "estimated_volatility": 0.02,
         "text_signal": -0.8, "text_uncertainty": 0.1, "text_evidence": "semantic:two"},
        {"trade_date": "2020-01-06", "signal_cutoff_date": "2020-01-03",
         "observed_return": 0.02, "market_signal": 0.0, "estimated_volatility": 0.02,
         "text_signal": 0.0, "text_uncertainty": 0.0, "text_evidence": "semantic:none"},
    ]


class SemanticReplayTest(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "research/configs/synthetic_stress_v1.json").read_text(encoding="utf-8"))
        self.agents = [AgentParameters(**raw) for raw in config["agents"]]

    def test_text_channel_changes_decisions_and_preserves_ledger(self):
        active = replay_semantic_path(steps(), self.agents, 0.001, use_text=True)
        control = replay_semantic_path(steps(), self.agents, 0.001, use_text=False)
        self.assertEqual(active["pipeline_version"], VERSION)
        self.assertNotEqual(active["trace"][0]["agents"]["aggressive"]["decision"]["target_weight"],
                            control["trace"][0]["agents"]["aggressive"]["decision"]["target_weight"])
        self.assertNotEqual(active["summary"]["aggressive"]["final_wealth"],
                            control["summary"]["aggressive"]["final_wealth"])
        for trace in active["trace"]:
            self.assertAlmostEqual(
                sum(row["cash"] for row in trace["agents"].values())
                + trace["external_cash"] + trace["fee_pool"],
                sum(agent.initial_cash for agent in self.agents), places=6)
            self.assertAlmostEqual(sum(row["shares"] for row in trace["agents"].values())
                                   + trace["external_shares"], 0.0, places=8)

    def test_joined_payload_requires_passed_gate(self):
        payload = {"pipeline_version": JOIN_VERSION, "steps": steps(),
                   "eligible_gate": {"passed": True, "checks": {"reviewed": True}}}
        result = replay_joined_payload(payload, self.agents, 0.001)
        self.assertEqual(result["pipeline_version"], VERSION)
        bad = {**payload, "eligible_gate": {"passed": False, "checks": {"reviewed": False}}}
        with self.assertRaisesRegex(ValueError, "passed Agent signal gate"):
            replay_joined_payload(bad, self.agents, 0.001)

    def test_directory_runner_rechecks_adapter_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            row = {"item_id": "1" * 64, "stock_code": "000001",
                   "available_at": "2020-01-01T10:00:00+08:00", "stage": "question",
                   "source_text_sha256": "2" * 64, "model_id": "model",
                   "prompt_version": "prompt", "text_signal": 0.8, "uncertainty": 0.1,
                   "event_count": 1, "parse_error": None, "text_evidence": "sha256:" + "2" * 64}
            rows_path = directory / "agent_signal_rows.jsonl"
            rows_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            result_path = directory / "agent_signal_result.json"
            result_path.write_text(json.dumps({
                "pipeline_version": ADAPTER_VERSION,
                "gate": {"passed": True, "checks": {"reviewed": True}, "scope": GATE_SCOPE},
            }), encoding="utf-8")
            report_path = directory / "agent_signal_report.md"
            report_path.write_text("fixture", encoding="utf-8")
            (directory / "agent_signal_manifest.json").write_text(json.dumps({
                "pipeline_version": ADAPTER_VERSION,
                "artifacts": {path.name: {"sha256": file_sha256(path)}
                              for path in (rows_path, result_path, report_path)},
            }), encoding="utf-8")
            result = replay_signal_directory(steps(), directory, "000001", self.agents, 0.001)
            self.assertEqual(result["pipeline_version"], VERSION)
            self.assertEqual(result["trace"][0]["semantic_item_count"], 1)

    def test_future_or_duplicate_dates_are_rejected(self):
        bad = steps()
        bad[1] = {**bad[1], "signal_cutoff_date": "2020-01-03"}
        with self.assertRaisesRegex(ValueError, "cutoff"):
            replay_semantic_path(bad, self.agents, 0.001)


if __name__ == "__main__":
    unittest.main()
