import copy
from pathlib import Path
import statistics
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_fixed_marginal_risk import fixture
from test_public_factor_channels import fixture as execution_fixture
from research.simulation.issuer_valuation import validate_risk_row as legacy_risk, history_hash
from research.simulation.public_market_factor import feature as legacy_feature, build_features
from research.simulation.issuer_valuation import build_lagged_risk
from research.simulation.public_information_risk import build_path as legacy_path
from research.simulation.portfolio_public_information_risk import simulate_portfolio as legacy_simulate
from research.simulation.temporal_risk_inputs import feature, validate_risk_row, check_steps
from research.simulation.temporal_public_information_risk import build_path, validate_issuer_valuation
from research.simulation.portfolio_temporal_information_risk import simulate_portfolio


class TemporalInformationRiskTests(unittest.TestCase):
    def test_stale_history_retains_dates_and_rejects_future(self):
        risk, factors, p = fixture()
        row = copy.deepcopy(risk["a"][0])
        f = copy.deepcopy(factors["a"][0])
        cutoff = "2019-10-25"
        row["signal_cutoff_date"] = cutoff
        f["signal_cutoff_date"] = cutoff
        validate_risk_row(row, row["trade_date"], cutoff, p["history_window"])
        actual = feature(f["history"], f["trade_date"], cutoff, p["history_window"], "sh.000300")
        self.assertEqual(actual["history"][-1]["trade_date"], "2019-10-24")
        with self.assertRaises(ValueError):
            legacy_risk(row, row["trade_date"], cutoff, p["history_window"])
        with self.assertRaises(ValueError):
            legacy_feature(f["history"], f["trade_date"], cutoff, p["history_window"], "sh.000300")
        actual["history"][-1]["trade_date"] = "2019-10-28"
        with self.assertRaises(ValueError):
            feature(actual["history"], f["trade_date"], cutoff, p["history_window"], "sh.000300")

    def test_strict_clock_features_and_all_risk_paths_match_legacy_exactly(self):
        risk, factors, p = fixture()
        for stock in risk:
            for f in factors[stock]:
                self.assertEqual(feature(f["history"], f["trade_date"], f["signal_cutoff_date"], p["history_window"], "sh.000300"), f)
        for mode in ("market_independent", "market_shared"):
            for delivery in ("masked", "public"):
                self.assertEqual(build_path(risk, factors, "compatibility", p, mode, "common", delivery),
                    legacy_path(risk, factors, "compatibility", p, mode, "common", delivery))

    def test_forged_stale_risk_hash_and_volatility_are_rejected(self):
        risk, factors, p = fixture()
        for field in ("history_sha256", "lagged_stock_volatility"):
            row = copy.deepcopy(risk["a"][0])
            row[field] = "wrong" if field == "history_sha256" else row[field] + .0001
            with self.assertRaises(ValueError):
                validate_risk_row(row, row["trade_date"], row["signal_cutoff_date"], p["history_window"])

    def test_zero_stock_risk_is_known_but_zero_benchmark_beta_is_undefined(self):
        risk, factors, p = fixture()
        h = copy.deepcopy(factors["a"][0]["history"])
        for row in h:
            row["stock_return"] = 0.0
        f = feature(h, "2019-10-28", "2019-10-24", p["history_window"], "sh.000300")
        self.assertEqual(f["source_stock_volatility"], 0)
        for row in h:
            row["market_return"] = 0.0
        with self.assertRaises(ValueError):
            feature(h, "2019-10-28", "2019-10-24", p["history_window"], "sh.000300")

    def test_complete_execution_bridge_all_sixteen_controls(self):
        args, shocks, industry, response, source, issuer, factor = execution_fixture()
        risk = build_lagged_risk(source, args[0], issuer["parameters"]["history_window"])
        factors = build_features(source, args[0], factor["parameters"])
        for mode in ("market_independent", "market_shared"):
            for delivery in ("masked", "public"):
                path = build_path(risk, factors, "compatibility", issuer["parameters"], mode, "common", delivery)
                for anchor in (None, {"background_anchor": "current_inventory", "strategy_wait": "original"}):
                    for scale in (0., 1.):
                        kwargs = dict(scenario_shocks=shocks, industry_shocks=industry, background_response=response,
                            issuer_valuation=path, quantity_controls=anchor, target_feedback_scale=scale, quote_feedback_scale=scale)
                        self.assertEqual(simulate_portfolio(*args, **kwargs), legacy_simulate(*args, **kwargs))

    def test_null_or_modified_evaluation_target_never_changes_simulation(self):
        args, shocks, industry, response, source, issuer, factor = execution_fixture()
        risk = build_lagged_risk(source, args[0], issuer["parameters"]["history_window"])
        factors = build_features(source, args[0], factor["parameters"])
        path = build_path(risk, factors, "target-isolation", issuer["parameters"], "market_shared", "common", "public")
        expected = simulate_portfolio(*args, issuer_valuation=path)
        for target in (None, 5.0):
            changed = copy.deepcopy(args)
            for steps in changed[0].values():
                for row in steps:
                    row["observed_return"] = target
            self.assertEqual(simulate_portfolio(*changed, issuer_valuation=path), expected)

    def test_suspension_continues_next_session_with_explicit_status(self):
        args, shocks, industry, response, source, issuer, factor = execution_fixture()
        risk = build_lagged_risk(source, args[0], issuer["parameters"]["history_window"])
        factors = build_features(source, args[0], factor["parameters"])
        path = build_path(risk, factors, "suspension", issuer["parameters"], "market_shared", "common", "masked")
        changed = copy.deepcopy(args)
        stock = sorted(changed[0])[0]
        changed[0][stock][0]["execution_available"] = False
        changed[0][stock][0]["observed_return"] = None
        result = simulate_portfolio(*changed, issuer_valuation=path)
        first = result["trace"][0]["portfolio_auction"]["asset_calls"][stock]
        self.assertEqual(first["matched_volume"], 0)
        self.assertEqual(first["price_before_minor"], first["price_after_minor"])
        self.assertEqual(len(result["trace"]), len(changed[0][stock]))
        changed[0][stock][0]["execution_available"] = None
        with self.assertRaises(ValueError):
            simulate_portfolio(*changed, issuer_valuation=path)
