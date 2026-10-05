import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.data_pipeline.provenance import file_sha256
from research.simulation.semantic_memory_sensitivity import canonical_hash
from research.simulation.wuhan_v3_portfolio_ablation import (
    ARTIFACTS, CODE_PATHS, VERSION, _check_cohort, load_summary, run_ablation)


class WuhanV3PortfolioAblationTest(unittest.TestCase):
    def test_failed_independent_gate_prevents_market_loading_and_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("research.simulation.wuhan_v3_portfolio_ablation.load_verified_signal_stream",
                       side_effect=ValueError("independent gate failed")), \
                 patch("research.simulation.wuhan_v3_portfolio_ablation._load_inputs") as market:
                with self.assertRaisesRegex(ValueError, "independent gate failed"):
                    run_ablation(*(root / name for name in (
                        "config", "baseline", "pack", "raw", "normal", "score", "signals", "output")))
                market.assert_not_called()
                self.assertFalse((root / "output").exists())

    def test_cohort_identity_and_all_24_missing_replies_are_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath("universe.json").write_text(json.dumps({
                "selection": {"selection_rule": "fixture"},
                "stock_codes": [f"{index:06d}" for index in range(126)]}), encoding="utf-8")
            config = root / "config.json"
            config.write_text(json.dumps({"universe_config": "universe.json"}), encoding="utf-8")
            joined = {f"{index:06d}": [] for index in range(126)}
            items = {f"item-{index}": {"stock_code": f"{index:06d}"} for index in range(102)}
            rows = [{"stock_code": f"{index:06d}"} for index in range(102)]
            manifest = {"universe_size": 126, "universe_selection": {"selection_rule": "fixture"}}
            with patch("research.simulation.wuhan_v3_portfolio_ablation.verify_pack",
                       return_value=(items, manifest)):
                _check_cohort(root / "pack", config, joined, rows)
                with self.assertRaisesRegex(ValueError, "frozen 126-company"):
                    _check_cohort(root / "pack", config, joined, rows[:-1])
                with self.assertRaisesRegex(ValueError, "frozen 126-company"):
                    _check_cohort(root / "pack", config, joined, rows[:-1] + [{"stock_code": "999999"}])

    def test_archived_paired_summary_rejects_changed_result_or_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.json"
            source.write_text("{}\n", encoding="utf-8")
            inputs = {str(source): file_sha256(source)}
            code = {name: file_sha256(path) for name, path in CODE_PATHS.items()}
            experiment_id = canonical_hash({"inputs": inputs, "code_sha256": code})
            (root / "v3_portfolio_summary.json").write_text(json.dumps({
                "pipeline_version": VERSION, "experiment_id": experiment_id,
                "gate": {"passed": True}}), encoding="utf-8")
            (root / "v3_portfolio_paths.json").write_text("[]\n", encoding="utf-8")
            (root / "v3_portfolio_report.md").write_text("fixture\n", encoding="utf-8")
            (root / "v3_portfolio_manifest.json").write_text(json.dumps({
                "pipeline_version": VERSION, "experiment_id": experiment_id,
                "inputs": inputs, "code_sha256": code,
                "artifacts": {name: {"sha256": file_sha256(root / name)} for name in ARTIFACTS}}),
                encoding="utf-8")
            self.assertEqual(load_summary(root)["experiment_id"], experiment_id)
            (root / "v3_portfolio_paths.json").write_text("[{}]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact differs"):
                load_summary(root)
            (root / "v3_portfolio_paths.json").write_text("[]\n", encoding="utf-8")
            source.write_text("{\"changed\":true}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source, code or artifact"):
                load_summary(root)


if __name__ == "__main__":
    unittest.main()
