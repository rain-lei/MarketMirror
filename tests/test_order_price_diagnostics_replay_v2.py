import ast
from pathlib import Path
import unittest

from research.simulation.run_pre_wuhan_order_price_diagnostics_replay_v2 import (
    RENDER_PATH, HISTORICAL_RENDER_SHA256, OBSERVED_RENDER_SHA256, allowed_historical_renderer, run)

ROOT = Path(__file__).resolve().parents[1]


def functions(name):
    tree = ast.parse((ROOT / "research/simulation" / name).read_text(encoding="utf-8"))
    return {n.name: ast.dump(n, include_attributes=False) for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


class OrderPriceReplayV2Test(unittest.TestCase):
    def test_only_declared_native_path_and_both_exact_hashes_can_differ(self):
        self.assertTrue(allowed_historical_renderer(RENDER_PATH, HISTORICAL_RENDER_SHA256, OBSERVED_RENDER_SHA256))
        for path, old, current in ((ROOT / "research_outputs/ledger.jsonl.gz", HISTORICAL_RENDER_SHA256, OBSERVED_RENDER_SHA256),
                                   (RENDER_PATH, "0" * 64, OBSERVED_RENDER_SHA256),
                                   (RENDER_PATH, HISTORICAL_RENDER_SHA256, "0" * 64),
                                   (Path(str(RENDER_PATH) + ".replacement"), HISTORICAL_RENDER_SHA256, OBSERVED_RENDER_SHA256)):
            self.assertFalse(allowed_historical_renderer(path, old, current))

    def test_price_order_aggregation_and_source_read_algorithms_are_unchanged(self):
        a = functions("run_pre_wuhan_order_price_diagnostics_2019.py")
        b = functions("run_pre_wuhan_order_price_diagnostics_replay_v2.py")
        for name in ("encoded", "load_source", "source_books", "diagnostic_rows", "condition_measures"):
            self.assertEqual(a[name], b[name])
        old = functions("verify_pre_wuhan_order_price_diagnostics_2019.py")
        new = functions("verify_pre_wuhan_order_price_diagnostics_2019_v2.py")
        for name in ("independent_book", "add_quantities", "IndependentTotals", "independent_measures", "compare"):
            self.assertEqual(old[name], new[name])

    def test_v2_reader_cannot_start_fresh_production(self):
        with self.assertRaisesRegex(ValueError, "restricted to replay"):
            run(ROOT / "research_outputs/forbidden_fresh_diagnostics", False)


if __name__ == "__main__":
    unittest.main()
