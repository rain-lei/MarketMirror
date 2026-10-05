import copy
import unittest
from dataclasses import asdict

import test_issuer_valuation
from research.simulation.audit_quantity_controls import verify_quantity_day
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_market import initial_portfolio, simulate_portfolio
from research.simulation.quantity_controls import BASELINE, anchor_background_demand

CONTROLS = [None, {"background_anchor": "current_inventory", "strategy_wait": "original"},
            {"background_anchor": "initial_inventory", "strategy_wait": "immediate_daily"},
            {"background_anchor": "current_inventory", "strategy_wait": "immediate_daily"}]


def simulate(args, shocks, industry, response, issuer, controls):
    return simulate_portfolio(*args, scenario_shocks=shocks, industry_shocks=industry, background_response=response,
                              issuer_valuation=issuer, quantity_controls=controls)


def state_for(args):
    _, accounts, _, _ = initial_portfolio(args[1], args[2], args[3], list(args[0]), args[6], args[4])
    return {"accounts": {name: asdict(account) for name, account in accounts.items()},
            "prices": {stock: args[4]["price_start_minor"] for stock in args[0]}, "fee_pool_minor": 0,
            "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
            "initial_shares": {stock: sum(account.shares[stock] for account in accounts.values()) for stock in args[0]}}


class QuantityControlsTest(unittest.TestCase):
    def test_baseline_control_is_exact_default_and_future_targets_do_not_enter_active_rules(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        original = test_issuer_valuation.simulate(args, shocks, industry, response, issuer)
        self.assertEqual(original, simulate(args, shocks, industry, response, issuer, None))
        self.assertEqual(original, simulate(args, shocks, industry, response, issuer, BASELINE))
        active = simulate(args, shocks, industry, response, issuer, CONTROLS[3])
        poisoned = copy.deepcopy(args[0])
        for rows in poisoned.values():
            for row in rows:
                row.update(observed_return=-0.8, text_signal=-0.99, text_uncertainty=0.8, text_evidence="unused target")
        self.assertEqual(active, simulate((poisoned, *args[1:]), shocks, industry, response, issuer, CONTROLS[3]))

    def test_current_anchor_can_reverse_an_initial_inventory_restore_without_changing_draws(self):
        args, _, _, _, _, _, _ = test_issuer_valuation.fixture()
        demand = {"mode": "inventory_target", "current_shares": 1000, "target_shares": 700,
                  "target_offset_lots": 2, "requested_quantity": 300, "side": "sell", "limit_price_minor": 9975,
                  "shock_sha256": "a" * 64}
        result = anchor_background_demand(demand, 10000, (9000, 11000), args[4], args[3], CONTROLS[1])
        self.assertEqual((result["target_shares"], result["requested_quantity"], result["side"]), (1200, 200, "buy"))
        self.assertEqual(result["limit_price_minor"], 10025)
        self.assertEqual(result["shock_sha256"], demand["shock_sha256"])
        self.assertEqual(result["target_offset_lots"], demand["target_offset_lots"])
        self.assertEqual(result["inventory_anchor_response"]["original_side"], "sell")
        self.assertIs(anchor_background_demand(demand, 10000, (9000, 11000), args[4], args[3], None), demand)

    def test_daily_strategy_changes_only_wait_parameters_and_keeps_actual_risk_constraints(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        original = simulate(args, shocks, industry, response, issuer, None)
        daily = simulate(args, shocks, industry, response, issuer, CONTROLS[2])
        for name, spec in daily["participant_specs"].items():
            if spec["kind"] != "strategy":
                continue
            prior = original["participant_specs"][name]["parameters"]
            self.assertEqual(spec["parameters"], {**prior, "confirmation_steps": 1, "rebalance_interval": 1})
            for day in daily["trace"]:
                decision = day["decisions"][name]
                self.assertNotIn("confirmation_or_rebalance_wait", decision["reasons"])
                self.assertLessEqual(decision["desired_portfolio_risk"], prior["risk_budget"] + 1e-12)
                if decision["risk_liquidation"]:
                    self.assertTrue(all(value <= 1e-12 for value in decision["order_weight_changes"].values()))

    def test_all_four_full_paths_have_independent_belief_allocation_target_quote_and_ledger_audits(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        for controls in CONTROLS:
            result = simulate(args, shocks, industry, response, issuer, controls)
            specs, state = result["participant_specs"], state_for(args)
            streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
            for session, day in enumerate(result["trace"]):
                counts = verify_quantity_day(day, state, specs, args[4], args[3], response, shocks, industry, issuer,
                                             controls, args[6], streaks, session)
                self.assertEqual(counts["strategy_allocation_checks"], 12)
                self.assertEqual(counts["background_demand_checks"], 36)
                state = audit_portfolio_day(day, state, args[4], session, dense=True)
            self.assertEqual(state["prices"], result["summary"]["final_prices_minor"])

    def test_false_inventory_anchor_and_false_wait_allocation_are_rejected(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = simulate(args, shocks, industry, response, issuer, CONTROLS[3])
        day, specs, state = result["trace"][0], result["participant_specs"], state_for(args)

        def check(record):
            streaks = {name: {"sign": 0, "streak": 0} for name, spec in specs.items() if spec["kind"] == "strategy"}
            verify_quantity_day(record, state, specs, args[4], args[3], response, shocks, industry, issuer, CONTROLS[3], args[6], streaks, 0)

        bad = copy.deepcopy(day)
        name = next(iter(bad["background_demands"]["000001"]))
        bad["background_demands"]["000001"][name]["target_shares"] += 100
        with self.assertRaisesRegex(ValueError, "inventory anchor demand"):
            check(bad)
        bad = copy.deepcopy(day)
        name = next(iter(bad["decisions"]))
        bad["decisions"][name]["desired_weights"]["000001"] += 0.01
        with self.assertRaisesRegex(ValueError, "strategy allocation"):
            check(bad)

    def test_unsupported_controls_cannot_disable_risk_limits(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        for invalid in ({"background_anchor": "current_inventory"}, {**BASELINE, "risk_budget_disabled": True},
                        {"background_anchor": "future_inventory", "strategy_wait": "original"}):
            with self.assertRaisesRegex(ValueError, "explicit inventory anchor"):
                simulate(args, shocks, industry, response, issuer, invalid)


if __name__ == "__main__":
    unittest.main()
