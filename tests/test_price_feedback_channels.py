import copy
import unittest

import test_issuer_valuation
from research.simulation.audit_own_price_feedback import verify_final_resources
from research.simulation.audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs, independent_covariance
from research.simulation.audit_price_feedback_channels import verify_day, verify_same_state, verify_specs
from research.simulation.own_price_feedback import same_state_orders as legacy_same_state
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_own_price_feedback import simulate_portfolio as legacy_simulate
from research.simulation.portfolio_price_feedback_channels import simulate_portfolio
from research.simulation.price_feedback_channels import diagonal_as_legacy, same_state_orders

ANCHORS = [None, {"background_anchor": "current_inventory", "strategy_wait": "original"}]


def run(args, shocks, industry, response, issuer, anchor, target, quote):
    return simulate_portfolio(*args, scenario_shocks=shocks, industry_shocks=industry, background_response=response,
                              issuer_valuation=issuer, quantity_controls=anchor,
                              target_feedback_scale=target, quote_feedback_scale=quote)


def initial(args, specs):
    return initial_from_specs(specs, sorted(args[0]), {"core": args[2], "background": args[3], "venue": args[4]}, args[6])


class PriceFeedbackChannelsTest(unittest.TestCase):
    def test_complete_diagonal_objects_reproduce_all_previous_scales_and_anchors(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        for anchor in ANCHORS:
            for scale in (1.0, 0.5, 0.0):
                old = legacy_simulate(*args, scenario_shocks=shocks, industry_shocks=industry,
                                      background_response=response, issuer_valuation=issuer,
                                      quantity_controls=anchor, own_price_feedback_scale=scale)
                new = run(args, shocks, industry, response, issuer, anchor, scale, scale)
                self.assertEqual(old, diagonal_as_legacy(new, scale))

    def test_all_eight_paths_keep_independent_allocation_private_quotes_risk_and_resources(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        receipts = None
        for anchor in ANCHORS:
            base = run(args, shocks, industry, response, issuer, anchor, 1, 1)
            for target, quote in ((1, 1), (0, 1), (1, 0), (0, 0)):
                result = run(args, shocks, industry, response, issuer, anchor, target, quote)
                specs = result["participant_specs"]
                verify_specs(specs, base["participant_specs"], target, quote)
                state = initial(args, specs)
                histories = {stock: [] for stock in args[0]}
                streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
                current_receipts = [day["issuer_information_receipts"] for day in result["trace"]]
                if receipts is None:
                    receipts = current_receipts
                self.assertEqual(receipts, current_receipts)
                for session, day in enumerate(result["trace"]):
                    independent_covariance(day, histories, args[5])
                    counts = verify_day(day, state, specs, args[4], args[3], response, shocks, industry, issuer,
                                        anchor, args[6], streaks, session, target, quote)
                    self.assertEqual(12, counts["strategy_allocation_checks"])
                    state = audit_portfolio_day(day, state, args[4], session, dense=True)
                    for stock, call in day["portfolio_auction"]["asset_calls"].items():
                        histories[stock].append({"trade_date": day["trade_date"], "price_before_minor": call["price_before_minor"],
                                                 "price_after_minor": call["price_after_minor"]})
                verify_final_resources(state, result["summary"])

    def test_quote_only_same_state_preserves_all_target_decisions_and_requested_quantities(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = run(args, shocks, industry, response, issuer, None, 1, 1)
        specs, state, day = result["participant_specs"], initial(args, result["participant_specs"]), copy.deepcopy(result["trace"][0])
        for observation in day["observations"].values():
            observation["market_signal"] = 0.4
        streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
        old_state, old_streaks = copy.deepcopy(state), copy.deepcopy(streaks)
        baseline = same_state_orders(day, state, specs, args[6], args[4], streaks, 1, 1)
        quote_off = same_state_orders(day, state, specs, args[6], args[4], streaks, 1, 0)
        stripped = {name: {k: v for k, v in row.items() if k != "quote_base_beliefs"} for name, row in quote_off["decisions"].items()}
        self.assertEqual(baseline["decisions"], stripped)
        project = lambda payload: {stock: [{k: v for k, v in order.items() if k != "limit_price_minor"}
                                           for order in orders] for stock, orders in payload["orders"].items()}
        self.assertEqual(project(baseline), project(quote_off))
        self.assertNotEqual(baseline["orders"], quote_off["orders"])
        self.assertEqual(old_state, state)
        self.assertEqual(old_streaks, streaks)
        verify_same_state(quote_off, day, state, specs, args[4], issuer, args[6], streaks, 0, 1, 0)

    def test_target_only_same_state_preserves_physical_quote_base_and_private_shift(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = run(args, shocks, industry, response, issuer, None, 1, 1)
        specs, state, day = result["participant_specs"], initial(args, result["participant_specs"]), copy.deepcopy(result["trace"][0])
        for observation in day["observations"].values():
            observation["market_signal"] = 0.4
        streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
        baseline = same_state_orders(day, state, specs, args[6], args[4], streaks, 1, 1)
        target_off = same_state_orders(day, state, specs, args[6], args[4], streaks, 0, 1)
        for name, decision in target_off["decisions"].items():
            self.assertEqual(baseline["decisions"][name]["issuer_base_beliefs"], decision["quote_base_beliefs"])
            self.assertEqual(baseline["decisions"][name]["issuer_quote_shift_bps"], decision["issuer_quote_shift_bps"])
        self.assertNotEqual(baseline["orders"], target_off["orders"])
        verify_same_state(target_off, day, state, specs, args[4], issuer, args[6], streaks, 0, 0, 1)

    def test_diagonal_same_state_reproduces_legacy_orders_and_private_contributions(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = run(args, shocks, industry, response, issuer, None, 1, 1)
        specs, state, day = result["participant_specs"], initial(args, result["participant_specs"]), result["trace"][0]
        streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
        for scale in (1.0, 0.5, 0.0):
            old = legacy_same_state(day, state, specs, args[6], args[4], streaks, scale)
            new = same_state_orders(day, state, specs, args[6], args[4], streaks, scale, scale)
            for decision in new["decisions"].values():
                decision.pop("quote_base_beliefs", None)
            self.assertEqual(old, new)

    def test_future_target_return_and_disabled_text_poison_cannot_change_split_paths(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        poisoned = copy.deepcopy(args[0])
        for rows in poisoned.values():
            for row in rows:
                row.update(observed_return=-0.99, market_signal=-1.0, text_signal=-1.0,
                           text_uncertainty=1.0, text_evidence="future answer")
        for target, quote in ((0, 1), (1, 0), (0, 0)):
            self.assertEqual(run(args, shocks, industry, response, issuer, None, target, quote),
                             run((poisoned, *args[1:]), shocks, industry, response, issuer, None, target, quote))

    def test_both_controls_reject_nonfinite_bool_strings_and_out_of_range(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        for value in (True, False, None, "0", float("nan"), float("inf"), -0.1, 1.1):
            for target, quote in ((value, 1), (1, value)):
                with self.assertRaises(ValueError):
                    run(args, shocks, industry, response, issuer, None, target, quote)

    def test_text_or_missing_private_news_is_rejected_at_explicit_study_boundary(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        with self.assertRaisesRegex(ValueError, "disabled text"):
            run((*args[:-1], True), shocks, industry, response, issuer, None, 0, 1)
        with self.assertRaisesRegex(ValueError, "private issuer"):
            run(args, shocks, industry, response, None, None, 0, 1)

    def test_independent_spec_audit_rejects_risk_origin_and_cross_channel_changes(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        base = run(args, shocks, industry, response, issuer, None, 1, 1)["participant_specs"]
        actual = run(args, shocks, industry, response, issuer, None, 0, 1)["participant_specs"]
        name = next(name for name, spec in actual.items() if spec["kind"] == "strategy")
        for field in ("risk", "origin", "quote", "profile"):
            bad = copy.deepcopy(actual)
            if field == "risk": bad[name]["parameters"]["risk_budget"] *= 2
            elif field == "origin": bad[name]["price_feedback_channels"]["original_momentum_loading"] += 1
            elif field == "quote": bad[name]["price_feedback_channels"]["quote_scale"] = 0
            else: bad[name]["profile"]["momentum_loading"] += 0.1
            with self.assertRaisesRegex(ValueError, "undeclared parameter"):
                verify_specs(bad, base, 0, 1)

    def test_independent_same_state_audit_rejects_target_quote_quantity_and_fabricated_fill(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = run(args, shocks, industry, response, issuer, None, 1, 1)
        specs, state, day = result["participant_specs"], initial(args, result["participant_specs"]), result["trace"][0]
        streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
        payload = same_state_orders(day, state, specs, args[6], args[4], streaks, 0, 1)
        stock = next(stock for stock, orders in payload["orders"].items() if orders)
        name = payload["orders"][stock][0]["owner"]
        for field in ("target", "quote", "quantity", "fill"):
            bad = copy.deepcopy(payload)
            if field == "target": bad["decisions"][name]["beliefs"][stock] += 0.1
            elif field == "quote": bad["orders"][stock][0]["limit_price_minor"] += 100
            elif field == "quantity": bad["orders"][stock][0]["quantity"] += 100
            else: bad["orders"][stock][0]["filled_quantity"] = 100
            with self.assertRaises(ValueError):
                verify_same_state(bad, day, state, specs, args[4], issuer, args[6], streaks, 0, 0, 1)


if __name__ == "__main__":
    unittest.main()
