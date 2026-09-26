import copy
import csv
import json
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

from research.baselines.event_study import DailyObservation
from research.baselines.prediction_panel import Question, close_at, load_prediction_config, make_panel, text_window
from research.baselines.run_prediction import block_bootstrap, chronological_split, evaluate_panel, fit_ridge, predict, run_prediction
from research.data_pipeline.build_dataset import build_dataset
from research.data_pipeline.market_data import import_market
from research.data_pipeline.provenance import file_sha256
from research.examples.generate_market_fixture import generate_fixture


def fixture():
    days, day = [], date(2020, 1, 1)
    while len(days) < 100:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    groups = {code: [DailyObservation(d, 0.002 * math.sin(i) + int(code) * 0.0001, 0.001 * math.cos(i))
                     for i, d in enumerate(days)] for code in ("000001", "000002")}
    questions = {code: [Question(close_at(days[5]), "政策风险", close_at(days[30]), "利润", True),
                        Question(close_at(days[40]), "疫情", close_at(days[50]), "回购", True)] for code in groups}
    config = {"run_id": "synthetic_text_validation", "data_kind": "synthetic", "qa_database": "dataset/dataset.sqlite",
              "qa_source_sha256": "a" * 64, "market_manifest": "prepared/market_manifest.json",
              "qa_coverage": {"start_date": "2020-01-01", "end_date": "2020-12-31", "basis": "Synthetic question coverage"},
              "stock_codes": ["000001", "000002"],
              "panel": {"start_date": days[20].isoformat(), "end_date": days[98].isoformat(),
                        "text_window_days": 10, "market_window_sessions": 5},
              "splits": {name: {"start_date": days[start].isoformat(), "end_date": days[end].isoformat()}
                         for name, start, end in (("train", 20, 49), ("validation", 50, 74), ("test", 75, 98))},
              "ridge_lambdas": [0.001, 0.1, 10], "bootstrap": {"seed": 11, "replicates": 100, "block_sessions": 3}}
    return groups, questions, config, days


def write_questions(path, text):
    book = Workbook()
    book.active.append(["股票代码", "提问时间", "提问内容", "上市公司是否回复", "回复时间", "回复内容"])
    for code in (1, 2):
        book.active.append([code, "2020-01-06 10:00:00", text, "已回复", "2020-03-01", "利润"])
    book.save(path)
    book.close()


