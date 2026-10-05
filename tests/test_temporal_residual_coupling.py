import copy
from fractions import Fraction
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from test_fixed_marginal_risk import fixture
from test_public_factor_channels import fixture as execution_fixture
from research.simulation.issuer_valuation import build_lagged_risk
from research.simulation.public_market_factor import build_features
from research.simulation.temporal_public_information_risk import build_path as old_path
from research.simulation.portfolio_temporal_information_risk import simulate_portfolio as old_simulate
from research.simulation.temporal_residual_coupling import (build_path, source_windows, choose_index,
    selected_windows, validate_issuer_valuation, subset)
from research.simulation.audit_temporal_residual_coupling import (independent_windows,
    independent_selection, independent_row)
from research.simulation.portfolio_temporal_residual_coupling import simulate_portfolio


class ResidualCouplingTests(unittest.TestCase):
    def test_rejection_sampling_does_not_use_biased_modulo_tail(self):
        hashes = ["f" * 16 + "0" * 48, "0" * 15 + "2" + "1" * 48]
        with patch("research.simulation.temporal_residual_coupling.digest", side_effect=hashes):
            result = choose_index("test", 3)
        self.assertEqual(result, {"selected_index": 2, "rejection_attempt": 1, "draw_sha256": hashes[1]})
        with self.assertRaises(ValueError):
            choose_index("test", 1)

    def test_ties_keep_full_interval_and_exact_uniform_moments(self):
        days = ["2021-01-04", "2021-01-05", "2021-01-06"]
        factor = {"trade_date": "2021-01-08", "signal_cutoff_date": "2021-01-06", "beta_fraction": [0, 1],
            "history": [{"trade_date": d, "stock_return": 4.0, "market_return": float(x)}
                for d, x in zip(days, (-1, 0, 1), strict=True)]}
        features = {"a": [factor], "b": [copy.deepcopy(factor)]}
        windows = source_windows(features)
        independent = independent_windows({"stocks": ["a", "b"], "dates": ["2021-01-08"]}, features)
        self.assertEqual(windows, independent)
        self.assertEqual(windows[0]["rank_intervals_by_stock"]["a"], [[0, 3]] * 3)
        low, size = windows[0]["rank_intervals_by_stock"]["a"][0]
        a, b = Fraction(low, 3), Fraction(low + size, 3)
        self.assertEqual((a + b) / 2, Fraction(1, 2))
        self.assertEqual((a * a + a * b + b * b) / 3, Fraction(1, 3))

    def test_insufficient_common_history_fails_without_independent_fallback(self):
        risk, features, params = fixture()
        altered = copy.deepcopy(features)
        for row in altered[sorted(altered)[-1]]:
            for i, item in enumerate(row["history"]):
                item["trade_date"] = "2018-01-" + f"{i + 1:02}"
        with self.assertRaises(ValueError):
            source_windows(altered)

    def test_disabled_paths_and_all_sixteen_full_execution_objects_match_original(self):
        args, shocks, industry, response, source, issuer, factor = execution_fixture()
        risk = build_lagged_risk(source, args[0], issuer["parameters"]["history_window"])
        features = build_features(source, args[0], factor["parameters"])
        for mode in ("market_independent", "market_shared"):
            for delivery in ("masked", "public"):
                path = build_path(risk, features, "compatibility", issuer["parameters"], mode, "common", delivery)
                self.assertEqual(path, old_path(risk, features, "compatibility", issuer["parameters"], mode, "common", delivery))
                for anchor in (None, {"background_anchor": "current_inventory", "strategy_wait": "original"}):
                    for scale in (0.0, 1.0):
                        kwargs = dict(scenario_shocks=shocks, industry_shocks=industry, background_response=response,
                            issuer_valuation=path, quantity_controls=anchor, target_feedback_scale=scale, quote_feedback_scale=scale)
                        self.assertEqual(simulate_portfolio(*args, **kwargs), old_simulate(*args, **kwargs))

    def test_global_draw_and_universe_survive_basket_subsetting(self):
        risk, features, params = fixture()
        path = build_path(risk, features, "global", params, "market_shared", "market", "public", "residual")
        windows = path["residual_coupling_parameters"]["windows"]
        for stock in sorted(risk):
            split = subset(path, [stock])
            self.assertEqual(split["residual_coupling_parameters"], path["residual_coupling_parameters"])
            calendar = [(r["trade_date"], r["signal_cutoff_date"], "unused") for r in risk[stock]]
            self.assertEqual(validate_issuer_valuation([stock], calendar, split), split)
        self.assertEqual(len(windows), len(risk[sorted(risk)[0]]))

    def test_independent_source_grouping_and_exact_draw_reconstruction(self):
        risk, features, params = fixture()
        stocks = sorted(risk)
        cfg = {"stocks": stocks, "dates": [r["trade_date"] for r in risk[stocks[0]]]}
        windows = independent_windows(cfg, features)
        self.assertEqual(windows, source_windows(features))
        choices = [independent_selection(w, "residual") for w in windows]
        self.assertEqual(choices, selected_windows(windows, "residual"))
        for mode in ("market_independent", "market_shared"):
            coupled = build_path(risk, features, "test", params, mode, "market", "masked", "residual")
            old = old_path(risk, features, "test", params, mode, "market", "masked")
            for stock in stocks:
                expected = [independent_row(r, w, c, stock, params)
                    for r, w, c in zip(old["by_stock"][stock], windows, choices, strict=True)]
                self.assertEqual(coupled["by_stock"][stock], expected)

    def test_all_market_and_individual_risk_coefficients_are_unchanged(self):
        risk, features, params = fixture()
        for mode in ("market_independent", "market_shared"):
            for delivery in ("masked", "public"):
                old = old_path(risk, features, "risk", params, mode, "market", delivery)
                new = build_path(risk, features, "risk", params, mode, "market", delivery, "residual")
                for stock in risk:
                    for a, b in zip(old["by_stock"][stock], new["by_stock"][stock], strict=True):
                        self.assertEqual({k: v for k, v in a["risk_budget"].items() if k != "residual_raw_shift_bps"},
                            {k: v for k, v in b["risk_budget"].items() if k != "residual_raw_shift_bps"})
                        self.assertEqual(a.get("public_information_budget"), b.get("public_information_budget"))

    def test_future_dates_forged_ranks_risk_and_metadata_are_rejected(self):
        risk, features, params = fixture()
        path = build_path(risk, features, "risk", params, "market_shared", "market", "public", "residual")
        stocks = sorted(risk)
        calendar = [(r["trade_date"], r["signal_cutoff_date"], "unused") for r in risk[stocks[0]]]
        for field in ("future", "rank", "risk", "extra"):
            altered = copy.deepcopy(path)
            row = altered["by_stock"][stocks[0]][0]
            if field == "future":
                altered["residual_coupling_parameters"]["windows"][0]["common_source_dates"][-1] = row["trade_date"]
            elif field == "rank":
                row["residual_coupling"]["selected_rank_lower"] += 1
            elif field == "risk":
                row["risk_budget"]["total_amplitude_bps"] += 1
            else:
                row["unknown_input"] = 0
            with self.assertRaises(ValueError):
                validate_issuer_valuation(stocks, calendar, altered)

    def test_new_coupled_execution_never_uses_target_return(self):
        args, shocks, industry, response, source, issuer, factor = execution_fixture()
        risk = build_lagged_risk(source, args[0], issuer["parameters"]["history_window"])
        features = build_features(source, args[0], factor["parameters"])
        path = build_path(risk, features, "target-isolation", issuer["parameters"], "market_shared", "market", "public", "residual")
        actual = simulate_portfolio(*args, issuer_valuation=path)
        for value in (None, 5.0):
            changed = copy.deepcopy(args)
            for rows in changed[0].values():
                for row in rows:
                    row["observed_return"] = value
            self.assertEqual(simulate_portfolio(*changed, issuer_valuation=path), actual)


if __name__ == "__main__":
    unittest.main()
