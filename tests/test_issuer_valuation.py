import copy
import math
import unittest
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
from types import SimpleNamespace

from research.simulation.audit_issuer_valuation import expected_receipt, verify_risk_panel, verify_issuer_day
from research.simulation.issuer_valuation import (build_lagged_risk, build_issuer_valuation, message_receipt,
                                                strategy_quote_terms, respond_issuer_background, validate_issuer_valuation)
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_market import initial_portfolio, simulate_portfolio
import test_industry_shocks


PARAMETERS = {"history_window": 3, "risk_multiplier": 1.0, "belief_scale_bps": 1000, "max_shift_bps": 500,
              "information_probability_bps": 5000, "innovation_seed": "generic-company-test", "information_seed": "generic-information-test"}


def fixture(probability=5000):
    args, shocks, industry, response = test_industry_shocks.IndustryShockTest().fixture(3)
    source = {stock: [SimpleNamespace(trade_date=date(2020, 6, 24) + timedelta(days=i), stock_return=((i + index) % 5 - 2) / 50)
                      for i in range(18)] for index, stock in enumerate(args[0])}
    risk = build_lagged_risk(source, args[0], 3)
    parameters = {**PARAMETERS, "information_probability_bps": probability}
    issuer = build_issuer_valuation(risk, "generic-issuer-test", parameters)
    return args, shocks, industry, response, source, risk, issuer


def simulate(args, shocks, industry, response, issuer):
    return simulate_portfolio(*args, scenario_shocks=shocks, industry_shocks=industry, background_response=response, issuer_valuation=issuer)


