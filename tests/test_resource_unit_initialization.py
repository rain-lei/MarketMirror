import copy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_public_factor_channels import fixture
from research.simulation.resource_unit_initialization import (
    BASELINE, FIXED, NOTIONAL, initialize, first_reference_prices, InitialPricePortfolioAuction)
from research.simulation.audit_resource_unit_initialization import verify_initialization
from research.simulation.portfolio_temporal_resource_units import simulate_portfolio
from research.simulation.portfolio_temporal_whole_information import simulate_portfolio as original
from research.simulation.issuer_valuation import build_lagged_risk
from research.simulation.public_market_factor import build_features
from research.simulation.temporal_whole_information_risk import build_path
from research.simulation.audit_temporal_resource_unit_channels import verify_day
from research.simulation.audit_pre_wuhan_allocation_attribution_2019 import independent_covariance
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.audit_own_price_feedback import verify_final_resources


def execution_fixture():
    args, shocks, industry, response, source, issuer, factor = fixture()
    args = list(args)
    args[6] = {**args[6], "cash_mode": "separated"}
    risk = build_lagged_risk(source, args[0], issuer["parameters"]["history_window"])
    features = build_features(source, args[0], factor["parameters"])
    path = build_path(risk, features, "resource-test", issuer["parameters"],
                      "market_shared", "resource-market", "whole_public", "resource-rank")
    return args, dict(scenario_shocks=shocks, background_response=response, issuer_valuation=path), shocks, response


