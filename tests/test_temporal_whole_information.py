import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_fixed_marginal_risk import fixture
from test_public_factor_channels import fixture as execution_fixture
from test_public_information_risk import numerical_moment
from research.simulation.issuer_valuation import build_lagged_risk, message_receipt as private_receipt
from research.simulation.public_market_factor import build_features
from research.simulation.temporal_residual_coupling import build_path as previous_path
from research.simulation.portfolio_temporal_residual_coupling import simulate_portfolio as previous_simulate
from research.simulation.temporal_whole_information_risk import (build_path, received_budget, with_whole_delivery,
    validate_issuer_valuation, message_receipt, respond_issuer_background, subset)
from research.simulation.audit_temporal_whole_information import verify_budget, verify_receipt, expected_receipt
from research.simulation.temporal_whole_information_study import CELLS, path_key
from research.simulation.portfolio_temporal_whole_information import simulate_portfolio


class WholeInformationTests(unittest.TestCase):
    def test_two_raw_components_match_independent_integral_under_caps(self):
        for residual, market, p, cap in ((20, 30, .25, 500), (200, 300, .5, 50),
                (10, .1, .5, 2), (2, 2, .02, 3), (0, 400, .5, 50), (400, 0, .5, 50)):
            with self.subTest(residual=residual, market=market, p=p, cap=cap):
                budget = received_budget(residual, market, p, cap)
                row = {"risk_budget": {"residual_amplitude_bps": residual, "signed_market_amplitude_bps": -market},
                    "whole_information_budget": budget}
                verify_budget(row, {"information_probability_bps": p * 10000, "max_shift_bps": cap})
                self.assertLessEqual(budget["matching_absolute_error_bps2"], budget["matching_tolerance_bps2"])
                if residual and market:
                    scale = budget["whole_raw_multiplier"]
                    numerical = numerical_moment(scale * residual, scale * market, cap)
                    self.assertAlmostEqual(numerical, budget["baseline_received_second_moment_bps2"],
                        delta=max(1, cap * cap) * 2e-7)
        self.assertAlmostEqual(received_budget(20, 30, .25, 500)["whole_raw_multiplier"], .5, places=12)
        self.assertNotAlmostEqual(received_budget(200, 300, .5, 50)["whole_raw_multiplier"], math.sqrt(.5), places=3)

    def test_endpoints_zero_amplitudes_and_cache_copies(self):
        self.assertEqual(received_budget(20, 30, 0, 50)["whole_raw_multiplier"], 0)
        self.assertEqual(received_budget(20, 30, 1, 50)["whole_raw_multiplier"], 1)
        self.assertEqual(received_budget(0, 0, .5, 50)["whole_raw_multiplier"], 0)
        self.assertEqual(received_budget(0, 0, 1, 50)["whole_raw_multiplier"], 0)
        result = received_budget(20, 30, .5, 50)
        result["whole_raw_multiplier"] = 5
        self.assertLessEqual(received_budget(20, 30, .5, 50)["whole_raw_multiplier"], 1)

    def test_invalid_values_and_booleans_rejected_even_after_cache_hit(self):
        received_budget(1, 2, 1, 50)
        for inputs in ((True, 2, 1, 50), (1, 2, True, 50), (1, 2, 1.1, 50),
                (1, 2, .5, 0), (-1, 2, .5, 50), (1, math.nan, .5, 50), (1, math.inf, .5, 50)):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                received_budget(*inputs)

    def test_masked_reference_remains_unknown_while_public_message_is_received(self):
        risk, features, parameters = fixture()
        path = build_path(risk, features, "reference", parameters, "market_shared", "market", "whole_public", "residual")
        row = path["by_stock"]["a"][0]
        seen = set()
        for owner in map(str, range(32)):
            actual = message_receipt(row, parameters, "a", owner)
            reference = private_receipt(row, parameters, "a", owner)
            self.assertEqual(actual["whole_information_receipt"]["reference_private_receipt"], reference)
            self.assertTrue(actual["received"])
            self.assertEqual(actual, expected_receipt(row, parameters, "a", owner))
            verify_receipt(actual, row, parameters, "a", owner)
            seen.add(reference["received"])
            if not reference["received"]:
                self.assertIsNone(reference["valuation_shift_bps"])
                altered = copy.deepcopy(actual)
                altered["whole_information_receipt"]["reference_private_receipt"]["valuation_shift_bps"] = 0
                with self.assertRaises(ValueError):
                    verify_receipt(altered, row, parameters, "a", owner)
        self.assertEqual(seen, {False, True})

    def test_scaling_precedes_cap_and_both_raw_components_are_public(self):
        p = {"information_probability_bps": 5000, "max_shift_bps": 50, "belief_scale_bps": 100, "information_seed": "info"}
        row = {"trade_date": "2021-01-08", "valuation_shift_bps": 50,
            "risk_budget": {"residual_raw_shift_bps": 600, "market_raw_shift_bps": 400},
            "whole_information_budget": received_budget(200, 300, .5, 50)}
        result = message_receipt(row, p, "stock", "actor")
        multiplier = row["whole_information_budget"]["whole_raw_multiplier"]
        self.assertEqual(result["applied_valuation_shift_bps"], min(50, multiplier * 600 + multiplier * 400))
        self.assertNotEqual(result["applied_valuation_shift_bps"], multiplier * row["valuation_shift_bps"])

    def test_disabled_all_previous_controls_and_dependences_match_full_objects(self):
        args, shocks, industry, response, source, issuer, factor = execution_fixture()
        risk = build_lagged_risk(source, args[0], issuer["parameters"]["history_window"])
        features = build_features(source, args[0], factor["parameters"])
        for coupling in (None, "residual"):
            for mode in ("market_independent", "market_shared"):
                for delivery in ("masked", "public"):
                    path = build_path(risk, features, "disabled", issuer["parameters"], mode, "market", delivery, coupling)
                    self.assertEqual(path, previous_path(risk, features, "disabled", issuer["parameters"], mode, "market", delivery, coupling))
                    for anchor in (None, {"background_anchor": "current_inventory", "strategy_wait": "original"}):
                        for scale in (0.0, 1.0):
                            kwargs = dict(scenario_shocks=shocks, industry_shocks=industry, background_response=response,
                                issuer_valuation=path, quantity_controls=anchor, target_feedback_scale=scale, quote_feedback_scale=scale)
                            self.assertEqual(simulate_portfolio(*args, **kwargs), previous_simulate(*args, **kwargs))

    def test_original_rows_and_global_draws_preserved_for_both_dependences(self):
        risk, features, params = fixture()
        stocks = sorted(risk)
        calendar = [(r["trade_date"], r["signal_cutoff_date"], "unused") for r in risk[stocks[0]]]
        for coupling in (None, "residual"):
            for mode in ("market_independent", "market_shared"):
                original = previous_path(risk, features, "source", params, mode, "market", "masked", coupling)
                saved = copy.deepcopy(original)
                new = with_whole_delivery(original)
                self.assertEqual(original, saved)
                for stock in stocks:
                    for a, b in zip(new["by_stock"][stock], original["by_stock"][stock], strict=True):
                        self.assertEqual({k: v for k, v in a.items() if k != "whole_information_budget"}, b)
                        verify_budget(a, params)
                    split = subset(new, [stock])
                    self.assertEqual(validate_issuer_valuation([stock], calendar, split), split)
                with self.assertRaises(ValueError):
                    with_whole_delivery(new)

    def test_forged_schema_budget_reference_and_future_source_rejected(self):
        risk, features, params = fixture()
        params = {**params, "information_probability_bps": 10000}
        stocks = sorted(risk)
        calendar = [(r["trade_date"], r["signal_cutoff_date"], "unused") for r in risk[stocks[0]]]
        path = build_path(risk, features, "validate", params, "market_shared", "market", "whole_public", "residual")
        for key in ("multiplier", "tolerance", "bool", "future", "extra", "delivery"):
            altered = copy.deepcopy(path)
            row = altered["by_stock"][stocks[0]][0]
            if key == "multiplier":
                row["whole_information_budget"]["whole_raw_multiplier"] = .9
            elif key == "tolerance":
                row["whole_information_budget"]["matching_tolerance_bps2"] = 1e9
            elif key == "bool":
                row["whole_information_budget"]["whole_raw_multiplier"] = True
            elif key == "future":
                row["budget_factor_feature"]["history"][-1]["trade_date"] = row["trade_date"]
            elif key == "extra":
                row["undeclared_input"] = 0
            else:
                altered["whole_information_coverage_parameters"]["delivery"] = "public"
            with self.assertRaises(ValueError):
                validate_issuer_valuation(stocks, calendar, altered)
            if key in ("multiplier", "tolerance", "bool"):
                with self.assertRaises(ValueError):
                    verify_budget(row, params)

    def test_known_zero_does_not_reprice_originally_uninformed_background(self):
        args, shocks, industry, response, source, issuer, factor = execution_fixture()
        p = {**issuer["parameters"], "information_probability_bps": 0}
        row = {"trade_date": "2021-01-08", "valuation_shift_bps": 0,
            "risk_budget": {"residual_raw_shift_bps": 0, "market_raw_shift_bps": 0},
            "whole_information_budget": received_budget(0, 0, 0, p["max_shift_bps"])}
        receipt = message_receipt(row, p, "stock", "background")
        demand = {"requested_quantity": 100, "side": "buy", "limit_price_minor": 10015}
        result = respond_issuer_background(demand, receipt, 10000, (9000, 11000), args[4], args[3], 0, response)
        self.assertEqual(result["limit_price_minor"], demand["limit_price_minor"])
        self.assertEqual(result["issuer_valuation_response"]["receipt"], receipt)

    def test_target_return_unknown_or_forged_does_not_change_whole_execution(self):
        args, shocks, industry, response, source, issuer, factor = execution_fixture()
        risk = build_lagged_risk(source, args[0], issuer["parameters"]["history_window"])
        features = build_features(source, args[0], factor["parameters"])
        for coupling in (None, "residual"):
            path = build_path(risk, features, "isolation", issuer["parameters"], "market_shared", "market", "whole_public", coupling)
            original = simulate_portfolio(*args, issuer_valuation=path)
            for value in (None, 5.0):
                changed = copy.deepcopy(args)
                for rows in changed[0].values():
                    for row in rows:
                        row["observed_return"] = value
                self.assertEqual(simulate_portfolio(*changed, issuer_valuation=path), original)

    def test_grid_has_eighty_unique_new_conditions_not_duplicate_reference_labels(self):
        self.assertEqual(len(CELLS), 16)
        self.assertEqual(len({r["name"] for r in CELLS}), 16)
        self.assertEqual({r["delivery"] for r in CELLS}, {"whole_public"})
        self.assertEqual(len({path_key(r) for r in CELLS}), 4)
        self.assertEqual(sum(r["residual_dependence"] == "historical_rank" for r in CELLS), 8)


if __name__ == "__main__":
    unittest.main()