class IssuerValuationTest(unittest.TestCase):
    def test_risk_contract_excludes_targets_and_independently_matches_t_minus_two_history(self):
        args, _, _, _, source, risk, _ = fixture()
        self.assertEqual(verify_risk_panel(risk, source, args[0], 3)["independent_t_minus_two_risk_rows"], 12)
        modified = copy.deepcopy(args[0])
        for rows in modified.values():
            for row in rows:
                row.update(observed_return=-0.99, future_text="forbidden target text")
        self.assertEqual(risk, build_lagged_risk(source, modified, 3))
        poisoned = copy.deepcopy(risk)
        poisoned["000001"][0]["observed_return"] = 0.99
        with self.assertRaisesRegex(ValueError, "target fields"):
            build_issuer_valuation(poisoned, "bad", PARAMETERS)
        later = copy.deepcopy(source)
        for rows in later.values():
            for row in rows:
                if row.trade_date > date(2020, 7, 1):
                    row.stock_return = 0.8
        rebuilt = build_lagged_risk(later, args[0], 3)
        self.assertEqual(risk["000001"][0], rebuilt["000001"][0])

    def test_recipient_masks_are_coupled_across_strength_and_zero_receipts_are_explicitly_missing(self):
        _, _, _, _, _, risk, first = fixture()
        stronger = build_issuer_valuation(risk, "different-scenario", {**PARAMETERS, "risk_multiplier": 0.25})
        for stock in risk:
            for original, changed in zip(first["by_stock"][stock], stronger["by_stock"][stock]):
                for owner in ("aggressive_000", f"background_{stock}_003"):
                    a = message_receipt(original, PARAMETERS, stock, owner)
                    b = message_receipt(changed, stronger["parameters"], stock, owner)
                    self.assertEqual((a["received"], a["receipt_sha256"]), (b["received"], b["receipt_sha256"]))
                    self.assertEqual(a, expected_receipt(original, PARAMETERS, stock, owner))
        row = first["by_stock"]["000001"][0]
        missing = message_receipt(row, {**PARAMETERS, "information_probability_bps": 0}, "000001", "aggressive_000")
        self.assertFalse(missing["received"])
        self.assertIsNone(missing["valuation_shift_bps"])
        self.assertEqual(missing["applied_valuation_shift_bps"], 0.0)
        self.assertEqual(missing["applied_belief_signal"], 0.0)

    def test_quotes_use_physical_shift_once_even_when_beliefs_saturate(self):
        agent = SimpleNamespace(market_sensitivity=0.8, text_sensitivity=0.4, uncertainty_aversion=0.2)
        observation = {"market_signal": 0.95, "scenario_shock": 0.0, "text_signal": 0.0, "text_uncertainty": 0.0}
        receipt = {"applied_valuation_shift_bps": 100.0, "applied_belief_signal": 0.1}
        terms = strategy_quote_terms(agent, {"momentum_loading": 1.0, "market_bias": 0.0}, observation, receipt, 0.8, False)
        self.assertAlmostEqual(terms["base_belief"], 0.76)
        self.assertAlmostEqual(terms["belief_contribution"], 0.04)
        self.assertEqual(terms["quote_shift_bps"], 80.0)

    def test_background_quotes_are_recomputed_before_rounding_and_preserve_liquidity_masks(self):
        args, _, _, _, _, _, _ = fixture()
        venue, background = args[4], args[3]
        demand = {"side": "buy", "requested_quantity": 100, "limit_price_minor": 10050, "target_shares": 600}
        receipt = {"received": True, "applied_valuation_shift_bps": 0.73}
        result = respond_issuer_background(demand, receipt, 10001, (9001, 11001), venue, background, 0.123,
                                           {"valuation_response_bps": 200, "pulse_participation_bps": 10000})
        raw = Decimal(10001) * (1 + (Decimal("0.123") * 200 + Decimal("0.73") + background["urgency_bps"]) / 10000)
        self.assertEqual(result["limit_price_minor"], int(raw.to_integral_value(rounding=ROUND_FLOOR)))
        self.assertEqual(result["requested_quantity"], demand["requested_quantity"])
        withheld = {**demand, "side": "hold", "requested_quantity": 0, "limit_price_minor": None}
        result = respond_issuer_background(withheld, receipt, 10001, (9001, 11001), venue, background, 0.123, None)
        self.assertEqual((result["side"], result["requested_quantity"], result["limit_price_minor"]), ("hold", 0, None))

    def test_private_decisions_full_orders_and_ledger_are_independently_rebuilt_and_tamper_rejected(self):
        args, shocks, industry, response, _, _, issuer = fixture()
        result = simulate(args, shocks, industry, response, issuer)
        _, accounts, specs, _ = initial_portfolio(args[1], args[2], args[3], list(args[0]), args[6], args[4])
        state = {"accounts": {name: asdict(account) for name, account in accounts.items()},
                 "prices": {stock: args[4]["price_start_minor"] for stock in args[0]}, "fee_pool_minor": 0,
                 "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
                 "initial_shares": {stock: sum(account.shares[stock] for account in accounts.values()) for stock in args[0]}}
        first_state = copy.deepcopy(state)
        for session, day in enumerate(result["trace"]):
            counts = verify_issuer_day(day, state, specs, args[4], args[3], response, shocks, industry, issuer, session)
            self.assertEqual(counts["background_demand_checks"], len(args[0]) * args[3]["participants"])
            self.assertFalse(any(key.startswith("issuer_valuation") for observation in day["observations"].values() for key in observation))
            state = audit_portfolio_day(day, state, args[4], session, dense=True)
        self.assertEqual(state["prices"], result["summary"]["final_prices_minor"])
        altered = copy.deepcopy(result["trace"][0])
        name = next(name for name in specs if specs[name]["kind"] == "strategy")
        altered["issuer_information_receipts"][name]["000001"]["received"] ^= True
        with self.assertRaisesRegex(ValueError, "private information masks"):
            verify_issuer_day(altered, first_state, specs, args[4], args[3], response, shocks, industry, issuer, 0)
        altered = copy.deepcopy(result["trace"][0])
        order = next(order for call in altered["portfolio_auction"]["asset_calls"].values() for order in call["orders"] if specs[order["owner"]]["kind"] == "background")
        order["limit_price_minor"] += 1
        with self.assertRaisesRegex(ValueError, "submitted order differs"):
            verify_issuer_day(altered, first_state, specs, args[4], args[3], response, shocks, industry, issuer, 0)

    def test_default_none_preserves_legacy_and_disabled_text_targets_never_reach_agents(self):
        args, shocks, industry, response, _, _, issuer = fixture()
        legacy = simulate_portfolio(*args, scenario_shocks=shocks, background_response=response, industry_shocks=industry)
        self.assertEqual(legacy, simulate(args, shocks, industry, response, None))
        original = simulate(args, shocks, industry, response, issuer)
        changed = copy.deepcopy(args[0])
        for rows in changed.values():
            for row in rows:
                row.update(observed_return=0.9, text_signal=-0.8, text_uncertainty=0.9, text_evidence="unused")
        self.assertEqual(original, simulate((changed, *args[1:]), shocks, industry, response, issuer))

    def test_future_valid_messages_cannot_change_prior_trace(self):
        args, shocks, industry, response, _, risk, issuer = fixture()
        original = simulate(args, shocks, industry, response, issuer)
        later = copy.deepcopy(risk)
        # Change only the last window, with a valid recomputed history hash.
        from research.simulation.issuer_valuation import history_hash
        import statistics
        for rows in later.values():
            row = rows[-1]
            row["history"][0]["stock_return"] += 0.02
            row["history_sha256"] = history_hash(row["history"])
            row["lagged_stock_volatility"] = statistics.stdev(item["stock_return"] for item in row["history"])
        altered = build_issuer_valuation(later, issuer["scenario_id"], issuer["parameters"])
        updated = simulate(args, shocks, industry, response, altered)
        self.assertEqual(original["trace"][:-1], updated["trace"][:-1])

    def test_future_missing_nonfinite_and_undeclared_message_inputs_are_rejected(self):
        args, shocks, industry, response, _, _, issuer = fixture()
        calendar = [(row["trade_date"], row["signal_cutoff_date"], row["execution_reference_date"]) for row in args[0]["000001"]]
        bad = copy.deepcopy(issuer)
        bad["by_stock"]["000001"][0]["history"][-1]["trade_date"] = calendar[0][0]
        with self.assertRaisesRegex(ValueError, "future"):
            validate_issuer_valuation(list(args[0]), calendar, bad)
        bad = copy.deepcopy(issuer)
        bad["by_stock"].pop("000001")
        with self.assertRaisesRegex(ValueError, "coverage"):
            simulate(args, shocks, industry, response, bad)
        bad = copy.deepcopy(issuer)
        bad["by_stock"]["000001"][0]["lagged_stock_volatility"] = math.nan
        with self.assertRaisesRegex(ValueError, "lagged risk"):
            simulate(args, shocks, industry, response, bad)
        bad = copy.deepcopy(issuer)
        bad["by_stock"]["000001"][0]["observed_return"] = 0.3
        with self.assertRaisesRegex(ValueError, "undeclared"):
            simulate(args, shocks, industry, response, bad)


if __name__ == "__main__":
    unittest.main()
