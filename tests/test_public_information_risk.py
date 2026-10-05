import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_fixed_marginal_risk import fixture
from research.simulation import fixed_marginal_risk as fixed
from research.simulation.public_information_risk import (
    clipped_uniform_second_moment, received_budget, build_path, message_receipt, validate_issuer_valuation, with_public_delivery,
)
from research.simulation.audit_public_information_risk import independent_second_moment, verify_budget, verify_receipt


def numerical_moment(a, b, cap):
    # Independent numerical convolution density, with no production moment formula.
    h, low = max(a, b) * math.sqrt(3), min(a, b) * math.sqrt(3)
    if low == 0:
        raise ValueError("numerical check uses two nonzero amplitudes")
    count = 20000
    step = (h + low) / count
    def value(x):
        overlap = max(0.0, min(h, x + low) - max(-h, x - low))
        return 2 * min(x * x, cap * cap) * overlap / (4 * h * low)
    return step / 3 * (value(0) + value(h + low)
        + sum((4 if i % 2 else 2) * value(i * step) for i in range(1, count)))


class PublicInformationRiskTests(unittest.TestCase):
    def test_independent_decimal_integral_and_forged_budget_detection(self):
        for a, b, cap in [(2, 3, 100), (2, 3, 1), (2, 3, 4), (10, .1, 2), (2, 2, 3)]:
            self.assertAlmostEqual(independent_second_moment(a, b, cap), numerical_moment(a, b, cap),
                                   delta=max(1, cap * cap) * 2e-7)
        risk, features, parameters = fixture()
        path = build_path(risk, features, 'audit', parameters, 'market_shared', 'common', 'public')
        row = path['by_stock']['a'][0]
        verify_budget(row, parameters)
        for field in ('public_market_multiplier', 'baseline_received_second_moment_bps2', 'matching_tolerance_bps2'):
            modified = copy.deepcopy(row)
            modified['public_information_budget'][field] += .01
            with self.assertRaises(ValueError):
                verify_budget(modified, parameters)

    def test_coverage_adapter_preserves_original_and_rejects_unknown_value_forgery(self):
        risk, features, parameters = fixture()
        old = fixed.build_path(risk, features, 'audit', parameters, 'market_independent', 'common')
        preserved = copy.deepcopy(old)
        new = with_public_delivery(old)
        self.assertEqual(old, preserved)
        self.assertEqual(new, build_path(risk, features, 'audit', parameters, 'market_independent', 'common', 'public'))
        with self.assertRaises(ValueError):
            with_public_delivery(new)
        row = new['by_stock']['a'][0]
        unknown = next(message_receipt(row, parameters, 'a', str(i)) for i in range(100)
                       if not message_receipt(row, parameters, 'a', str(i))['public_information_receipt']['private_received'])
        actor = next(str(i) for i in range(100) if message_receipt(row, parameters, 'a', str(i)) == unknown)
        verify_receipt(unknown, row, parameters, 'a', actor)
        unknown['public_information_receipt']['private_raw_shift_bps'] = 0
        with self.assertRaises(ValueError):
            verify_receipt(unknown, row, parameters, 'a', actor)
        with self.assertRaises(ValueError):
            validate_issuer_valuation(['a'], [], None)

    def test_post_cap_moment_against_independent_density_quadrature(self):
        for a, b, cap in [(2, 3, 100), (2, 3, 1), (2, 3, 4), (10, 0.1, 2), (2, 2, 3)]:
            with self.subTest(a=a, b=b, cap=cap):
                expected = numerical_moment(a, b, cap)
                self.assertAlmostEqual(clipped_uniform_second_moment(a, b, cap), expected, delta=max(1, expected) * 2e-7)

    def test_uncapped_matching_is_sqrt_probability_and_cap_changes_it(self):
        result = received_budget(20, 30, .25, 500)
        self.assertAlmostEqual(result["public_market_multiplier"], .5, places=12)
        clipped = received_budget(200, 300, .25, 50)
        self.assertNotAlmostEqual(clipped["public_market_multiplier"], .5, places=3)
        self.assertLessEqual(clipped["matching_absolute_error_bps2"], clipped["matching_tolerance_bps2"])

    def test_probability_zero_one_and_zero_market_component(self):
        self.assertEqual(received_budget(20, 30, 0, 50)["public_market_multiplier"], 0)
        self.assertEqual(received_budget(20, 30, 1, 50)["public_market_multiplier"], 1)
        self.assertEqual(received_budget(20, 0, .5, 50)["public_market_multiplier"], 1)
        self.assertEqual(clipped_uniform_second_moment(0, 0, 50), 0)

    def test_nonfinite_boolean_and_invalid_probabilities_rejected(self):
        for args in [(True, 2, .5, 50), (1, float("nan"), .5, 50), (1, 2, 1.1, 50), (1, 2, .5, 0)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                received_budget(*args)

    def test_private_masks_stay_exact_and_unknown_residual_stays_null(self):
        risk, features, p = fixture()
        old = fixed.build_path(risk, features, "study", p, "market_shared", "common")
        new = build_path(risk, features, "study", p, "market_shared", "common", "public")
        seen = set()
        for i in range(32):
            first = fixed.message_receipt(old["by_stock"]["a"][0], p, "a", str(i))
            second = message_receipt(new["by_stock"]["a"][0], p, "a", str(i))
            details = second["public_information_receipt"]
            seen.add(details["private_received"])
            self.assertEqual(first["received"], details["private_received"])
            self.assertEqual(first["receipt_sha256"], second["receipt_sha256"])
            self.assertTrue(details["public_received"])
            if not first["received"]:
                self.assertIsNone(details["private_raw_shift_bps"])
        self.assertEqual(seen, {False, True})

    def test_masked_bridge_is_exact_and_forged_or_future_budget_rejected(self):
        risk, features, p = fixture()
        masked = build_path(risk, features, "study", p, "market_shared", "common", "masked")
        self.assertEqual(masked, fixed.build_path(risk, features, "study", p, "market_shared", "common"))
        public = build_path(risk, features, "study", p, "market_shared", "common", "public")
        calendar = [("2019-10-28", "2019-10-24", "2019-10-25")]
        self.assertIs(public, validate_issuer_valuation(["a", "b"], calendar, public))
        for field in ("budget", "future"):
            changed = copy.deepcopy(public)
            if field == "budget":
                changed["by_stock"]["a"][0]["public_information_budget"]["public_market_multiplier"] += .1
            else:
                changed["by_stock"]["a"][0]["budget_factor_feature"]["history"][-1]["trade_date"] = "2019-10-29"
            with self.assertRaises(ValueError):
                validate_issuer_valuation(["a", "b"], calendar, changed)

    def test_negative_exposure_sign_and_independent_shared_marginal_budget(self):
        risk, features, p = fixture([.03, .01, -.01, -.03])
        paths = [build_path(risk, features, "study", p, mode, "common", "public")
                 for mode in ("market_independent", "market_shared")]
        for path in paths:
            row = path["by_stock"]["a"][0]
            self.assertLess(row["risk_budget"]["signed_market_amplitude_bps"], 0)
        self.assertEqual(paths[0]["by_stock"]["a"][0]["public_information_budget"],
                         paths[1]["by_stock"]["a"][0]["public_information_budget"])

    def test_complete_execution_bridge_and_public_settlement(self):
        from test_public_factor_channels import fixture as execution_fixture, initial
        from research.simulation.issuer_valuation import build_lagged_risk
        from research.simulation.public_market_factor import build_features
        from research.simulation.portfolio_fixed_marginal_risk import simulate_portfolio as old_simulate
        from research.simulation.portfolio_public_information_risk import simulate_portfolio
        from research.simulation.portfolio_audit import audit_portfolio_day
        from research.simulation.audit_own_price_feedback import verify_final_resources
        from research.simulation.audit_public_information_channels import verify_day
        args, shocks, industry, response, source, issuer, factor = execution_fixture()
        risk = build_lagged_risk(source, args[0], issuer["parameters"]["history_window"])
        features = build_features(source, args[0], factor["parameters"])
        for mode in ("market_independent", "market_shared"):
            masked = build_path(risk, features, "execution", issuer["parameters"], mode, "common", "masked")
            public = build_path(risk, features, "execution", issuer["parameters"], mode, "common", "public")
            for anchor in (None, {"background_anchor": "current_inventory", "strategy_wait": "original"}):
                for scale in (0., 1.):
                    kwargs = dict(scenario_shocks=shocks, industry_shocks=industry, background_response=response,
                                  quantity_controls=anchor, target_feedback_scale=scale, quote_feedback_scale=scale)
                    self.assertEqual(old_simulate(*args, issuer_valuation=masked, **kwargs),
                                     simulate_portfolio(*args, issuer_valuation=masked, **kwargs))
                    result = simulate_portfolio(*args, issuer_valuation=public, **kwargs)
                    state = initial(args, result["participant_specs"])
                    streaks = {name: {'sign': 0, 'streak': 0} for name, spec in result['participant_specs'].items()
                               if spec['kind'] == 'strategy'}
                    for i, day in enumerate(result["trace"]):
                        verify_day(day, state, result['participant_specs'], args[4], args[3], response, shocks, industry,
                                   public, anchor, args[6], streaks, i, scale, scale)
                        state = audit_portfolio_day(day, state, args[4], i, dense=True)
                        for receipts in day["issuer_information_receipts"].values():
                            for receipt in receipts.values():
                                self.assertTrue(receipt["public_information_receipt"]["public_received"])
                    verify_final_resources(state, result["summary"])


if __name__ == "__main__":
    unittest.main()
