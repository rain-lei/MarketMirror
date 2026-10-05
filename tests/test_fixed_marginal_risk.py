import ast
import copy
import math
from pathlib import Path
import statistics
import unittest

from research.simulation.fixed_marginal_risk import (build_path, validate_issuer_valuation, budget_coefficients, message_receipt)
from research.simulation.issuer_valuation import history_hash, build_issuer_valuation
from research.simulation.public_market_factor import feature

def fixture(values=None, market=None, probability=5000, cap=500):
    market = market if market is not None else [-.03, -.01, .01, .03]
    values = values if values is not None else [.01, -.02, .03, -.02]
    dates = ["2019-10-" + str(i) for i in (21, 22, 23, 24)]
    risk_history = [{"trade_date": d, "stock_return": y} for d, y in zip(dates, values)]
    history = [{**r, "market_return": x, "benchmark_id": "sh.000300"} for r, x in zip(risk_history, market)]
    risk = {"trade_date": "2019-10-28", "signal_cutoff_date": dates[-1], "history": risk_history,
        "history_sha256": history_hash(risk_history), "lagged_stock_volatility": statistics.stdev(values)}
    factor = feature(history, risk["trade_date"], dates[-1], 4, "sh.000300")
    p = {"history_window": 4, "risk_multiplier": 1.0, "belief_scale_bps": 1000, "max_shift_bps": cap,
        "information_probability_bps": probability, "innovation_seed": "test-private", "information_seed": "test-receipt"}
    return {"a": [risk], "b": [copy.deepcopy(risk)]}, {"a": [factor], "b": [copy.deepcopy(factor)]}, p