class ResourceUnitTests(unittest.TestCase):
    def inputs(self):
        args, _, _, _ = execution_fixture()
        return args, sorted(args[0])

    def test_first_reference_rejects_unknown_duplicate_future_and_fractional_prices(self):
        rows = [{"secid": "0.000001", "trade_date": "2022-01-04",
                 "execution_reference_date": "2021-12-31", "raw_reference_close": "10.25"}]
        self.assertEqual(first_reference_prices(rows, ["000001"], "2022-01-04", "2021-12-31", 1), {"000001": 1025})
        for field, value in (("raw_reference_close", None), ("raw_reference_close", True),
                             ("raw_reference_close", "10.251"), ("raw_reference_close", "NaN"),
                             ("raw_reference_close", "0"), ("execution_reference_date", "2022-01-04")):
            changed = copy.deepcopy(rows)
            changed[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                first_reference_prices(changed, ["000001"], "2022-01-04", "2021-12-31", 1)
        for changed in ([], rows + rows):
            with self.assertRaises(ValueError):
                first_reference_prices(changed, ["000001"], "2022-01-04", "2021-12-31", 1)

    def test_later_source_quote_does_not_change_initial_reference(self):
        first = {"secid": "0.000001", "trade_date": "2022-01-04",
                 "execution_reference_date": "2021-12-31", "raw_reference_close": "10.25"}
        later = {**first, "trade_date": "2022-01-05", "execution_reference_date": "2022-01-04", "raw_reference_close": None}
        self.assertEqual(first_reference_prices([first, later], ["000001"], "2022-01-04", "2021-12-31", 1), {"000001": 1025})

    def test_fixed_cash_shares_preserved_but_wealth_changes(self):
        args, assets = self.inputs()
        prices = {a: (1025, 26812, 224)[i % 3] for i, a in enumerate(assets)}
        result = initialize(*args[1:4], assets, args[6], args[4], FIXED, prices)
        receipt = result[4]
        for row in receipt["accounts"].values():
            self.assertEqual(row["original_wallets"], row["initial_wallets"])
            self.assertEqual(row["original_shares"], row["initial_shares"])
        self.assertTrue(any(row["wealth_change_minor"] for row in receipt["accounts"].values()))

    def test_notional_exact_wealth_and_residuals_for_all_owners_assets(self):
        args, assets = self.inputs()
        prices = {a: (1025, 26812, 224)[i % 3] for i, a in enumerate(assets)}
        saved = copy.deepcopy(args)
        result = initialize(*args[1:4], assets, args[6], args[4], NOTIONAL, prices)
        self.assertEqual(args, saved)
        for row in result[4]["accounts"].values():
            self.assertEqual(row["initial_wealth_minor"], row["original_wealth_minor"])
            for asset in assets:
                self.assertEqual(row["initial_shares"][asset] % args[4]["lot_size"], 0)
                self.assertEqual(row["original_shares"][asset] * args[4]["price_start_minor"],
                    row["initial_shares"][asset] * prices[asset] + row["stock_notional_residual_minor"][asset])
        self.assertFalse(result[4]["cross_arm_share_supply_preserved"])
        self.assertNotEqual(result[4]["aggregate_original_shares"], result[4]["aggregate_initial_shares"])

    def test_independent_all_owner_receipt_and_background_target_reconstruction(self):
        args, assets = self.inputs()
        design = dict(core=args[2], background=args[3], venue=args[4])
        for arm in (BASELINE, FIXED, NOTIONAL):
            prices = dict.fromkeys(assets, args[4]["price_start_minor"]) if arm == BASELINE else {a: (1025, 26812, 224)[i % 3] for i, a in enumerate(assets)}
            _, accounts, specs, _, receipt, backgrounds = initialize(*args[1:4], assets, args[6], args[4], arm, prices)
            state, expected_bg = verify_initialization(receipt, specs, assets, design, args[6], arm, prices)
            self.assertEqual(state["accounts"], {n: asdict(a) for n, a in accounts.items()})
            self.assertEqual(expected_bg, backgrounds)
            bad = copy.deepcopy(receipt)
            owner = next(iter(bad["accounts"]))
            bad["accounts"][owner]["initial_wallets"][assets[0]] += 1
            with self.assertRaisesRegex(ValueError, "receipt differs"):
                verify_initialization(bad, specs, assets, design, args[6], arm, prices)

    def test_background_target_follows_projected_inventory_but_order_cap_unchanged(self):
        args, assets = self.inputs()
        prices = dict.fromkeys(assets, 1025)
        result = initialize(*args[1:4], assets, args[6], args[4], NOTIONAL, prices)
        for asset, bg in result[-1].items():
            self.assertNotEqual(bg["initial_shares"], args[3]["initial_shares"])
            for key in ("target_range_lots", "max_order_lots", "urgency_bps", "seed"):
                self.assertEqual(bg[key], args[3][key])

    def test_invalid_or_implicit_price_change_and_boolean_prices_rejected(self):
        args, assets = self.inputs()
        prices = dict.fromkeys(assets, 1025)
        for arm, changed in ((None, prices), ("other", prices), (BASELINE, prices),
                             (FIXED, {assets[0]: 1025}), (FIXED, dict.fromkeys(assets, True)),
                             (FIXED, dict.fromkeys(assets, 0))):
            with self.subTest(arm=arm), self.assertRaises(ValueError):
                initialize(*args[1:4], assets, args[6], args[4], arm, changed)

    def test_disabled_contract_preserves_entire_previous_simulation(self):
        args, kwargs, _, _ = execution_fixture()
        for scale in (0.0, 1.0):
            changed = dict(kwargs, target_feedback_scale=scale, quote_feedback_scale=scale)
            self.assertEqual(simulate_portfolio(*args, **changed), original(*args, **changed))

    def test_raw_prices_do_not_accept_evaluator_targets_as_decision_input(self):
        args, kwargs, _, _ = execution_fixture()
        prices = dict.fromkeys(args[0], 1025)
        for arm in (FIXED, NOTIONAL):
            expected = simulate_portfolio(*args, **kwargs, resource_arm=arm, initial_prices_minor=prices)
            changed = copy.deepcopy(args)
            for rows in changed[0].values():
                for row in rows:
                    row["observed_return"] = 999.0
            self.assertEqual(simulate_portfolio(*changed, **kwargs, resource_arm=arm, initial_prices_minor=prices), expected)

    def test_all_days_independent_orders_cash_shares_fees_and_final_resources(self):
        args, kwargs, shocks, response = execution_fixture()
        assets = sorted(args[0])
        prices = {a: (1025, 26812, 224)[i % 3] for i, a in enumerate(assets)}
        for arm in (FIXED, NOTIONAL):
            sim = simulate_portfolio(*args, **kwargs, resource_arm=arm, initial_prices_minor=prices)
            specs = sim["participant_specs"]
            state, backgrounds = verify_initialization(sim["summary"]["resource_unit_initialization"], specs,
                assets, dict(core=args[2], background=args[3], venue=args[4]), args[6], arm, prices)
            histories = {a: [] for a in assets}
            streaks = {n: dict(sign=0, streak=0) for n,s in specs.items() if s["kind"] == "strategy"}
            for session, day in enumerate(sim["trace"]):
                independent_covariance(day, histories, args[5])
                verify_day(day, state, specs, args[4], backgrounds, response, shocks, None,
                    kwargs["issuer_valuation"], None, args[6], streaks, session, 1.0, 1.0)
                state = audit_portfolio_day(day, state, args[4], session, dense=True)
                for asset, call in day["portfolio_auction"]["asset_calls"].items():
                    histories[asset].append(dict(trade_date=day["trade_date"], price_before_minor=call["price_before_minor"], price_after_minor=call["price_after_minor"]))
            verify_final_resources(state, sim["summary"])


if __name__ == "__main__":
    unittest.main()
