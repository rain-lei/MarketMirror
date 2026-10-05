import copy
import unittest
from dataclasses import asdict, replace

import test_issuer_valuation
from research.simulation.call_auction import LimitOrder
from research.simulation.issuer_quote_sides import CASES, reprice_orders, verify_repriced_orders, reclear_from_state
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_auction import PortfolioAccount, PortfolioAuction
from research.simulation.portfolio_market import initial_portfolio


def original_fixture():
    args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
    result = test_issuer_valuation.simulate(args, shocks, industry, response, issuer)
    _, accounts, specs, _ = initial_portfolio(args[1], args[2], args[3], list(args[0]), args[6], args[4])
    state = {"accounts": {name: asdict(account) for name, account in accounts.items()},
             "prices": {stock: args[4]["price_start_minor"] for stock in args[0]}, "fee_pool_minor": 0,
             "initial_cash_minor": sum(sum(account.wallets.values()) for account in accounts.values()),
             "initial_shares": {stock: sum(account.shares[stock] for account in accounts.values()) for stock in args[0]}}
    return args, response, issuer, result, specs, state


def positive_toy():
    stock = "generic-asset"
    settings = {"price_start_minor": 10000, "lot_size": 100, "tick_minor": 1, "fee_bps": 10,
                "price_band_bps": 1000, "quote_spread_bps": 10, "quote_response_bps": 200}
    accounts = {"known_buyer": PortfolioAccount({stock: 2000000}, {stock: 0}, {stock: 0}),
                "unknown_buyer": PortfolioAccount({stock: 2000000}, {stock: 0}, {stock: 0}),
                "unknown_seller": PortfolioAccount({stock: 0}, {stock: 300}, {stock: 300})}
    books = {stock: [LimitOrder("1", "known_buyer", "buy", 100, 10300, 0),
                     LimitOrder("2", "unknown_buyer", "buy", 100, 10000, 1),
                     LimitOrder("3", "unknown_seller", "sell", 300, 10000, 2)]}
    venue = PortfolioAuction(accounts, [stock], settings)
    cleared = venue.clear(0, books, {stock: True})
    day = {"trade_date": "2020-07-03", "signal_cutoff_date": "2020-07-01", "execution_reference_date": "2020-07-02",
           "observations": {stock: {"common_shock": 0.0, "industry_shock": 0.0}}, "portfolio_auction": cleared,
           "issuer_information_receipts": {name: {stock: {"received": name == "known_buyer"}} for name in accounts}}
    state = {"accounts": {name: asdict(account) for name, account in accounts.items()}, "prices": {stock: 10000},
             "fee_pool_minor": 0, "initial_cash_minor": 4000000, "initial_shares": {stock: 300}}
    specs = {name: {"kind": "background", "asset": stock} for name in accounts}
    return stock, settings, day, state, specs


