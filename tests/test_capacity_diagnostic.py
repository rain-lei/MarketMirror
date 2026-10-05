import csv
import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.market_activity import VERSION as ACTIVITY_VERSION
from research.data_pipeline.provenance import file_sha256
from research.simulation.capacity_diagnostic import compare_capacity, run_diagnostic
from research.simulation.historical_replay import VERSION as REPLAY_VERSION


def replay_fixture():
    trace = [
        {"trade_date": "2020-01-02", "price_index_before": 100.0,
         "agents": {"a": {"filled_shares": 0.1}, "b": {"filled_shares": -0.05}}},
        {"trade_date": "2020-01-03", "price_index_before": 102.0,
         "agents": {"a": {"filled_shares": 0.0}, "b": {"filled_shares": 0.0}}},
    ]
    return {"pipeline_version": REPLAY_VERSION, "data_kind": "observed",
            "market_dataset_id": "same-market", "replay_id": "replay-1",
            "agents": {"a": {"initial_cash": 100.0}, "b": {"initial_cash": 100.0}},
            "paths": {"000001": {"market_signal": {"trace": trace},
                                 "zero_signal": {"trace": trace}}}}


def activity_fixture():
    return {("000001", "2020-01-02"): {"amount_cny": "1000", "trading_status": "trading"},
            ("000001", "2020-01-03"): {"amount_cny": "0", "trading_status": "suspended"}}


class CapacityDiagnosticTest(unittest.TestCase):
    def test_hypothetical_aum_scales_orders_and_flags_suspended_fills(self):
        result = compare_capacity(replay_fixture(), activity_fixture(), [100, 1000])
        small, large = (next(row for row in result["results"]
                             if row["path"] == "market_signal" and row["aum_cny_per_agent"] == aum)
                        for aum in (100, 1000))
        self.assertAlmostEqual(small["daily"][0]["hypothetical_gross_cny"], 15.0)
        self.assertAlmostEqual(small["daily"][0]["fraction_of_observed_amount"], 0.015)
        self.assertAlmostEqual(large["daily"][0]["fraction_of_observed_amount"], 0.15)
        self.assertEqual(small["days_above_fraction"]["0.01"], 1)
        self.assertEqual(small["suspended_with_model_fill_days"], 0)
        self.assertIsNone(small["daily"][1]["fraction_of_observed_amount"])
        suspended_fill = activity_fixture()
        replay = replay_fixture()
        for path in replay["paths"]["000001"].values():
            path["trace"][1]["agents"]["a"]["filled_shares"] = 0.01
        flagged = compare_capacity(replay, suspended_fill, [100])
        self.assertEqual(flagged["results"][0]["suspended_with_model_fill_days"], 1)

    def test_missing_observed_date_and_manifest_tampering_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            compare_capacity(replay_fixture(), {}, [100])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay_dir, activity_dir, config_dir = (root / name for name in ("replay", "activity", "config"))
            for path in (replay_dir, activity_dir, config_dir):
                path.mkdir()
            replay_path = replay_dir / "historical_replay.json"
            replay_path.write_text(json.dumps(replay_fixture()), encoding="utf-8")
            (replay_dir / "historical_replay_manifest.json").write_text(json.dumps({
                "pipeline_version": REPLAY_VERSION, "replay_id": "replay-1",
                "artifacts": {replay_path.name: {"sha256": file_sha256(replay_path)}}}), encoding="utf-8")
            activity_path = activity_dir / "market_activity.csv"
            with activity_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["trade_date", "stock_code", "provider_symbol",
                                                            "volume_shares", "amount_cny", "trading_status"])
                writer.writeheader()
                for (code, day), row in activity_fixture().items():
                    writer.writerow({"trade_date": day, "stock_code": code, "provider_symbol": "sz.000001",
                                     "volume_shares": 1, **row})
            (activity_dir / "activity_manifest.json").write_text(json.dumps({
                "pipeline_version": ACTIVITY_VERSION, "data_kind": "observed",
                "market_dataset_id": "same-market", "units": {"amount_cny": "CNY"},
                "artifacts": {activity_path.name: {"sha256": file_sha256(activity_path)}}}), encoding="utf-8")
            config_path = config_dir / "capacity.json"
            config_path.write_text(json.dumps({"run_id": "synthetic-test", "data_kind": "observed_posthoc",
                                               "replay_manifest": "../replay/historical_replay_manifest.json",
                                               "activity_manifest": "../activity/activity_manifest.json",
                                               "aum_cny_per_agent": [100]}), encoding="utf-8")
            result = run_diagnostic(config_path, root / "output")
            self.assertEqual(len(result["results"]), 2)
            activity_path.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs from its manifest"):
                run_diagnostic(config_path, root / "output2")


if __name__ == "__main__":
    unittest.main()
