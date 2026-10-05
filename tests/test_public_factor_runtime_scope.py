import copy
import unittest
from pathlib import Path
from research.simulation.public_factor_runtime_scope import (RENDERER, HISTORICAL, OBSERVED,
    allowed_nonexecuted_renderer, validate_declaration)


class PublicRuntimeScopeTest(unittest.TestCase):
    def test_exception_requires_exact_path_and_both_exact_hashes(self):
        self.assertTrue(allowed_nonexecuted_renderer(RENDERER, HISTORICAL, OBSERVED))
        for p, old, new in ((Path("D:/another/pdftoppm.exe"), HISTORICAL, OBSERVED),
                            (RENDERER, OBSERVED, HISTORICAL), (RENDERER, HISTORICAL, "new-unknown"),
                            (Path("research/simulation/portfolio_market.py"), HISTORICAL, OBSERVED)):
            self.assertFalse(allowed_nonexecuted_renderer(p, old, new))

    def test_scope_cannot_authorize_pdf_processing_or_numerical_protocol_edits(self):
        cfg = {"version": "public-factor-nonexecuted-renderer-scope-v2",
            "scope": "NUMERIC_SIMULATION_FROM_FROZEN_CSV_JSON_GZIP_ONLY", "renderer_path": str(RENDERER),
            "historical_sha256": HISTORICAL, "observed_sha256": OBSERVED,
            "allow_new_pdf_parsing_or_rendering": False, "allow_numeric_simulation": True, "alter_numerical_protocol": False}
        validate_declaration(cfg)
        for key, value in (("allow_new_pdf_parsing_or_rendering", True), ("alter_numerical_protocol", True),
                            ("observed_sha256", "unknown"), ("scope", "ALL_FILES")):
            changed = copy.deepcopy(cfg)
            changed[key] = value
            with self.assertRaises(ValueError):
                validate_declaration(changed)

    def test_v2_production_run_is_identical_ast_to_frozen_v1(self):
        import ast
        root = Path(__file__).resolve().parents[1] / "research/simulation"
        def read(filename):
            tree = ast.parse((root / filename).read_text(encoding="utf-8"))
            return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
        self.assertEqual(ast.dump(read("run_pre_wuhan_public_factor_channels_2019.py")),
                         ast.dump(read("run_pre_wuhan_public_factor_channels_2019_v2.py")))
