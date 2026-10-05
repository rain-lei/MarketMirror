import copy
import statistics
from pathlib import Path
import unittest

import test_issuer_valuation
from research.simulation.audit_cash_resource_controls import (
    independent_transfer, initial_state, verify_funding, verify_initial_wealth)
from research.simulation.audit_own_price_feedback import verify_final_resources
from research.simulation.audit_pre_wuhan_allocation_attribution_2019 import independent_covariance
from research.simulation.audit_price_feedback_channels import verify_day
from research.simulation.cash_resource_controls import CONTROLS, funding_only, split_amount, transfer_cash
from research.simulation.issuer_valuation import build_issuer_valuation, history_hash, RISK_FIELDS
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_cash_resource_controls import simulate_portfolio
from research.simulation.portfolio_price_feedback_channels import simulate_portfolio as legacy


def run(args, shocks, industry, response, issuer, anchor, scale, control):
    return simulate_portfolio(*args, scenario_shocks=shocks, industry_shocks=industry,
        background_response=response, issuer_valuation=issuer, quantity_controls=anchor,
        target_feedback_scale=scale, quote_feedback_scale=scale, cash_resource_control=control)


def finite_accounts():
    accounts = {"aggressive_00": {"wallets": {"a": 10000000}, "shares": {"a": 300}, "sellable": {"a": 200}},
                "background_a_000": {"wallets": {"a": 5000000}, "shares": {"a": 500}, "sellable": {"a": 500}}}
    specs = {"aggressive_00": {"kind": "strategy"}, "background_a_000": {"kind": "background", "asset": "a"}}
    return accounts, specs


