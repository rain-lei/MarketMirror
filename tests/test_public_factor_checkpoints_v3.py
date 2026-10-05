import ast
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.simulation.public_factor_numeric_inputs_v3 import separate, verify_numeric, RENDERER
from research.simulation.run_public_factor_checkpoints_v3 import combined_gzip, verify_checkpoint


class PublicCheckpointTest(unittest.TestCase):
    def test_complete_condition_body_matches_the_frozen_uninterrupted_producer(self):
        root = Path(__file__).resolve().parents[1] / "research/simulation"
        original = ast.parse((root / "run_pre_wuhan_public_factor_channels_2019_v2.py").read_text(encoding="utf-8"))
        old = next(n for n in ast.walk(original) if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "cell")
        current = ast.parse((root / "public_factor_condition_worker_v3.py").read_text(encoding="utf-8"))
        new = next(n for n in ast.walk(current) if isinstance(n, ast.With))
        self.assertEqual([ast.dump(n) for n in old.body], [ast.dump(n) for n in new.body[:-1]])

    def test_numeric_scope_separates_only_exact_nonexecuted_dependency(self):
        bindings = {str(RENDERER): "history", "D:/different/pdftoppm.exe": "not-excluded", "research/simulation/portfolio_market.py": "core"}
        numeric, historical = separate(bindings)
        self.assertEqual({str(RENDERER): "history"}, historical)
        self.assertEqual(2, len(numeric))
        with self.assertRaisesRegex(ValueError, "explicitly separated"):
            verify_numeric({str(RENDERER): "history"})
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "source.csv"
            p.write_text("different", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "input/code changed"):
                verify_numeric({str(p): "wrong"})

    def test_partitioned_stream_merges_byte_identically_to_one_uninterrupted_gzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values = [b'{"a":1}\n' * 13579, b'', b'{"b":2}\n' * 9001]
            chunks = []
            for i, value in enumerate(values):
                p = root / f"chunk{i}.gz"
                with p.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as stream:
                    stream.write(value)
                chunks.append(p)
            expected, actual = root / "expected.gz", root / "actual.gz"
            with expected.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as stream:
                for value in values:
                    for line in value.splitlines(keepends=True):
                        stream.write(line)
            combined_gzip(actual, chunks)
            self.assertEqual(expected.read_bytes(), actual.read_bytes())

    def test_checkpoint_rejects_partial_extra_and_tampered_data(self):
        from research.data_pipeline.provenance import file_sha256
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg, cell = root / "config.json", root / "cell"
            cfg.write_text('{}', encoding="utf-8")
            cell.mkdir()
            data = cell / "condition.json"
            data.write_text('{"valid":true}', encoding="utf-8")
            receipt = {"identity": ["seed", "condition"], "execution_config_sha256": file_sha256(cfg),
                       "artifacts": {"condition.json": file_sha256(data)}}
            (cell / "checkpoint.json").write_text(json.dumps(receipt), encoding="utf-8")
            with patch("research.simulation.run_public_factor_checkpoints_v3.CONFIG_V3", cfg):
                verify_checkpoint(cell, ["seed", "condition"])
                with self.assertRaises(ValueError):
                    verify_checkpoint(cell, ["seed", "wrong"])
                data.write_text('{}', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "changed"):
                    verify_checkpoint(cell, ["seed", "condition"])
                extra = cell / "unfinished.tmp"
                extra.write_text("partial", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "undeclared"):
                    verify_checkpoint(cell, ["seed", "condition"])
