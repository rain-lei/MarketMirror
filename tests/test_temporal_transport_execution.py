import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_fixed_marginal_risk import fixture
from research.simulation.temporal_transport_study import (CELLS, DESIGN, ROOT, read, validate_protocol, seed_paths,
    audit_scope, summarize, path_key)
from research.simulation.audit_temporal_transport_paths import verify_prepared
from research.simulation.temporal_residual_coupling import source_windows
from research.simulation.audit_temporal_transport_summary import verify_contrasts, identity


def protocol_fixture():
    design = read(DESIGN)
    original = read(ROOT / design["original_numerical_protocol_path"])
    cfg = {**copy.deepcopy(design), "version": "temporal-full-factorial-transport-execution-2022-v1",
        "formal_execution_protocol_frozen": True, "evaluation_mask_frozen": True}
    return cfg, design, original


def paths_fixture():
    risk, features, params = fixture()
    cfg, _, _ = protocol_fixture()
    cfg.update(stocks=sorted(risk), dates=[row["trade_date"] for row in risk["a"]], issuer_parameters=params)
    seed = cfg["seeds"][0]
    _, _, paths = seed_paths(cfg, risk, features, seed)
    return cfg, risk, features, seed, paths, source_windows(features)


def summary_fixture():
    cfg, _, _ = protocol_fixture()
    result = []
    for seed in cfg["seeds"]:
        for cell in CELLS:
            dep = int(cell["residual_dependence"] == "historical_rank")
            anchor = int(cell["background_anchor"] == "current_inventory")
            delivery = {"masked": 0, "public": 1, "whole_public": 3}[cell["delivery"]]
            x = 19 * anchor + 3 * cell["feedback_scale"] + 2 * dep + delivery * (1 + 11 * dep + 17 * anchor)
            result.append({"seed_id": seed["seed_id"], **cell,
                "comparison": {"synthetic": {"mean": x, "standard_deviation": x + 1, "zero_return_fraction": x + 2}},
                "common_factor_metrics": {"synthetic": {"mean_pairwise_stock_return_correlation": x + 3,
                    "equal_weight_daily_return_std": x + 4}},
                "joint_checks": {"all_five_pass": True, "values": {"absolute_mean_gap": x,
                    "volatility_ratio": x + 1, "zero_fraction_gap": x, "stock_correlation_gap": x,
                    "portfolio_volatility_ratio": x + 1}}})
    return cfg, result


