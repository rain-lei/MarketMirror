import json
import tempfile
import unittest
from pathlib import Path

from research.data_pipeline.provenance import file_sha256
from research.semantic.agent_signal_adapter import GATE_SCOPE, VERSION as ADAPTER_VERSION
from research.simulation.semantic_signal_join import (
    VERSION,
    join_signal_directory,
    join_steps,
    load_verified_signal_stream,
)


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def signal_row(item_id, available_at, signal, stock_code="000001"):
    return {"item_id": f"{item_id:064x}", "stock_code": stock_code, "available_at": available_at,
            "stage": "question", "source_text_sha256": f"{item_id + 10:064x}",
            "model_id": "model", "prompt_version": "prompt", "text_signal": signal,
            "uncertainty": 0.2, "event_count": 1, "parse_error": None,
            "text_evidence": f"sha256:{item_id + 10:064x}"}


def make_stream(directory, passed=True):
    rows_path = directory / "agent_signal_rows.jsonl"
    result_path = directory / "agent_signal_result.json"
    report_path = directory / "agent_signal_report.md"
    rows = [signal_row(1, "2020-01-02T10:00:00+08:00", 0.5),
            signal_row(2, "2020-01-04T10:00:00+08:00", -0.8)]
    write_jsonl(rows_path, rows)
    result_path.write_text(json.dumps({
        "pipeline_version": ADAPTER_VERSION,
        "gate": {"passed": passed, "checks": {"complete_independent_review": passed}, "scope": GATE_SCOPE},
    }), encoding="utf-8")
    report_path.write_text("fixture", encoding="utf-8")
    manifest = {"pipeline_version": ADAPTER_VERSION,
                "artifacts": {path.name: {"sha256": file_sha256(path)}
                              for path in (rows_path, result_path, report_path)}}
    (directory / "agent_signal_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


class SemanticSignalJoinTest(unittest.TestCase):
    def test_join_uses_only_text_visible_before_cutoff(self):
        rows = [signal_row(1, "2020-01-02T10:00:00+08:00", 0.5),
                signal_row(2, "2020-01-04T10:00:00+08:00", -0.8)]
        steps = [{"trade_date": "2020-01-05", "signal_cutoff_date": "2020-01-03"},
                 {"trade_date": "2020-01-08", "signal_cutoff_date": "2020-01-07"}]
        joined = join_steps(steps, "000001", rows)
        self.assertEqual(joined[0]["text_signal"], 0.5)
        self.assertEqual(joined[0]["semantic_item_count"], 1)
        self.assertAlmostEqual(joined[1]["text_signal"], -0.15)
        self.assertEqual(joined[1]["semantic_item_count"], 2)
        self.assertNotEqual(joined[0]["text_evidence"], "semantic:none-through:2020-01-03")

    def test_stream_requires_passed_gate_and_binds_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signal_dir = root / "signals"
            signal_dir.mkdir()
            make_stream(signal_dir)
            rows, result = load_verified_signal_stream(signal_dir)
            self.assertEqual(len(rows), 2)
            self.assertTrue(result["gate"]["passed"])
            steps_path = root / "steps.json"
            steps_path.write_text(json.dumps([{"trade_date": "2020-01-05",
                                               "signal_cutoff_date": "2020-01-03"}]), encoding="utf-8")
            output = root / "joined.json"
            payload = join_signal_directory(steps_path, signal_dir, "000001", output)
            self.assertEqual(payload["pipeline_version"], VERSION)
            self.assertEqual(payload["steps"][0]["semantic_item_count"], 1)
            with self.assertRaisesRegex(ValueError, "cannot replace"):
                join_signal_directory(steps_path, signal_dir, "000001", signal_dir / "joined.json")

    def test_stream_rejects_failed_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            signal_dir = Path(temporary)
            make_stream(signal_dir, passed=False)
            with self.assertRaisesRegex(ValueError, "has not passed"):
                load_verified_signal_stream(signal_dir)

    def test_future_cutoff_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "precede"):
            join_steps([{"trade_date": "2020-01-05", "signal_cutoff_date": "2020-01-05"}],
                       "000001", [])


if __name__ == "__main__":
    unittest.main()
