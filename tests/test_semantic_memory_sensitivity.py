import copy
import json
import tempfile
import unittest
from pathlib import Path

from test_assistant_review import fixture
from test_semantic_historical_replay import ObservedFixtureSDK
from test_semantic_signal_join import signal_row
from research.data_pipeline.fetch_baostock import fetch_baostock
from research.data_pipeline.market_data import import_market
from research.semantic.assistant_review import finalize_review
from research.semantic.compare_holdout import compare_holdout
from research.semantic.agent_signal_adapter import run_adapter
from research.simulation.semantic_historical_replay import run_ablation
from research.simulation.semantic_signal_join import join_steps
from research.simulation.semantic_memory_sensitivity import (
    memory_join, validate_scenario, local_visible_date, run_sensitivity, load_memory_summary,
)


def scenario(mode="cumulative", sessions=None, lag=0):
    return {"scenario_id": f"{mode}_{sessions or 'all'}_lag{lag}", "memory_mode": mode,
            "memory_sessions": sessions, "lag_days": lag}


def steps(cutoffs):
    from datetime import date, timedelta
    return [{"trade_date": (date.fromisoformat(day) + timedelta(days=2)).isoformat(),
             "signal_cutoff_date": day} for day in cutoffs]


class SemanticMemorySensitivityTest(unittest.TestCase):
    def test_cumulative_zero_delay_matches_existing_join_and_future_isolation(self):
        calendar = ["2020-07-01", "2020-07-02", "2020-07-03", "2020-07-06"]
        replay_steps = steps(calendar)
        rows = [signal_row(1, "2020-07-01T15:00:00+08:00", 0.8),
                signal_row(2, "2020-07-03T10:00:00+08:00", -0.6),
                signal_row(3, "2020-07-02T10:00:00+08:00", 0.9, stock_code="000002")]
        new = memory_join(replay_steps, "000001", rows, calendar, scenario())
        for actual, expected in zip(new, join_steps(replay_steps, "000001", rows)):
            self.assertEqual({key: actual[key] for key in expected}, expected)
        future = signal_row(4, "2020-07-10T10:00:00+08:00", -1.0)
        for policy in (scenario(), scenario("rolling", 2), scenario("exponential", 2), scenario(lag=3)):
            self.assertEqual(memory_join(replay_steps, "000001", rows, calendar, policy),
                             memory_join(replay_steps, "000001", rows + [future], calendar, policy))

    def test_rolling_exact_boundary_and_holiday_age(self):
        calendar = ["2020-09-30", "2020-10-09", "2020-10-12", "2020-10-13"]
        rows = [signal_row(1, "2020-10-01T10:00:00+08:00", 0.8)]
        joined = memory_join(steps(calendar), "000001", rows, calendar, scenario("rolling", 2))
        self.assertEqual([row["text_signal"] for row in joined], [0, 0.8, 0.8, 0])
        self.assertEqual(joined[-1]["semantic_expired_item_count"], 1)
        self.assertEqual(joined[-1]["text_uncertainty"], 0)
        self.assertEqual(joined[-1]["semantic_visible_item_count"], 1)

    def test_exponential_half_life_reduces_single_event_amplitude(self):
        calendar = ["2020-07-01", "2020-07-02", "2020-07-03", "2020-07-06", "2020-07-07"]
        rows = [signal_row(1, "2020-07-01T10:00:00+08:00", 0.8)]
        joined = memory_join(steps(calendar), "000001", rows, calendar, scenario("exponential", 2))
        self.assertAlmostEqual(joined[2]["text_signal"], 0.4)
        self.assertAlmostEqual(joined[2]["text_uncertainty"], 0.1)
        self.assertAlmostEqual(joined[4]["text_signal"], 0.2)
        self.assertNotEqual(joined[0]["text_evidence"], joined[2]["text_evidence"])

    def test_visible_empty_rows_preserve_denominator_and_rolling_removes_expired(self):
        calendar = ["2020-07-01", "2020-07-02", "2020-07-03"]
        rows = [signal_row(1, "2020-07-01T10:00:00+08:00", 0.8),
                {**signal_row(2, "2020-07-03T10:00:00+08:00", 0), "uncertainty": 0, "event_count": 0}]
        joined = memory_join(steps(calendar), "000001", rows, calendar, scenario("exponential", 2))
        self.assertAlmostEqual(joined[2]["text_signal"], 0.2)
        self.assertAlmostEqual(joined[2]["text_uncertainty"], 0.05)
        rolling = memory_join(steps(calendar), "000001", rows, calendar, scenario("rolling", 2))
        self.assertEqual(rolling[2]["text_signal"], 0)
        self.assertEqual(rolling[2]["text_uncertainty"], 0)

    def test_natural_day_delay_weekend_and_china_timezone(self):
        calendar = ["2020-07-01", "2020-07-02", "2020-07-03", "2020-07-06", "2020-07-07"]
        rows = [signal_row(1, "2020-07-03T10:00:00+08:00", 0.8)]
        joined = memory_join(steps(calendar), "000001", rows, calendar, scenario("rolling", 1, lag=1))
        self.assertEqual([row["text_signal"] for row in joined], [0, 0, 0, 0.8, 0])
        self.assertEqual(str(local_visible_date("2020-07-01T20:00:00Z")), "2020-07-02")
        utc_rows = [signal_row(2, "2020-07-01T20:00:00Z", 0.8)]
        utc_joined = memory_join(steps(calendar), "000001", utc_rows, calendar, scenario("rolling", 2))
        self.assertEqual(utc_joined[0]["text_signal"], 0)
        self.assertEqual(utc_joined[1]["text_signal"], 0.8)
        with self.assertRaisesRegex(ValueError, "China-time"):
            memory_join(steps(calendar), "000001", utc_rows, calendar, scenario())

    def test_invalid_memory_or_incomplete_calendar_rejected(self):
        for bad in (scenario("rolling", 0), scenario("exponential", True), scenario("cumulative", 5),
                    scenario("unknown"), scenario(lag=-1), scenario(lag=True)):
            with self.assertRaises(ValueError):
                validate_scenario(bad)
        with self.assertRaisesRegex(ValueError, "calendar"):
            memory_join(steps(["2020-07-02"]), "000001", [], ["2020-07-01"], scenario())
        with self.assertRaisesRegex(ValueError, "starts after"):
            memory_join(steps(["2020-07-02"]), "000001", [signal_row(1, "2020-07-01", 1)],
                        ["2020-07-02"], scenario("rolling", 2))

    def test_full_source_chain_sensitivity_no_text_parity_rerun_and_tamper(self):
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
            agents = json.loads((Path(__file__).parents[1] / "research/configs/synthetic_stress_v1.json")
                                .read_text(encoding="utf-8"))["agents"]
            base = {"run_id": "fixture", "data_kind": "observed", "market_manifest": "prepared/market_manifest.json",
                    "download_manifest": "download/download_manifest.json", "signal_directory": "signals",
                    "selection_rule": "all_signal_stocks", "stock_codes": ["000001", "000002"],
                    "start_date": "2020-07-03", "end_date": "2020-07-10", "momentum_sessions": 2,
                    "volatility_sessions": 2, "transaction_cost_rate": 0.001, "agents": agents}
            (root / "base.json").write_text(json.dumps(base), encoding="utf-8")
            run_ablation(root / "base.json", root / "baseline")
            cfg = {"run_id": "memory_fixture", "base_config": "base.json", "baseline_directory": "baseline",
                   "scenarios": [scenario(), scenario("rolling", 2), scenario("exponential", 2), scenario(lag=3)]}
            config_path = root / "memory.json"
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            result = run_sensitivity(config_path, root / "output")
            rerun = run_sensitivity(config_path, root / "rerun")
            self.assertEqual(result, rerun)
            self.assertEqual(result, load_memory_summary(root / "output"))
            self.assertEqual(result["comparison_rows"], 24)
            self.assertTrue(result["baseline_trajectory_parity"])
            self.assertTrue(result["no_text_trajectory_invariant"])
            self.assertEqual(result["blocked_execution_days_per_scenario"], 1)
            manifest = json.loads((root / "output/semantic_memory_manifest.json").read_text(encoding="utf-8"))
            repeated = json.loads((root / "rerun/semantic_memory_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["artifacts"], repeated["artifacts"])
            rows = json.loads((root / "output/semantic_memory_results.json").read_text(encoding="utf-8"))["comparisons"]
            for role in ("aggressive", "conservative", "institutional"):
                for code in ("000001", "000002"):
                    selected = [row for row in rows if row["role"] == role and row["stock_code"] == code]
                    self.assertTrue(all(row["no_text_summary"] == selected[0]["no_text_summary"] for row in selected))
                    if code == "000001":
                        self.assertTrue(all(row["wealth_difference_multiple"] == 0 for row in selected))
            with self.assertRaisesRegex(ValueError, "separate"):
                run_sensitivity(config_path, root)
            broken = copy.deepcopy(cfg)
            broken["scenarios"].append(scenario())
            (root / "bad.json").write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                run_sensitivity(root / "bad.json", root / "bad-output")
            original = decisions.read_text(encoding="utf-8")
            decisions.write_text(original + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source changed"):
                load_memory_summary(root / "output")
            decisions.write_text(original, encoding="utf-8")
            artifact = root / "output/semantic_memory_summary.json"
            artifact.write_text(artifact.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                load_memory_summary(root / "output")


if __name__ == "__main__":
    unittest.main()