class TransportExecutionTests(unittest.TestCase):
    def test_full_factorial_matches_preregistered_order(self):
        cfg, design, original = protocol_fixture()
        validate_protocol(cfg, design, original)
        self.assertEqual(CELLS, design["variants"])
        self.assertEqual(len(CELLS), 48)
        self.assertEqual(len({path_key(cell) for cell in CELLS}), 12)

    def test_numerical_parameter_change_rejected(self):
        cfg, design, original = protocol_fixture()
        cfg["model_design"]["venue"]["fee_bps"] += 1
        with self.assertRaisesRegex(ValueError, "frozen numerical"):
            validate_protocol(cfg, design, original)

    def test_boolean_number_substitution_rejected(self):
        cfg, design, original = protocol_fixture()
        cfg["issuer_parameters"]["risk_multiplier"] = True
        with self.assertRaisesRegex(ValueError, "frozen numerical"):
            validate_protocol(cfg, design, original)

    def test_reduced_grid_and_open_semantics_rejected(self):
        for key, value in (("variants", CELLS[:-1]), ("semantic_gate_enabled", True),
                ("formal_execution_protocol_frozen", False), ("candidate_selected_as_default", True)):
            cfg, design, original = protocol_fixture()
            cfg[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_protocol(cfg, design, original)

    def test_all_twelve_paths_independently_reconstructed(self):
        cfg, risk, features, seed, paths, windows = paths_fixture()
        counts = verify_prepared(cfg, paths, risk, features, seed, windows)
        self.assertEqual(counts["independently_rebuilt_budget_rows"], 24)
        self.assertEqual(counts["independently_verified_coupled_innovations"], 12)
        self.assertEqual(counts["independently_integrated_public_budget_rows"], 8)
        self.assertEqual(counts["independently_integrated_whole_public_budget_rows"], 8)

    def test_missing_and_extra_prepared_paths_rejected(self):
        cfg, risk, features, seed, paths, windows = paths_fixture()
        del paths[next(iter(paths))]
        with self.assertRaisesRegex(ValueError, "omitted or added"):
            verify_prepared(cfg, paths, risk, features, seed, windows)

    def test_changed_shared_innovation_rejected(self):
        cfg, risk, features, seed, paths, windows = paths_fixture()
        paths["historical_rank_market_shared_masked"]["by_stock"]["a"][0]["risk_budget"]["market_raw_shift_bps"] += 1
        with self.assertRaisesRegex(ValueError, "innovations"):
            verify_prepared(cfg, paths, risk, features, seed, windows)

    def test_changed_public_scale_rejected_by_independent_integral(self):
        for delivery, budget, scale in (("public", "public_information_budget", "public_market_multiplier"),
                ("whole_public", "whole_information_budget", "whole_raw_multiplier")):
            cfg, risk, features, seed, paths, windows = paths_fixture()
            paths["independent_market_shared_" + delivery]["by_stock"]["a"][0][budget][scale] = .99
            with self.subTest(delivery=delivery), self.assertRaises(ValueError):
                verify_prepared(cfg, paths, risk, features, seed, windows)

    def test_future_sealed_rank_date_rejected(self):
        cfg, risk, features, seed, paths, windows = paths_fixture()
        windows[0]["common_source_dates"][-1] = cfg["dates"][0]
        with self.assertRaisesRegex(ValueError, "past ranks"):
            verify_prepared(cfg, paths, risk, features, seed, windows)

    def test_scope_uses_this_period_missingness(self):
        cfg, _, _ = protocol_fixture()
        joined = {s: [{"observed_return": .01, "execution_available": True} for _ in cfg["dates"]] for s in cfg["stocks"]}
        result = audit_scope(cfg, joined)
        self.assertEqual(result["unknown_target_asset_calls_preserved"], 0)
        self.assertEqual(result["suspended_asset_calls"], 0)
        self.assertEqual(result["complete_wealth_drawdown_risk_accounts"] * 5, 472320)
        joined[cfg["stocks"][0]][0] = {"observed_return": None, "execution_available": False}
        result = audit_scope(cfg, joined)
        self.assertEqual(result["unknown_target_asset_calls_preserved"], 48)
        self.assertEqual(result["suspended_asset_calls"], 48)

    def test_all_contrasts_and_interaction_orientation(self):
        cfg, rows = summary_fixture()
        result = summarize(rows, cfg["seeds"])
        self.assertEqual([len(result[name]) for name in ("coverage_pairs", "residual_dependence_pairs",
            "coverage_dependence_interactions", "coverage_anchor_interactions")], [160, 120, 80, 80])
        for row in result["coverage_dependence_interactions"]:
            self.assertEqual(row["metric_interaction"]["mean"], 33 if row["reference_delivery"] == "masked" else 22)
        for row in result["coverage_anchor_interactions"]:
            self.assertEqual(row["metric_interaction"]["mean"], 51 if row["reference_delivery"] == "masked" else 34)

    def test_summary_rejects_missing_duplicate_and_renamed_cells(self):
        cfg, rows = summary_fixture()
        for bad in (rows[:-1], [rows[0], *rows[2:], rows[0]]):
            with self.assertRaises(ValueError):
                summarize(bad, cfg["seeds"])
        rows[0]["name"] = "another-condition"
        with self.assertRaisesRegex(ValueError, "name or identity"):
            summarize(rows, cfg["seeds"])

    def test_undefined_statistics_remain_undefined(self):
        cfg, rows = summary_fixture()
        rows[0]["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"] = None
        rows[0]["joint_checks"]["values"]["stock_correlation_gap"] = None
        result = summarize(rows, cfg["seeds"])
        self.assertIsNone(result["seed_summary"][rows[0]["name"]]["metric_medians"]["stock_correlation"])
        pairs = [row for row in result["coverage_pairs"] if row["seed_id"] == rows[0]["seed_id"]
            and row["background_anchor"] == rows[0]["background_anchor"] and row["feedback_scale"] == rows[0]["feedback_scale"]
            and row["residual_dependence"] == rows[0]["residual_dependence"] and row["risk_mode"] == rows[0]["risk_mode"]
            and row["reference_delivery"] == "masked"]
        self.assertIsNone(pairs[0]["metric_changes"]["stock_correlation"])
        self.assertFalse(pairs[0]["all_five_gaps_nonworse"])

    def test_independent_signed_contrasts_accept_full_grid(self):
        cfg, rows = summary_fixture()
        summary = summarize(rows, cfg["seeds"])
        rebuilt = {identity(row): {"comparison": row["comparison"], "common_factor_metrics": row["common_factor_metrics"],
            "joint_values": row["joint_checks"]["values"], "all_five_pass": row["joint_checks"]["all_five_pass"]} for row in rows}
        self.assertEqual(verify_contrasts(rows, cfg["seeds"], summary, rebuilt)["coverage_pairs"], 160)

    def test_independent_contrasts_reject_altered_interaction(self):
        cfg, rows = summary_fixture()
        summary = summarize(rows, cfg["seeds"])
        rebuilt = {identity(row): {"comparison": row["comparison"], "common_factor_metrics": row["common_factor_metrics"],
            "joint_values": row["joint_checks"]["values"], "all_five_pass": row["joint_checks"]["all_five_pass"]} for row in rows}
        summary["coverage_dependence_interactions"][0]["metric_interaction"]["mean"] += 1
        with self.assertRaises(ValueError):
            verify_contrasts(rows, cfg["seeds"], summary, rebuilt)


if __name__ == "__main__":
    unittest.main()
