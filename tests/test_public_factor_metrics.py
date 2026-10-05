import copy
import unittest
from research.simulation.public_factor_metrics import expected_coverage, summarize
from research.simulation.verify_pre_wuhan_public_factor_channels_2019 import independent_summary


def variants():
    output = []
    for s in ("7", "11", "23", "47", "89"):
        for a in ("initial_inventory", "current_inventory"):
            for f in (1.0, 0.0):
                for p, amount in (("none", 1), ("targets_only", 3), ("quotes_only", 4), ("both", 10)):
                    x = amount + int(s) * f
                    output.append({"seed_id": s, "background_anchor": a, "feedback_scale": f, "public_mode": p,
                        "name": a + str(f) + p, "comparison": {"synthetic": {"mean": x, "standard_deviation": x, "zero_return_fraction": x}},
                        "common_factor_metrics": {"synthetic": {"mean_pairwise_stock_return_correlation": x, "equal_weight_daily_return_std": x}},
                        "joint_checks": {"all_five_pass": False, "values": {"absolute_mean_gap": x, "volatility_ratio": x,
                            "zero_fraction_gap": x, "stock_correlation_gap": x, "portfolio_volatility_ratio": x}}})
    return output


class PublicFactorMetricsTest(unittest.TestCase):
    def test_declared_coverage_is_full_grid_and_separate_nonexecuted_probe_counts(self):
        c = expected_coverage(5, 41, 43, 3, 12, 12)
        self.assertEqual((80, 3280, 141040, 105780, 9520200), tuple(c[k] for k in
            ("conditions", "paths", "full_path_ledger_records", "same_state_public_records", "paired_private_receipt_values")))
        self.assertEqual(423120, c["quote_only_same_state_strategy_targets"])
        self.assertEqual(1269360, c["target_only_same_state_backgrounds"])
        with self.assertRaises(ValueError):
            expected_coverage(True, 41, 43, 3, 12, 12)

    def test_interaction_is_computed_within_every_seed_before_summary(self):
        r = summarize(variants())
        for rows in r["paired_public_channel_effects_and_interactions"].values():
            for row in rows:
                self.assertEqual(4, row["target_quote_interaction|1.0"]["mean"])
                self.assertEqual(4, row["target_quote_interaction|0.0"]["mean"])
                self.assertEqual(0, row["both|own_feedback_interaction"]["mean"])
        rows = variants()
        with self.assertRaises(ValueError):
            summarize(rows[:-1])
        with self.assertRaises(ValueError):
            summarize(rows + [copy.deepcopy(rows[0])])

    def test_undefined_correlation_remains_null_and_cannot_pass_nonworse(self):
        rows = variants()
        for r in rows:
            r["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"] = None
            r["joint_checks"]["values"]["stock_correlation_gap"] = None
        r = summarize(rows)
        for summary in r["seed_summary"].values():
            self.assertIsNone(summary["metric_medians"]["stock_correlation"])
            self.assertEqual(0, summary["all_five_nonworse_seeds"])
        for row in r["paired_public_channel_effects_and_interactions"]["initial_inventory"]:
            self.assertIsNone(row["both|own_feedback_interaction"]["stock_correlation"])

    def test_independent_summary_recovers_all_paired_changes_and_seed_medians(self):
        rows = variants()
        measured = {r["seed_id"] + "|" + r["name"]: {"comparison": r["comparison"],
            "common_factor_metrics": r["common_factor_metrics"], "joint_values": r["joint_checks"]["values"],
            "all_five_pass": r["joint_checks"]["all_five_pass"]} for r in rows}
        cells = [{k: r[k] for k in ("name", "background_anchor", "feedback_scale", "public_mode")} for r in rows if r["seed_id"] == "7"]
        summary, effects = independent_summary(measured, cells, ["7", "11", "23", "47", "89"])
        self.assertEqual({"seed_summary": summary, "paired_public_channel_effects_and_interactions": effects}, summarize(rows))
