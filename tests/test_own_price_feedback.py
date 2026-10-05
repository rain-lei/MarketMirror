import copy
import unittest

import test_issuer_valuation
from research.simulation.audit_own_price_feedback import (expected_specs, strategy_orders, verify_final_resources,
                                                        verify_arrival, verify_same_state, verify_specs)
from research.simulation.audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs, independent_covariance
from research.simulation.audit_quantity_controls import verify_quantity_day
from research.simulation.own_price_feedback import same_state_orders
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_market import simulate_portfolio as original_simulate
from research.simulation.portfolio_own_price_feedback import simulate_portfolio


ANCHORS = [None, {"background_anchor": "current_inventory", "strategy_wait": "original"}]


def run(args, shocks, industry, response, issuer, controls, scale):
    return simulate_portfolio(*args, scenario_shocks=shocks, industry_shocks=industry, background_response=response,
                              issuer_valuation=issuer, quantity_controls=controls, own_price_feedback_scale=scale)


def resources(args, specs):
    base = {"core": args[2], "background": args[3], "venue": args[4]}
    return initial_from_specs(specs, sorted(args[0]), base, args[6])


class OwnPriceFeedbackTest(unittest.TestCase):
    def test_new_loop_one_scale_matches_complete_old_objects_for_both_anchors(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        for controls in ANCHORS:
            old = original_simulate(*args, scenario_shocks=shocks, industry_shocks=industry, background_response=response,
                                    issuer_valuation=issuer, quantity_controls=controls)
            self.assertEqual(old, run(args, shocks, industry, response, issuer, controls, 1.0))

    def test_profiles_keep_every_parameter_and_non_momentum_field(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        original = run(args, shocks, industry, response, issuer, None, 1.0)
        for scale in (0.5, 0.0):
            result = run(args, shocks, industry, response, issuer, None, scale)
            verify_specs(result["participant_specs"], original["participant_specs"], scale)
            for name, spec in result["participant_specs"].items():
                if spec["kind"] == "strategy":
                    self.assertEqual(spec["parameters"], original["participant_specs"][name]["parameters"])
                    self.assertEqual(spec["profile"]["momentum_loading"], original["participant_specs"][name]["profile"]["momentum_loading"] * scale)
            self.assertEqual(result["summary"]["own_price_feedback_scale"], scale)

    def test_all_six_paths_keep_private_draws_risk_covariance_and_resources_audited(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        masks = None
        for controls in ANCHORS:
            baseline = run(args, shocks, industry, response, issuer, controls, 1.0)
            for scale in (1.0, 0.5, 0.0):
                result = run(args, shocks, industry, response, issuer, controls, scale)
                specs, state = result["participant_specs"], resources(args, result["participant_specs"])
                histories = {stock: [] for stock in args[0]}
                streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
                verify_specs(specs, baseline["participant_specs"], scale)
                receipts = [day["issuer_information_receipts"] for day in result["trace"]]
                if masks is None:
                    masks = receipts
                else:
                    self.assertEqual(masks, receipts)
                for session, day in enumerate(result["trace"]):
                    independent_covariance(day, histories, args[5])
                    counts = verify_quantity_day(day, state, specs, args[4], args[3], response, shocks, industry,
                                                 issuer, controls, args[6], streaks, session)
                    self.assertEqual(counts["strategy_allocation_checks"], 12)
                    state = audit_portfolio_day(day, state, args[4], session, dense=True)
                    for stock, call in day["portfolio_auction"]["asset_calls"].items():
                        histories[stock].append({"trade_date": day["trade_date"], "price_before_minor": call["price_before_minor"],
                                                 "price_after_minor": call["price_after_minor"]})
                verify_final_resources(state, result["summary"])

    def test_future_returns_and_disabled_text_do_not_change_intervened_paths(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        poisoned = copy.deepcopy(args[0])
        for rows in poisoned.values():
            for row in rows:
                row.update(observed_return=-0.95, market_signal=-1.0, text_signal=-1.0,
                           text_uncertainty=1.0, text_evidence="unused future answer")
        for scale in (0.5, 0.0):
            a = run(args, shocks, industry, response, issuer, ANCHORS[1], scale)
            b = run((poisoned, *args[1:]), shocks, industry, response, issuer, ANCHORS[1], scale)
            self.assertEqual(a, b)

    def test_scale_validation_rejects_nonfinite_bool_out_of_range_and_unscoped_controls(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        for value in (True, False, None, "0.5", float("nan"), float("inf"), -0.1, 1.1, {"risk_disabled": True}):
            with self.assertRaisesRegex(ValueError, "finite number"):
                run(args, shocks, industry, response, issuer, None, value)

    def test_independent_profile_audit_rejects_risk_change_false_origin_and_wrong_scale(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        base = run(args, shocks, industry, response, issuer, None, 1.0)["participant_specs"]
        active = run(args, shocks, industry, response, issuer, None, 0.5)["participant_specs"]
        name = next(name for name, spec in active.items() if spec["kind"] == "strategy")
        for field in ("risk", "origin", "scale"):
            bad = copy.deepcopy(active)
            if field == "risk": bad[name]["parameters"]["risk_budget"] *= 2
            elif field == "origin": bad[name]["own_price_feedback_control"]["original_momentum_loading"] += 1
            else: bad[name]["profile"]["momentum_loading"] += 0.2
            with self.assertRaisesRegex(ValueError, "other than momentum"):
                verify_specs(bad, base, 0.5)

    def test_same_state_all_scales_match_rational_audit_and_do_not_mutate_prior_state(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        baseline = run(args, shocks, industry, response, issuer, None, 1.0)
        specs, state = baseline["participant_specs"], resources(args, baseline["participant_specs"])
        streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
        for session, day in enumerate(baseline["trace"]):
            saved_state, saved_streaks = copy.deepcopy(state), copy.deepcopy(streaks)
            for scale in (1.0, 0.5, 0.0):
                payload = same_state_orders(day, state, specs, args[6], args[4], streaks, scale)
                verify_same_state(payload, day, state, specs, args[4], args[3], response, shocks, industry,
                                  issuer, None, args[6], streaks, session, scale)
                self.assertEqual(state, saved_state)
                self.assertEqual(streaks, saved_streaks)
                for orders in payload["orders"].values():
                    self.assertTrue(all("filled_quantity" not in order for order in orders))
                if scale == 1.0:
                    self.assertEqual(payload["orders"], strategy_orders(day, specs))
            verify_quantity_day(day, state, specs, args[4], args[3], response, shocks, industry,
                                issuer, None, args[6], streaks, session)
            state = audit_portfolio_day(day, state, args[4], session, dense=True)

    def test_same_state_tampered_quantity_and_fabricated_fill_are_rejected(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = run(args, shocks, industry, response, issuer, None, 1.0)
        specs, state, day = result["participant_specs"], resources(args, result["participant_specs"]), result["trace"][0]
        streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
        payload = same_state_orders(day, state, specs, args[6], args[4], streaks, 0.5)
        stock = next(stock for stock, orders in payload["orders"].items() if orders)
        for tamper in ("quantity", "filled_quantity"):
            bad = copy.deepcopy(payload)
            if tamper == "quantity": bad["orders"][stock][0]["quantity"] += args[4]["lot_size"]
            else: bad["orders"][stock][0]["filled_quantity"] = 100
            with self.assertRaises(ValueError):
                verify_same_state(bad, day, state, specs, args[4], args[3], response, shocks, industry,
                                  issuer, None, args[6], streaks, 0, 0.5)

    def test_final_resource_audit_rejects_wallet_mutation(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = run(args, shocks, industry, response, issuer, None, 0.0)
        state = resources(args, result["participant_specs"])
        for session, day in enumerate(result["trace"]):
            state = audit_portfolio_day(day, state, args[4], session, dense=True)
        verify_final_resources(state, result["summary"])
        bad = copy.deepcopy(result["summary"])
        name = next(iter(bad["accounts"]))
        wallet = next(iter(bad["accounts"][name]["wallets"]))
        bad["accounts"][name]["wallets"][wallet] += 1
        with self.assertRaisesRegex(ValueError, "wallets/shares"):
            verify_final_resources(state, bad)

    def test_zero_feedback_retains_bias_shared_and_actually_received_private_information(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture(10000)
        result = run(args, shocks, industry, response, issuer, None, 1.0)
        specs, state, day = result["participant_specs"], resources(args, result["participant_specs"]), copy.deepcopy(result["trace"][0])
        for observation in day["observations"].values(): observation["market_signal"] = 0.9
        streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
        zero = same_state_orders(day, state, specs, args[6], args[4], streaks, 0.0)
        for name, decision in zero["decisions"].items():
            for stock, belief in decision["beliefs"].items():
                expected = specs[name]["profile"]["market_bias"] + day["observations"][stock]["scenario_shock"]
                expected += day["issuer_information_receipts"][name][stock]["applied_belief_signal"]
                self.assertAlmostEqual(belief, specs[name]["parameters"]["market_sensitivity"] * max(-1, min(1, expected)))

    def test_arrival_priority_is_independently_bound_to_seed(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = run(args, shocks, industry, response, issuer, None, 1.0)
        core = {**args[2], "order_mode": "hashed", "seed": 7}
        result = run((args[0], args[1], core, *args[3:]), shocks, industry, response, issuer, None, 1.0)
        verify_arrival(result["trace"][0], result["participant_specs"], core, 0)
        bad = copy.deepcopy(result["trace"][0]); bad["arrival_order"].reverse()
        with self.assertRaisesRegex(ValueError, "arrival priority"):
            verify_arrival(bad, result["participant_specs"], core, 0)


if __name__ == "__main__":
    unittest.main()
