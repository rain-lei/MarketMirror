import csv
import json
import tempfile
import unittest
from pathlib import Path

from research.baselines.run_experiments import run_experiments, load_experiment, visibility_anchor
from research.data_pipeline.market_data import import_market, read_config
from research.data_pipeline.provenance import file_sha256
from research.examples.generate_market_fixture import generate_fixture


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class MarketPipelineTest(unittest.TestCase):
    def test_end_to_end_known_shocks_price_conversion_and_repeatability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            source_hashes = {p.name: file_sha256(p) for p in root.glob("*.csv")}
            market = import_market(paths["market_config"], root / "prepared")
            self.assertEqual(market["counts"]["market_rows"], 198)
            self.assertEqual(market["counts"]["warmup_rows_without_aligned_returns"], 2)
            results = run_experiments(paths["experiment_config"], root / "results")
            self.assertEqual(results["status"], "complete")
            self.assertEqual(len(results["results"]), 4)
            for result in results["results"]:
                expected = {"000001": 0.04, "000002": -0.02}[result["stock_code"]] if result["event_id"] == "known_shock" else 0
                self.assertAlmostEqual(result["cumulative_abnormal_return"], expected, places=11)
                self.assertAlmostEqual(result["beta"], {"000001": 1.2, "000002": 0.8}[result["stock_code"]], places=10)
                self.assertLess(result["estimation_end"], result["abnormal_returns"][0]["trade_date"])
            self.assertAlmostEqual(results["event_aggregates"][0]["mean_cumulative_abnormal_return"], 0.01, places=11)
            rerun = run_experiments(paths["experiment_config"], root / "results")
            self.assertEqual(rerun["experiment_id"], results["experiment_id"])
            self.assertEqual(rerun["results"], results["results"])
            self.assertEqual(source_hashes, {p.name: file_sha256(p) for p in root.glob("*.csv")})
            manifest = json.loads((root / "results" / "experiment_manifest.json").read_text())
            for name, artifact in manifest["artifacts"].items():
                self.assertEqual(file_sha256(root / "results" / name), artifact["sha256"])
            self.assertIn("Synthetic fixture only", (root / "results" / "event_report.md").read_text())

    def test_missing_stock_or_benchmark_session_is_not_bridged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            for name in ["stock_prices.csv", "benchmark_prices.csv"]:
                path = root / name
                before = path.read_bytes()
                fields, rows = read_csv(path)
                del rows[20]
                write_csv(path, fields, rows)
                with self.assertRaisesRegex(ValueError, "missing sessions"):
                    import_market(paths["market_config"], root / "prepared")
                path.write_bytes(before)
            self.assertFalse((root / "prepared" / "market_daily.csv").exists())

    def test_duplicate_dates_invalid_prices_returns_and_units_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            path = root / "stock_prices.csv"
            fields, rows = read_csv(path)
            write_csv(path, fields, rows + [rows[0]])
            with self.assertRaisesRegex(ValueError, "duplicate stock"):
                import_market(paths["market_config"], root / "prepared")
            for bad in ["0", "-1", "NaN", "inf", ""]:
                modified = [dict(r) for r in rows]
                modified[0]["adjusted_close"] = bad
                write_csv(path, fields, modified)
                with self.assertRaises(ValueError):
                    import_market(paths["market_config"], root / "prepared")
            write_csv(path, fields, rows)
            settings = json.loads(paths["market_config"].read_text())
            settings["stock_price_basis"] = "unadjusted"
            write_json(paths["market_config"], settings)
            with self.assertRaisesRegex(ValueError, "unadjusted"):
                import_market(paths["market_config"], root / "prepared")

    def test_percent_returns_are_converted_explicitly_and_strict_dates_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            settings = json.loads(paths["market_config"].read_text())
            for prefix in ["stock", "benchmark"]:
                settings.pop(f"{prefix}_price_basis")
                settings[f"{prefix}_format"] = "returns"
                settings[f"{prefix}_return_unit"] = "percent"
                settings[f"{prefix}_return_basis"] = "adjusted_price_return" if prefix == "stock" else "price_index_return"
            fields, stocks = read_csv(root / "stock_prices.csv")
            for row in stocks:
                row["adjusted_close"] = "1.25"
            write_csv(root / "stock_prices.csv", fields, stocks)
            fields_b, benchmark = read_csv(root / "benchmark_prices.csv")
            for row in benchmark:
                row["close"] = "0.5"
            write_csv(root / "benchmark_prices.csv", fields_b, benchmark)
            write_json(paths["market_config"], settings)
            report = import_market(paths["market_config"], root / "prepared")
            self.assertEqual(report["counts"]["market_rows"], 200)
            _, normalized = read_csv(root / "prepared" / "market_daily.csv")
            self.assertAlmostEqual(float(normalized[0]["stock_return"]), 0.0125)
            self.assertAlmostEqual(float(normalized[0]["market_return"]), 0.005)
            self.assertEqual(normalized[0]["stock_price"], "")
            stocks[0]["adjusted_close"] = "-101"
            write_csv(root / "stock_prices.csv", fields, stocks)
            with self.assertRaisesRegex(ValueError, "below -100"):
                import_market(paths["market_config"], root / "other")
            stocks[0]["adjusted_close"] = "1.25"
            stocks[0]["trade_date"] += "T00:00:00"
            write_csv(root / "stock_prices.csv", fields, stocks)
            with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                import_market(paths["market_config"], root / "other")
            settings["stock_return_unit"] = "log"
            write_json(paths["market_config"], settings)
            with self.assertRaisesRegex(ValueError, "unit"):
                import_market(paths["market_config"], root / "other")

    def test_mixed_adjusted_and_unadjusted_stock_return_basis_must_be_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            settings = json.loads(paths["market_config"].read_text())
            for prefix in ("stock", "benchmark"):
                settings.pop(f"{prefix}_price_basis")
                settings[f"{prefix}_format"] = "returns"
                settings[f"{prefix}_return_unit"] = "percent"
                settings[f"{prefix}_return_basis"] = (
                    "mixed_adjusted_and_unadjusted_price_return" if prefix == "stock"
                    else "price_index_return")
            write_json(paths["market_config"], settings)
            self.assertEqual(read_config(paths["market_config"])["stock_return_basis"],
                             "mixed_adjusted_and_unadjusted_price_return")
            report = import_market(paths["market_config"], root / "prepared")
            self.assertEqual(report["counts"]["market_rows"], 200)
            self.assertEqual(report["settings"]["stock_return_basis"],
                             "mixed_adjusted_and_unadjusted_price_return")

    def test_visibility_date_only_after_close_weekend_and_timezone(self):
        self.assertEqual(str(visibility_anchor({"event_date": "2020-01-03", "visible_date": "2020-01-03"})[0]), "2020-01-04")
        self.assertEqual(str(visibility_anchor({"event_date": "2020-01-03", "visible_at": "2020-01-03T07:00:00Z"})[0]), "2020-01-04")
        self.assertEqual(str(visibility_anchor({"event_date": "2020-01-06", "visible_at": "2020-01-03T08:00:00+08:00"})[0]), "2020-01-06")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            import_market(paths["market_config"], root / "prepared")
            config = json.loads(paths["experiment_config"].read_text())
            config["events"] = [{"event_id": "weekend", "event_date": "2020-03-20", "visible_at": "2020-03-20T15:00:00+08:00",
                                 "event_type": "synthetic", "evidence_source": "Fixture"}]
            write_json(paths["experiment_config"], config)
            report = run_experiments(paths["experiment_config"], root / "results")
            self.assertEqual(report["results"][0]["event_date_used"], "2020-03-23")

    def test_official_2018_page_timestamp_matches_conservative_date_anchor(self):
        config_dir = Path(__file__).parents[1] / "research" / "configs"
        date_config = load_experiment(config_dir / "observed_pilot_2018.json")
        timestamp_config = load_experiment(config_dir / "observed_pilot_2018_page_timestamp.json")
        date_event = date_config["events"][0]
        timestamp_event = timestamp_config["events"][0]
        self.assertEqual(timestamp_event["visible_at"], "2018-04-27T18:38:49+08:00")
        self.assertEqual(date_event["event_date"], timestamp_event["event_date"])
        self.assertEqual(visibility_anchor(date_event)[0], visibility_anchor(timestamp_event)[0])
        self.assertEqual(str(visibility_anchor(timestamp_event)[0]), "2018-04-28")
        self.assertIn("date_only", visibility_anchor(date_event)[1])
        self.assertIn("timestamp", visibility_anchor(timestamp_event)[1])

    def test_failures_are_recorded_without_reducing_an_event_mean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            config = json.loads(paths["experiment_config"].read_text())
            config["events"] = config["events"][:1]
            fields, rows = read_csv(root / "stock_prices.csv")
            rows = [r for r in rows if not (r["stock_code"] == "000002" and r["trade_date"] > "2020-03-25")]
            write_csv(root / "stock_prices.csv", fields, rows)
            import_market(paths["market_config"], root / "prepared")
            write_json(paths["experiment_config"], config)
            report = run_experiments(paths["experiment_config"], root / "results")
            self.assertEqual(report["status"], "partial")
            self.assertEqual(len(report["results"]), 1)
            self.assertEqual(len(report["failures"]), 1)
            self.assertIn("complete event", report["failures"][0]["reason"])
            self.assertIsNone(report["event_aggregates"][0]["mean_cumulative_abnormal_return"])
            config["events"][0]["event_date"] = "2021-01-01"
            write_json(paths["experiment_config"], config)
            report = run_experiments(paths["experiment_config"], root / "results")
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["results"], [])

    def test_tampering_and_synthetic_observed_mixing_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            import_market(paths["market_config"], root / "prepared")
            config = json.loads(paths["experiment_config"].read_text())
            config["data_kind"] = "observed"
            write_json(paths["experiment_config"], config)
            with self.assertRaisesRegex(ValueError, "classification"):
                run_experiments(paths["experiment_config"], root / "results")
            config["data_kind"] = "synthetic"
            write_json(paths["experiment_config"], config)
            market = root / "prepared" / "market_daily.csv"
            market.write_bytes(market.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "hash differs"):
                run_experiments(paths["experiment_config"], root / "results")

    def test_invalid_event_configs_and_input_overwrite_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            import_market(paths["market_config"], root / "prepared")
            original = json.loads(paths["experiment_config"].read_text())
            for edit in ["naive", "duplicate", "ambiguous", "window"]:
                config = json.loads(json.dumps(original))
                if edit == "naive":
                    config["events"][0]["visible_at"] = "2020-03-25T08:00:00"
                elif edit == "duplicate":
                    config["events"].append(dict(config["events"][0]))
                elif edit == "ambiguous":
                    config["events"][0]["visible_date"] = "2020-03-25"
                else:
                    config["windows"]["window_before"] = True
                write_json(paths["experiment_config"], config)
                with self.assertRaises(ValueError):
                    load_experiment(paths["experiment_config"])
            write_json(paths["experiment_config"], original)
            protected_config = root / "event_results.json"
            protected_config.write_bytes(paths["experiment_config"].read_bytes())
            before = file_sha256(protected_config)
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                run_experiments(protected_config, root)
            self.assertEqual(file_sha256(protected_config), before)

    def test_cached_event_evidence_is_verified_and_traced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = generate_fixture(root)
            import_market(paths["market_config"], root / "prepared")
            source = root / "source.md"
            source.write_text("Authored synthetic evidence fixture", encoding="utf-8")
            url = "https://example.invalid/synthetic-not-fetched"
            evidence = root / "evidence.json"
            write_json(evidence, {"sources": [{"url": url, "path": source.name, "sha256": file_sha256(source)}]})
            config = json.loads(paths["experiment_config"].read_text())
            config["evidence_manifest"] = evidence.name
            for event in config["events"]:
                event["evidence_source"] = url
            write_json(paths["experiment_config"], config)
            report = run_experiments(paths["experiment_config"], root / "results")
            self.assertEqual(len(report["evidence_inputs"]), 2)
            self.assertEqual(report["evidence_inputs"][1]["sha256"], file_sha256(source))
            source.write_text("Changed source", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence file hash"):
                run_experiments(paths["experiment_config"], root / "results")


if __name__ == "__main__":
    unittest.main()