class CashResourceControlsTest(unittest.TestCase):
    def test_known_initial_transfer_has_fixed_total_half_or_double_background_cash(self):
        accounts, specs = finite_accounts()
        before = copy.deepcopy(accounts)
        for name, s_cash, b_cash in (("less_background", 12500000, 2500000), ("original_cash", 10000000, 5000000),
                                      ("more_background", 5000000, 10000000)):
            result, receipt = transfer_cash(accounts, specs, ["a"], CONTROLS[name])
            self.assertEqual(result["aggressive_00"]["wallets"]["a"], s_cash)
            self.assertEqual(result["background_a_000"]["wallets"]["a"], b_cash)
            self.assertEqual(receipt["a"]["total_cash_minor"], 15000000)
            self.assertEqual((result, receipt), independent_transfer(accounts, specs, ["a"], CONTROLS[name]))
            self.assertEqual(result["aggressive_00"]["shares"], before["aggressive_00"]["shares"])
            self.assertEqual(result["aggressive_00"]["sellable"], before["aggressive_00"]["sellable"])
        self.assertEqual(accounts, before)

    def test_integer_residuals_are_exact_and_zero_wallet_receiver_can_receive(self):
        self.assertEqual(split_amount(5, {"c": 1, "b": 1, "a": 1}), {"c": 1, "b": 2, "a": 2})
        a, specs = finite_accounts()
        a["aggressive_00"]["wallets"]["a"] = 101
        a["background_a_000"]["wallets"]["a"] = 19
        a["background_a_001"] = {"wallets": {"a": 0}, "shares": {"a": 1}, "sellable": {"a": 1}}
        specs["background_a_001"] = {"kind": "background", "asset": "a"}
        result, receipt = transfer_cash(a, specs, ["a"], CONTROLS["more_background"])
        self.assertGreater(result["background_a_001"]["wallets"]["a"], 0)
        self.assertEqual(sum(x["wallets"]["a"] for x in result.values()), 120)
        self.assertEqual((result, receipt), independent_transfer(a, specs, ["a"], CONTROLS["more_background"]))

    def test_zero_pool_stays_zero_and_original_cash_is_identity_for_unequal_wallets(self):
        a, specs = finite_accounts()
        a["aggressive_00"]["wallets"]["a"] = 37
        a["background_a_000"]["wallets"]["a"] = 91
        result, receipt = transfer_cash(a, specs, ["a"], CONTROLS["original_cash"])
        self.assertEqual(a, result)
        self.assertEqual(receipt["a"]["owner_transfers_minor"], {})
        for account in a.values():
            account["wallets"]["a"] = 0
        for control in CONTROLS.values():
            result, receipt = transfer_cash(a, specs, ["a"], control)
            self.assertEqual(a, result)
            self.assertEqual((result, receipt), independent_transfer(a, specs, ["a"], control))

    def test_complete_original_resource_objects_match_legacy_all_four_anchor_feedback_cells(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        for anchor in (None, {"background_anchor": "current_inventory", "strategy_wait": "original"}):
            for scale in (1, 0):
                old = legacy(*args, scenario_shocks=shocks, industry_shocks=industry,
                    background_response=response, issuer_valuation=issuer, quantity_controls=anchor,
                    target_feedback_scale=scale, quote_feedback_scale=scale)
                new = run(args, shocks, industry, response, issuer, anchor, scale, CONTROLS["original_cash"])
                self.assertEqual(new, old)

    def test_all_twelve_paths_keep_messages_and_independently_rebuild_risk_quotes_wallets(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        receipts = None
        base = {"core": args[2], "background": args[3], "venue": args[4]}
        for anchor in (None, {"background_anchor": "current_inventory", "strategy_wait": "original"}):
            for scale in (1, 0):
                for cash in CONTROLS.values():
                    result = run(args, shocks, industry, response, issuer, anchor, scale, cash)
                    specs = result["participant_specs"]
                    state = initial_state(specs, sorted(args[0]), base, args[6], cash, result.get("initial_cash_resource_receipt"))
                    verify_initial_wealth(state, result["summary"])
                    history = {a: [] for a in args[0]}
                    streaks = {n: {"sign": 0, "streak": 0} for n, s in specs.items() if s["kind"] == "strategy"}
                    current = [d["issuer_information_receipts"] for d in result["trace"]]
                    if receipts is None:
                        receipts = current
                    self.assertEqual(receipts, current)
                    for session, day in enumerate(result["trace"]):
                        expected = None if cash == CONTROLS["original_cash"] else cash
                        self.assertEqual(day.get("initial_cash_resource_control"), expected)
                        independent_covariance(day, history, args[5])
                        verify_day(day, state, specs, args[4], args[3], response, shocks, industry, issuer,
                                   anchor, args[6], streaks, session, scale, scale)
                        state = audit_portfolio_day(day, state, args[4], session, dense=True)
                        for a, call in day["portfolio_auction"]["asset_calls"].items():
                            history[a].append({"trade_date": day["trade_date"], "price_before_minor": call["price_before_minor"],
                                               "price_after_minor": call["price_after_minor"]})
                    verify_final_resources(state, result["summary"])

    def test_funding_only_holds_all_submissions_fixed_and_contains_no_fills_or_new_prices(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = run(args, shocks, industry, response, issuer, None, 1, CONTROLS["original_cash"])
        specs = result["participant_specs"]
        state = initial_state(specs, sorted(args[0]), {"core": args[2], "background": args[3], "venue": args[4]}, args[6], CONTROLS["original_cash"], None)
        for session, day in enumerate(result["trace"]):
            before = copy.deepcopy((state, day))
            for cash in CONTROLS.values():
                payload = funding_only(day, state, specs, args[4], cash)
                verify_funding(payload, day, state, specs, args[4], cash)
                for a, orders in payload["orders"].items():
                    for o, original in zip(orders, day["portfolio_auction"]["asset_calls"][a]["orders"]):
                        self.assertEqual(set(o), {"order_id", "owner", "side", "quantity", "limit_price_minor", "sequence", "accepted_quantity"})
                        self.assertEqual({k: v for k, v in o.items() if k != "accepted_quantity"}, {k: original[k] for k in o if k != "accepted_quantity"})
            self.assertEqual((state, day), before)
            state = audit_portfolio_day(day, state, args[4], session)

    def test_funding_changes_affordability_without_using_any_same_session_sale(self):
        a, specs = finite_accounts()
        a["aggressive_00"]["wallets"]["a"] = 1000
        a["background_a_000"]["wallets"]["a"] = 100
        orders = [{"order_id": "0", "owner": "background_a_000", "side": "buy", "quantity": 20, "limit_price_minor": 100, "sequence": 0},
                  {"order_id": "1", "owner": "aggressive_00", "side": "sell", "quantity": 200, "limit_price_minor": 100, "sequence": 1}]
        day = {"portfolio_auction": {"session": 0, "asset_calls": {"a": {"orders": orders, "execution_available": True,
                "price_before_minor": 100, "price_bounds_minor": [80, 120]}}}}
        state = {"accounts": a, "prices": {"a": 100}}
        venue = {"tick_minor": 1, "lot_size": 1, "price_band_bps": 2000, "fee_bps": 0}
        low = funding_only(day, state, specs, venue, CONTROLS["original_cash"])
        high = funding_only(day, state, specs, venue, CONTROLS["more_background"])
        self.assertEqual((low["orders"]["a"][0]["accepted_quantity"], high["orders"]["a"][0]["accepted_quantity"]), (1, 3))
        self.assertEqual(high["orders"]["a"][1]["accepted_quantity"], 200)
        verify_funding(high, day, state, specs, venue, CONTROLS["more_background"])
        bad = copy.deepcopy(high)
        bad["orders"]["a"][0]["filled_quantity"] = 1
        with self.assertRaises(ValueError):
            verify_funding(bad, day, state, specs, venue, CONTROLS["more_background"])

    def test_invalid_controls_shared_cash_foreign_asset_and_tampered_initial_receipt_fail(self):
        a, specs = finite_accounts()
        for control in ({"numerator": True, "denominator": 1}, {"numerator": 3, "denominator": 1},
                        {"numerator": 0, "denominator": 1}, {"numerator": 4.0, "denominator": 1}):
            with self.assertRaises(ValueError):
                transfer_cash(a, specs, ["a"], control)
        bad = copy.deepcopy(a)
        bad["aggressive_00"]["wallets"] = {"shared": 100}
        with self.assertRaises(ValueError):
            transfer_cash(bad, specs, ["a"], CONTROLS["more_background"])
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        result = run(args, shocks, industry, response, issuer, None, 1, CONTROLS["more_background"])
        bad = copy.deepcopy(result["initial_cash_resource_receipt"])
        first = next(iter(bad["assets"].values()))
        first["total_cash_minor"] += 1
        with self.assertRaises(ValueError):
            initial_state(result["participant_specs"], sorted(args[0]), {"core": args[2], "background": args[3], "venue": args[4]}, args[6], CONTROLS["more_background"], bad)

    def test_future_source_target_and_future_news_cannot_change_prior_cash_paths(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        original = run(args, shocks, industry, response, issuer, None, 1, CONTROLS["more_background"])
        changed_args = copy.deepcopy(args)
        for rows in changed_args[0].values():
            for row in rows:
                row["observed_return"] = 1000.0
        changed = run(changed_args, shocks, industry, response, issuer, None, 1, CONTROLS["more_background"])
        self.assertEqual(original, changed)
        risk = {a: [{k: copy.deepcopy(row[k]) for k in RISK_FIELDS} for row in rows]
                for a, rows in issuer["by_stock"].items()}
        for rows in risk.values():
            row = rows[-1]
            row["history"][0]["stock_return"] += .02
            row["history_sha256"] = history_hash(row["history"])
            row["lagged_stock_volatility"] = statistics.stdev(item["stock_return"] for item in row["history"])
        later = build_issuer_valuation(risk, issuer["scenario_id"], issuer["parameters"])
        future = run(args, shocks, industry, response, later, None, 1, CONTROLS["more_background"])
        self.assertEqual(original["trace"][:-1], future["trace"][:-1])


if __name__ == "__main__":
    unittest.main()
