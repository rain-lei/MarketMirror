import copy
import unittest
from dataclasses import asdict

from research.simulation.agents import AgentParameters
from research.simulation.audit_background_response import expected_demand, verify_background_response, verify_strategy_quotes
from research.simulation.background_response import respond_background_demand, validate_background_response
from research.simulation.call_auction import AuctionAccount
from research.simulation.participant_market import background_demand
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_market import initial_portfolio, simulate_portfolio
from research.simulation.scenario_shocks import build_scenario_shocks
from test_background_market import CONFIG, CORE, FEEDBACK
from test_semantic_auction_experiment import PARAMETERS, VENUE, sample_steps


class BackgroundResponseTest(unittest.TestCase):
    def setUp(self):
        self.background = CONFIG["background_cases"][3]
        self.venue = {**VENUE, "price_start_minor": 10000, "lot_size": 100, "tick_minor": 1}
        self.bounds = (9000, 11000)

    def demand(self, shares):
        return background_demand("000001", 1, "background_000001_000", AuctionAccount(1000000, shares, shares),
                                 10000, self.bounds, self.venue, self.background)

    def respond(self, demand, shock, parameters):
        return respond_background_demand(demand, "000001", 1, "background_000001_000", 10000, self.bounds,
                                         self.venue, self.background, shock, parameters)

    def fixture(self):
        joined = {code: copy.deepcopy(sample_steps()) for code in ("000001", "000002", "000003")}
        agents = [AgentParameters(**row) for row in PARAMETERS]
        case = {"case_id": "response_test", "cash_mode": "separated", "institutional_asset_cap": 0.35}
        shocks = build_scenario_shocks(sorted(joined), len(joined["000001"]), "response_test",
                                       [1, 2], [1, -1], 0.2, 0.0, "test_seed")
        return joined, agents, case, shocks

    def test_common_valuation_shifts_both_sides_and_respects_price_band(self):
        response = {"valuation_response_bps": 200, "pulse_participation_bps": 10000}
        for shares, side, shock in ((0, "buy", 0.2), (1600, "sell", -0.2)):
            base = self.demand(shares)
            self.assertEqual(base["side"], side)
            result = self.respond(base, shock, response)
            self.assertEqual(result["requested_quantity"], base["requested_quantity"])
            self.assertEqual(result["limit_price_minor"] - base["limit_price_minor"], 40 if shock > 0 else -40)
            self.assertEqual(result, expected_demand("000001", 1, "background_000001_000", shares, 10000,
                                                   list(self.bounds), self.venue, self.background, shock, response))
        capped = self.respond(self.demand(0), 1.0, {"valuation_response_bps": 1000, "pulse_participation_bps": 10000})
        self.assertEqual(capped["limit_price_minor"], self.bounds[1])

    def test_liquidity_draws_are_coupled_across_valuation_cells(self):
        for name in (f"background_000001_{i:03d}" for i in range(20)):
            demand = background_demand("000001", 1, name, AuctionAccount(1000000, 0, 0),
                                       10000, self.bounds, self.venue, self.background)
            first = respond_background_demand(demand, "000001", 1, name, 10000, self.bounds, self.venue,
                                              self.background, 0.2, {"valuation_response_bps": 0, "pulse_participation_bps": 5000})
            second = respond_background_demand(demand, "000001", 1, name, 10000, self.bounds, self.venue,
                                               self.background, 0.2, {"valuation_response_bps": 200, "pulse_participation_bps": 5000})
            self.assertEqual(first["common_news_response"]["liquidity_sha256"], second["common_news_response"]["liquidity_sha256"])
            self.assertEqual(first["common_news_response"]["participates"], second["common_news_response"]["participates"])
            self.assertEqual(first["requested_quantity"], second["requested_quantity"])
        absent = self.respond(self.demand(0), 0.2, {"valuation_response_bps": 200, "pulse_participation_bps": 0})
        self.assertEqual((absent["requested_quantity"], absent["side"], absent["limit_price_minor"]), (0, "hold", None))

    def test_zero_response_and_no_pulse_preserve_demands_and_full_legacy_path(self):
        zero = {"valuation_response_bps": 0, "pulse_participation_bps": 10000}
        demand = self.demand(0)
        self.assertEqual(self.respond(demand, 0.2, zero), demand)
        self.assertEqual(self.respond(demand, 0, {"valuation_response_bps": 200, "pulse_participation_bps": 5000}), demand)
        joined, agents, case, shocks = self.fixture()
        args = (joined, agents, CORE, self.background, self.venue, FEEDBACK, case, False)
        legacy = simulate_portfolio(*args, scenario_shocks=shocks)
        result = simulate_portfolio(*args, scenario_shocks=shocks, background_response=zero)
        self.assertEqual(result, legacy)

    def test_full_path_has_independent_finite_ledger_audit_and_tamper_detection(self):
        joined, agents, case, shocks = self.fixture()
        response = {"valuation_response_bps": 200, "pulse_participation_bps": 5000}
        result = simulate_portfolio(joined, agents, CORE, self.background, self.venue, FEEDBACK, case, False,
                                    scenario_shocks=shocks, background_response=response)
        _, accounts, _, _ = initial_portfolio(agents, CORE, self.background, sorted(joined), case, self.venue)
        state = {"accounts": {name: asdict(account) for name, account in accounts.items()},
                 "prices": {asset: self.venue["price_start_minor"] for asset in joined}, "fee_pool_minor": 0,
                 "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
                 "initial_shares": {asset: sum(account.shares[asset] for account in accounts.values()) for asset in joined}}
        for session, day in enumerate(result["trace"]):
            verify_strategy_quotes(day, state, self.venue, result["participant_specs"])
            for stock in joined:
                verify_background_response(day, state, stock, session, self.venue, self.background, shocks["common"][session], response,
                                           result["participant_specs"])
            if session == 1:
                bad = copy.deepcopy(day)
                owner = next(iter(bad["background_demands"]["000001"]))
                bad["background_demands"]["000001"][owner]["common_news_response"]["valuation_shift_bps"] += 1
                with self.assertRaisesRegex(ValueError, "reconstruction"):
                    verify_background_response(bad, state, "000001", session, self.venue, self.background,
                                               shocks["common"][session], response, result["participant_specs"])
            state = audit_portfolio_day(day, state, self.venue, session, dense=True)
        self.assertEqual(state["prices"], result["summary"]["final_prices_minor"])

    def test_realized_targets_and_future_pulses_do_not_enter_earlier_response(self):
        joined, agents, case, shocks = self.fixture()
        response = {"valuation_response_bps": 200, "pulse_participation_bps": 5000}
        args = (agents, CORE, self.background, self.venue, FEEDBACK, case, False)
        first = simulate_portfolio(joined, *args, scenario_shocks=shocks, background_response=response)
        modified = copy.deepcopy(joined)
        for series in modified.values():
            for step in series:
                step.update(observed_return=-0.8, market_signal=0.99, estimated_volatility=0.9,
                            text_signal=0.9, text_uncertainty=0.9, text_evidence="future target")
        self.assertEqual(first, simulate_portfolio(modified, *args, scenario_shocks=shocks, background_response=response))
        later = copy.deepcopy(shocks)
        later["common"][-1] = 0.9
        second = simulate_portfolio(joined, *args, scenario_shocks=later, background_response=response)
        self.assertEqual(first["trace"][:-1], second["trace"][:-1])

    def test_invalid_response_or_missing_declared_shocks_is_rejected(self):
        for response in ({"valuation_response_bps": True, "pulse_participation_bps": 5000},
                         {"valuation_response_bps": 200, "pulse_participation_bps": -1},
                         {"valuation_response_bps": 200}):
            with self.assertRaises(ValueError):
                validate_background_response(response)
        joined, agents, case, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, "explicit shocks"):
            simulate_portfolio(joined, agents, CORE, self.background, self.venue, FEEDBACK, case, False,
                               background_response={"valuation_response_bps": 200, "pulse_participation_bps": 5000})


if __name__ == "__main__":
    unittest.main()