class FixedMarginalRiskTests(unittest.TestCase):
    def test_budget_preserves_each_source_amplitude_and_shared_covariance_sign(self):
        risk, factors, p = fixture()
        b = budget_coefficients(risk["a"][0], factors["a"][0], p)
        total = (10000 * statistics.stdev([.01, -.02, .03, -.02])) ** 2
        self.assertAlmostEqual(total, b["signed_market_amplitude_bps"] ** 2 + b["residual_amplitude_bps"] ** 2)
        self.assertGreater(b["residual_amplitude_bps"], 0)
        negative_risk, negative_features, p = fixture([.03, .01, -.01, -.03])
        n = budget_coefficients(negative_risk["a"][0], negative_features["a"][0], p)
        self.assertLess(n["signed_market_amplitude_bps"], 0)
        self.assertEqual(0, n["residual_amplitude_bps"])

    def test_independent_and_shared_controls_preserve_coefficients_and_private_draws(self):
        risk, features, p = fixture()
        independent = build_path(risk, features, "experiment", p, "market_independent", "test-common")
        shared = build_path(risk, features, "experiment", p, "market_shared", "test-common")
        for stock in risk:
            a, b = independent["by_stock"][stock][0], shared["by_stock"][stock][0]
            self.assertEqual(a["innovation_sha256"], b["innovation_sha256"])
            self.assertEqual(a["risk_budget"]["residual_raw_shift_bps"], b["risk_budget"]["residual_raw_shift_bps"])
            self.assertEqual(a["risk_budget"]["pre_cap_variance_bps2"], b["risk_budget"]["pre_cap_variance_bps2"])
        self.assertEqual(shared["by_stock"]["a"][0]["risk_budget"]["used_market_innovation_sha256"], shared["by_stock"]["b"][0]["risk_budget"]["used_market_innovation_sha256"])
        self.assertNotEqual(independent["by_stock"]["a"][0]["risk_budget"]["used_market_innovation_sha256"], independent["by_stock"]["b"][0]["risk_budget"]["used_market_innovation_sha256"])

    def test_missing_receipts_remain_missing_with_same_mask_in_both_controls(self):
        risk, features, p = fixture()
        paths = [build_issuer_valuation(risk, "experiment", p)] + [build_path(risk, features, "experiment", p, m, "test-common") for m in ("market_independent", "market_shared")]
        seen = set()
        for i in range(32):
            receipts = [message_receipt(path["by_stock"]["a"][0], p, "a", "actor" + str(i)) for path in paths]
            self.assertEqual(1, len({(r["received"], r["receipt_sha256"]) for r in receipts}))
            seen.add(receipts[0]["received"])
            for r in receipts:
                if not r["received"]:
                    self.assertIsNone(r["valuation_shift_bps"])
                    self.assertEqual(0.0, r["applied_valuation_shift_bps"])
        self.assertEqual({True, False}, seen)

    def test_zero_and_full_information_boundary_do_not_change_latent_draws(self):
        risk, features, p = fixture(probability=0)
        absent = build_path(risk, features, "experiment", p, "market_shared", "test-common")
        p2 = {**p, "information_probability_bps": 10000}
        present = build_path(risk, features, "experiment", p2, "market_shared", "test-common")
        self.assertEqual(absent["by_stock"], present["by_stock"])
        self.assertFalse(message_receipt(absent["by_stock"]["a"][0], p, "a", "actor")["received"])
        self.assertTrue(message_receipt(present["by_stock"]["a"][0], p2, "a", "actor")["received"])

    def test_zero_stock_variance_is_known_zero_and_zero_index_fails(self):
        risk, features, p = fixture([0, 0, 0, 0])
        path = build_path(risk, features, "experiment", p, "market_shared", "test-common")
        self.assertEqual(0.0, path["by_stock"]["a"][0]["raw_shift_bps"])
        with self.assertRaises(ValueError):
            fixture(market=[0, 0, 0, 0])

    def test_clipping_is_after_combination_and_raw_components_remain_available(self):
        risk, features, p = fixture([.1, -.2, .3, -.2], cap=1)
        path = build_path(risk, features, "experiment", p, "market_shared", "test-common")
        row = path["by_stock"]["a"][0]
        self.assertTrue(row["was_capped"])
        self.assertAlmostEqual(row["raw_shift_bps"], row["risk_budget"]["residual_raw_shift_bps"] + row["risk_budget"]["market_raw_shift_bps"])
        self.assertEqual(1, abs(row["valuation_shift_bps"]))

    def test_future_history_extra_target_fields_or_tampering_are_rejected(self):
        risk, features, p = fixture()
        path = build_path(risk, features, "experiment", p, "market_shared", "test-common")
        calendar = [("2019-10-28", "2019-10-24", "2019-10-25")]
        self.assertIs(path, validate_issuer_valuation(["a", "b"], calendar, path))
        for mutate in (lambda r: r.update(raw_shift_bps=r["raw_shift_bps"] + 1),
            lambda r: r.update(observed_return=.9),
            lambda r: r["budget_factor_feature"]["history"][-1].update(trade_date="2019-10-29"),
            lambda r: r["risk_budget"].update(residual_amplitude_bps=0)):
            changed = copy.deepcopy(path)
            mutate(changed["by_stock"]["a"][0])
            with self.assertRaises(ValueError):
                validate_issuer_valuation(["a", "b"], calendar, changed)

    def test_execution_loop_only_redirects_issuer_validator_and_legacy_is_exact(self):
        root = Path(__file__).resolve().parents[1]
        old = ast.parse((root / "research/simulation/portfolio_public_factor_channels.py").read_text(encoding="utf-8"))
        new = ast.parse((root / "research/simulation/portfolio_fixed_marginal_risk.py").read_text(encoding="utf-8"))
        old_fn = next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == "simulate_portfolio")
        new_fn = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == "simulate_portfolio")
        for node in ast.walk(new_fn):
            if isinstance(node, ast.ImportFrom) and node.module == "fixed_marginal_risk":
                node.module = "issuer_valuation"
        self.assertEqual(ast.dump(old_fn, include_attributes=False), ast.dump(new_fn, include_attributes=False))
        risk, _, p = fixture()
        path = build_issuer_valuation(risk, "legacy", p)
        self.assertIs(path, validate_issuer_valuation(["a", "b"], [("2019-10-28", "2019-10-24", "2019-10-25")], path))

if __name__ == "__main__":
    unittest.main()