class IssuerQuoteSidesTest(unittest.TestCase):
    def test_full_source_auction_is_reproduced_including_wallets_escrows_and_t_plus_one(self):
        args, response, issuer, result, specs, state = original_fixture()
        for session, day in enumerate(result["trace"]):
            messages = {stock: issuer["by_stock"][stock][session]["valuation_shift_bps"] for stock in args[0]}
            books, changes = reprice_orders(day, specs, messages, args[4], args[3], response, CASES[0])
            self.assertEqual(changes, [])
            cleared = reclear_from_state(state, args[4], day, books, session)
            self.assertEqual(cleared, day["portfolio_auction"])
            state = audit_portfolio_day(day, state, args[4], session, dense=True)

    def test_quote_side_interventions_preserve_submissions_and_have_independent_formula_checks(self):
        args, response, issuer, result, specs, state = original_fixture()
        day = result["trace"][0]
        messages = {stock: issuer["by_stock"][stock][0]["valuation_shift_bps"] for stock in args[0]}
        original = copy.deepcopy((day, state))
        for case in CASES:
            books, changes = reprice_orders(day, specs, messages, args[4], args[3], response, case)
            verify_repriced_orders(day, specs, messages, args[4], args[3], response, case, books, changes)
            cleared = reclear_from_state(state, args[4], day, books, 0)
            record = {**day, "portfolio_auction": cleared}
            audit_portfolio_day(record, state, args[4], 0, dense=True)
            if case == "inform_unreceived_buyers":
                self.assertTrue(all(change["side"] == "buy" for change in changes))
            if case == "inform_unreceived_sellers":
                self.assertTrue(all(change["side"] == "sell" for change in changes))
            self.assertEqual((day, state), original)

    def test_uninformed_sell_quotes_can_anchor_positive_message_prices_with_excess_supply(self):
        stock, settings, day, state, specs = positive_toy()
        self.assertEqual(day["portfolio_auction"]["asset_calls"][stock]["price_after_minor"], 10000)
        outcomes = {}
        for case in CASES:
            books, changes = reprice_orders(day, specs, {stock: 300.0}, settings, {"urgency_bps": 0}, {"valuation_response_bps": 200}, case)
            verify_repriced_orders(day, specs, {stock: 300.0}, settings, {"urgency_bps": 0}, {"valuation_response_bps": 200}, case, books, changes)
            cleared = reclear_from_state(state, settings, day, books, 0)
            audit_portfolio_day({**day, "portfolio_auction": cleared}, state, settings, 0, dense=True)
            outcomes[case] = cleared["asset_calls"][stock]
        self.assertEqual(outcomes["inform_unreceived_buyers"]["price_after_minor"], 10000)
        self.assertEqual(outcomes["inform_unreceived_sellers"]["price_after_minor"], 10300)
        self.assertEqual(outcomes["inform_unreceived_both"]["price_after_minor"], 10300)
        self.assertEqual(outcomes["inform_unreceived_sellers"]["matched_volume"], 100)
        self.assertEqual(outcomes["inform_unreceived_both"]["matched_volume"], 200)

    def test_changed_quantity_or_wrong_treatment_quote_is_rejected(self):
        stock, settings, day, _, specs = positive_toy()
        messages, background, response = {stock: 300.0}, {"urgency_bps": 0}, {"valuation_response_bps": 200}
        books, changes = reprice_orders(day, specs, messages, settings, background, response, "inform_unreceived_sellers")
        altered = copy.deepcopy(books)
        altered[stock][0] = replace(altered[stock][0], quantity=200)
        with self.assertRaisesRegex(ValueError, "submission"):
            verify_repriced_orders(day, specs, messages, settings, background, response, "inform_unreceived_sellers", altered, changes)
        altered = copy.deepcopy(books)
        altered[stock][1] = replace(altered[stock][1], limit_price_minor=10300)
        with self.assertRaisesRegex(ValueError, "independent unrounded"):
            verify_repriced_orders(day, specs, messages, settings, background, response, "inform_unreceived_sellers", altered, changes)

    def test_halted_day_stays_untraded_and_invalid_messages_do_not_silently_clear(self):
        stock, settings, day, state, specs = positive_toy()
        day["portfolio_auction"]["asset_calls"][stock]["execution_available"] = False
        books, _ = reprice_orders(day, specs, {stock: 300.0}, settings, {"urgency_bps": 0}, {"valuation_response_bps": 200}, "inform_unreceived_both")
        cleared = reclear_from_state(state, settings, day, books, 0)
        self.assertEqual(cleared["asset_calls"][stock]["matched_volume"], 0)
        self.assertEqual(cleared["asset_calls"][stock]["price_after_minor"], state["prices"][stock])
        for message in (float("nan"), True, 501.0):
            with self.assertRaises(ValueError):
                reprice_orders(day, specs, {stock: message}, settings, {"urgency_bps": 0}, {"valuation_response_bps": 200}, "inform_unreceived_both")


if __name__ == "__main__":
    unittest.main()