class TextPredictionTest(unittest.TestCase):
    def test_future_reply_text_and_questions_do_not_change_earlier_window(self):
        records = [Question("2020-01-02T10:00:00.000000+08:00", "政策", "2020-01-04T00:00:00.000000+08:00", "风险", True),
                   Question("2020-01-05T10:00:00.000000+08:00", "疫情")]
        start, cutoff = "2020-01-01T00:00:00.000000+08:00", "2020-01-03T15:00:00.000000+08:00"
        first = text_window(records, start, cutoff, "000001")
        changed = [Question(records[0].question_available_at, "政策", records[0].reply_available_at, "分红治理", True),
                   Question(records[1].question_available_at, "债务诉讼")]
        self.assertEqual(first, text_window(changed, start, cutoff, "000001"))
        self.assertEqual(first["known_reply_count"], 0)
        later = text_window(records, start, "2020-01-04T00:00:00.000000+08:00", "000001")
        self.assertEqual(later["reply_risk_share"], 1)
        self.assertEqual(text_window(records, "2020-01-03T00:00:00.000000+08:00", cutoff, "000001")["question_count"], 0)

    def test_next_return_is_target_and_never_current_feature(self):
        groups, questions, config, days = fixture()
        first = make_panel(groups, questions, config)[0]
        obs = groups["000001"][21]
        groups["000001"][21] = DailyObservation(obs.trade_date, 0.9, 0.8)
        changed = make_panel(groups, questions, config)[0]
        self.assertEqual(first["as_of"], close_at(days[20]))
        self.assertEqual(first["target_date"], days[21].isoformat())
        self.assertNotEqual(first["target_return"], changed["target_return"])
        self.assertEqual({k: v for k, v in first.items() if k != "target_return"},
                         {k: v for k, v in changed.items() if k != "target_return"})

    def test_labels_on_boundary_are_purged_before_model_fitting(self):
        groups, questions, config, _ = fixture()
        splits, audit = chronological_split(make_panel(groups, questions, config), config)
        self.assertEqual(audit["train"]["purged_rows"], 2)
        self.assertEqual(audit["validation"]["purged_rows"], 2)
        self.assertLess(max(r["target_available_at"] for r in splits["train"]), min(r["as_of"] for r in splits["validation"]))
        self.assertLess(max(r["target_available_at"] for r in splits["validation"]), min(r["as_of"] for r in splits["test"]))

    def test_test_labels_cannot_select_lambda_scaler_or_fitted_coefficients(self):
        groups, questions, config, _ = fixture()
        panel = make_panel(groups, questions, config)
        first, predictions = evaluate_panel(panel, config)
        changed = copy.deepcopy(panel)
        for row in changed:
            if row["trade_date"] >= config["splits"]["test"]["start_date"]:
                row["target_return"] = 0.7
        second, changed_predictions = evaluate_panel(changed, config)
        for key in ("models", "validation_grid", "historical_means"):
            self.assertEqual(first[key], second[key])
        for a, b in zip(predictions, changed_predictions):
            self.assertEqual(a["market_only"], b["market_only"])
            self.assertEqual(a["market_plus_text"], b["market_plus_text"])
        self.assertNotEqual(first["test_metrics"], second["test_metrics"])

    def test_ridge_recovers_known_linear_relation_and_training_scaler(self):
        rows = [{"x": float(i), "target_return": 2 + i * 0.3, "target_available_at": str(i).zfill(4)} for i in range(30)]
        model = fit_ridge(rows, ["x"], 1e-8)
        self.assertEqual(model["means"], [14.5])
        self.assertAlmostEqual(predict(model, {"x": 40}), 14, places=6)
        constant = fit_ridge([{**row, "x": 3} for row in rows], ["x"], 1)
        self.assertEqual(constant["scales"], [1])
        self.assertEqual(constant["coefficients"], [0])

    def test_missing_stock_coverage_and_incomplete_calendar_fail(self):
        groups, questions, config, _ = fixture()
        questions["000002"] = []
        with self.assertRaisesRegex(ValueError, "future QA coverage"):
            make_panel(groups, questions, config)
        groups, questions, config, days = fixture()
        questions["000002"] = [Question(close_at(days[30]), "未来才出现的问答")]
        with self.assertRaisesRegex(ValueError, "future QA coverage"):
            make_panel(groups, questions, config)
        groups, questions, config, _ = fixture()
        del groups["000002"][40]
        with self.assertRaisesRegex(ValueError, "complete panel calendar"):
            make_panel(groups, questions, config)

    def test_window_before_declared_coverage_fails(self):
        groups, questions, config, _ = fixture()
        config["panel"]["text_window_days"] = 60
        with self.assertRaisesRegex(ValueError, "declared QA coverage"):
            make_panel(groups, questions, config)

    def test_bootstrap_is_reproducible_and_preserves_difference_sign(self):
        settings = {"seed": 4, "replicates": 100, "block_sessions": 3}
        result = block_bootstrap([0.01] * 20, settings)
        self.assertEqual(result["mean"], 0.01)
        self.assertEqual(result["interval_95"], [0.01, 0.01])
        self.assertEqual(result, block_bootstrap([0.01] * 20, settings))
        with self.assertRaisesRegex(ValueError, "two finite bootstrap blocks"):
            block_bootstrap([0.01] * 3, settings)

    def test_configuration_rejects_overlapping_splits_and_invalid_penalty(self):
        _, _, config, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for field, value in (("ridge_lambdas", [True]), ("ridge_lambdas", [-1]), ("ridge_lambdas", [float("nan")])):
                changed = copy.deepcopy(config)
                changed[field] = value
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    load_prediction_config(path)
            config["splits"]["validation"]["start_date"] = config["splits"]["train"]["end_date"]
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "ordered and disjoint"):
                load_prediction_config(path)

    def test_end_to_end_source_filter_provenance_and_tamper_guard(self):
        _, _, config, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, other = root / "source.xlsx", root / "other.xlsx"
            write_questions(source, "政策")
            write_questions(other, "疫情疫情疫情")
            build_dataset([source, other], root / "dataset")
            config["qa_source_sha256"] = file_sha256(source)
            paths = generate_fixture(root)
            import_market(paths["market_config"], root / "prepared")
            path = root / "config.json"
            path.write_text(json.dumps(config))
            result = run_prediction(path, root / "results")
            self.assertEqual(result["provenance"]["qa_questions_by_stock"], {"000001": 1, "000002": 1})
            self.assertEqual(result["data_kind"], "synthetic")
            with (root / "results/prediction_panel.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(all(float(row["question_pandemic_count"]) == 0 for row in rows))
            self.assertFalse(any("user" in k or "question_text" in k or "violation" in k for k in rows[0]))
            manifest = json.loads((root / "results/prediction_manifest.json").read_text())
            for name, info in manifest["artifacts"].items():
                self.assertEqual(file_sha256(root / "results" / name), info["sha256"])
            repeated = run_prediction(path, root / "second")
            self.assertEqual(result, repeated)
            with self.assertRaisesRegex(ValueError, "new empty output"):
                run_prediction(path, root / "results")
            market_csv = root / "prepared/market_daily.csv"
            market_csv.write_text(market_csv.read_text() + "tampered\n")
            with self.assertRaisesRegex(ValueError, "CSV hash"):
                run_prediction(path, root / "third")


if __name__ == "__main__":
    unittest.main()
