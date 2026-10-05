import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from test_assistant_review import fixture
from research.data_pipeline.fetch_baostock import fetch_baostock
from research.data_pipeline.market_data import import_market
from research.data_pipeline.provenance import file_sha256
from research.semantic.assistant_review import finalize_review
from research.semantic.compare_holdout import compare_holdout
from research.semantic.agent_signal_adapter import run_adapter
from research.semantic.event_snapshot import VERSION as SNAPSHOT_VERSION
from research.semantic.parse_model_outputs import normalize_file
from research.simulation.semantic_historical_replay import run_ablation, load_verified_summary


class Response:
    error_code, error_msg = "0", ""

    def __init__(self, fields=(), rows=()):
        self.fields, self.rows, self.index = list(fields), list(rows), -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class ObservedFixtureSDK:
    """Local synthetic provider response used only to exercise the observed-data pipeline."""
    def login(self):
        return Response()

    def logout(self):
        return Response()

    def query_trade_dates(self, start_date, end_date):
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return Response(["calendar_date", "is_trading_day"],
                        [[day.isoformat(), str(int(day.weekday() < 5))] for day in days])

    def query_stock_basic(self):
        return Response(["code", "code_name", "ipoDate", "outDate", "type", "status"], [
            ["sz.000001", "Fixture One", "2000-01-01", "", "1", "1"],
            ["sz.000002", "Fixture Two", "2000-01-01", "", "1", "1"],
            ["sz.000003", "Fixture Three", "2000-01-01", "", "1", "1"],
        ])

    def query_history_k_data_plus(self, symbol, fields, start_date, end_date, **kwargs):
        calendar = self.query_trade_dates(start_date, end_date)
        rows, previous = [], 100.0
        for index, (day, flag) in enumerate(calendar.rows):
            if flag != "1":
                continue
            suspended = symbol == "sz.000002" and day == "2020-07-02"
            rate = 0.0 if suspended else (0.005 if index % 2 else -0.002)
            close = previous * (1 + rate)
            record = {"date": day, "code": symbol, "close": str(close), "preclose": str(previous),
                      "volume": "0" if suspended else "1000", "amount": "0" if suspended else "100000",
                      "adjustflag": "1", "tradestatus": "0" if suspended else "1", "pctChg": str(rate * 100)}
            columns = fields.split(",")
            rows.append([record[name] for name in columns])
            previous = close
        return Response(columns, rows)


class SemanticHistoricalReplayTest(unittest.TestCase):
    def test_source_bound_end_to_end_ablation_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, decisions = fixture(root)
            finalize_review(root / "pack", decisions, root / "review")
            compare_holdout(root / "pack", None, None, root / "raw", root / "normalized",
                            root / "keyword", root / "scored", ai_review_dir=root / "review")
            run_adapter(root / "pack", root / "normalized/model_predictions.jsonl", root / "scored", root / "signals")
            fetch_baostock(["sz.000001", "sz.000002"], "sh.000300", "2020-06-25", "2020-07-10",
                           root / "download", sdk=ObservedFixtureSDK())
            import_market(root / "download/market_import.json", root / "prepared")
            agents = json.loads((Path(__file__).parents[1] / "research/configs/synthetic_stress_v1.json")
                                .read_text(encoding="utf-8"))["agents"]
            cfg = {"run_id": "fixture", "data_kind": "observed", "market_manifest": "prepared/market_manifest.json",
                   "download_manifest": "download/download_manifest.json", "signal_directory": "signals",
                   "selection_rule": "all_signal_stocks", "stock_codes": ["000001", "000002"],
                   "start_date": "2020-07-03", "end_date": "2020-07-10", "momentum_sessions": 2,
                   "volatility_sessions": 2, "transaction_cost_rate": 0.001, "agents": agents}
            config_path = root / "config.json"
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            result = run_ablation(config_path, root / "output")
            summary = load_verified_summary(root / "output")
            self.assertEqual(summary["stocks"], 2)
            self.assertEqual(summary["directional_stocks"], 1)
            self.assertEqual(summary["no_effect_stocks"], 1)
            self.assertEqual(summary["blocked_execution_days"], 1)
            self.assertEqual(result["paths"]["000001"]["text"]["summary"],
                             result["paths"]["000001"]["no_text"]["summary"])
            for path in ("text", "no_text"):
                trace = result["paths"]["000002"][path]["trace"][0]
                self.assertFalse(trace["execution_available"])
                self.assertTrue(all(row["filled_shares"] == 0 for row in trace["agents"].values()))
            with self.assertRaisesRegex(ValueError, "separate"):
                run_ablation(config_path, root)
            original = decisions.read_text(encoding="utf-8")
            decisions.write_text(original + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source changed"):
                load_verified_summary(root / "output")
            decisions.write_text(original, encoding="utf-8")
            artifact = root / "output/semantic_ablation_summary.json"
            artifact.write_text(artifact.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                load_verified_summary(root / "output")

    def test_pre_event_universe_keeps_company_without_visible_reply(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, decisions = fixture(root)
            universe = {"stock_codes": ["000001", "000002", "000003"],
                        "selection": {"selection_rule": "sha256_rank_of_pre_event_question_active_issuers",
                                      "seed": "fixture"}}
            universe_path = root / "universe.json"
            universe_path.write_text(json.dumps(universe), encoding="utf-8")
            pack_path = root / "pack/annotation_manifest.json"
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
            pack.update({"snapshot_pipeline_version": SNAPSHOT_VERSION, "universe_size": 3,
                         "universe_selection": universe["selection"],
                         "input_sha256": {str(universe_path.resolve()): file_sha256(universe_path)}})
            pack["counts"].update(selected_companies=2, companies_without_reply_snapshot=1)
            pack_path.write_text(json.dumps(pack), encoding="utf-8")
            raw_path = root / "raw/model_run_manifest.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw["input_sha256"]["annotation_manifest"] = file_sha256(pack_path)
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            keyword_path = root / "keyword/keyword_manifest.json"
            keyword = json.loads(keyword_path.read_text(encoding="utf-8"))
            keyword["input_sha256"]["annotation_manifest.json"] = file_sha256(pack_path)
            keyword_path.write_text(json.dumps(keyword), encoding="utf-8")
            normalize_file(root / "pack", root / "raw/model_raw_outputs.jsonl", root / "normalized_snapshot")
            finalize_review(root / "pack", decisions, root / "review")
            compare_holdout(root / "pack", None, None, root / "raw", root / "normalized_snapshot",
                            root / "keyword", root / "scored", ai_review_dir=root / "review")
            run_adapter(root / "pack", root / "normalized_snapshot/model_predictions.jsonl",
                        root / "scored", root / "signals")
            fetch_baostock(["sz.000001", "sz.000002", "sz.000003"], "sh.000300",
                           "2020-06-25", "2020-07-10", root / "download", sdk=ObservedFixtureSDK())
            market_config = json.loads((root / "download/market_import.json").read_text(encoding="utf-8"))
            for key in ("stock_input", "benchmark_input", "calendar_input"):
                market_config[key] = "download/" + market_config[key]
            external_config = root / "market_import.json"
            external_config.write_text(json.dumps(market_config), encoding="utf-8")
            import_market(external_config, root / "prepared")
            agents = json.loads((Path(__file__).parents[1] / "research/configs/synthetic_stress_v1.json")
                                .read_text(encoding="utf-8"))["agents"]
            config = {"run_id": "snapshot-fixture", "data_kind": "observed",
                      "market_manifest": "prepared/market_manifest.json",
                      "download_manifest": "download/download_manifest.json",
                      "signal_directory": "signals", "selection_rule": "pre_event_snapshot_universe",
                      "universe_config": "universe.json", "stock_codes": universe["stock_codes"],
                      "start_date": "2020-07-03", "end_date": "2020-07-10",
                      "momentum_sessions": 2, "volatility_sessions": 2,
                      "transaction_cost_rate": 0.001, "agents": agents}
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = run_ablation(config_path, root / "output")
            summary = load_verified_summary(root / "output")
            self.assertEqual(summary["stocks"], 3)
            self.assertEqual(summary["no_effect_stocks"], 2)
            self.assertEqual(result["paths"]["000003"]["text"]["summary"],
                             result["paths"]["000003"]["no_text"]["summary"])
            universe_path.write_text(universe_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source changed"):
                load_verified_summary(root / "output")


if __name__ == "__main__":
    unittest.main()
