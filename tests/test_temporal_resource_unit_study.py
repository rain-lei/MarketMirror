import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_temporal_transport_execution import summary_fixture
from research.simulation.temporal_transport_study import CONFIG as PARENT_CONFIG, CELLS as PARENT_CELLS, read
from research.simulation.temporal_resource_unit_study import (
    CELLS, BASELINE, FIXED, NOTIONAL, validate_protocol, resource_audit_scope, summarize_resource_pairs)


def protocol_fixture():
    parent = read(PARENT_CONFIG)
    cfg = {**copy.deepcopy(parent), "version": "temporal-resource-unit-descriptive-study-2022-v1",
        "variants": CELLS, "resource_arms": [BASELINE, FIXED, NOTIONAL], "full_scope_unique_conditions": 720,
        "newly_simulated_conditions": 480, "reused_independently_audited_conditions": 240,
        "same_2022_window_previously_observed": True, "independent_new_period_validation": False,
        "empirical_agent_calibration_complete": False, "historical_exchange_rules_certified": False,
        "prices_reset_using_later_daily_source_quotes": False,
        "background_order_caps_and_target_offsets_held_in_shares": True,
        "initial_reference_date": "2021-12-31", "initial_raw_prices_minor": dict.fromkeys(parent["stocks"], 1025),
        "source_secid_by_stock": {s: "0."+s for s in parent["stocks"]}}
    return cfg, parent


def full_rows():
    cfg, previous = summary_fixture()
    rows = []
    for arm in (BASELINE, FIXED, NOTIONAL):
        for row in previous:
            ci = next(i for i,c in enumerate(PARENT_CELLS) if c["name"] == row["name"])
            rows.append({**copy.deepcopy(row), "resource_arm": arm, "parent_cell_index": ci,
                         "parent_condition_name": row["name"]})
    return cfg, rows


class TemporalResourceStudyTests(unittest.TestCase):
    def test_complete_registered_parent_grid_and_no_default_or_validation_promotion(self):
        cfg, parent = protocol_fixture()
        validate_protocol(cfg, parent)
        self.assertEqual(len(CELLS), 96)
        self.assertEqual(len({c["name"] for c in CELLS}), 96)
        self.assertEqual({c["parent_cell_index"] for c in CELLS}, set(range(48)))
        for key,value in (("variants", CELLS[:-1]), ("independent_new_period_validation", True),
                          ("empirical_agent_calibration_complete", True), ("candidate_selected_as_default", True),
                          ("prices_reset_using_later_daily_source_quotes", True)):
            bad = copy.deepcopy(cfg); bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_protocol(bad, parent)

    def test_role_risk_budget_and_quote_controls_cannot_change_undeclared(self):
        cfg, parent = protocol_fixture()
        for change in ("role", "quote"):
            bad = copy.deepcopy(cfg)
            if change == "role": bad["agents"][0]["risk_budget"] *= 2
            else: bad["model_design"]["venue"]["quote_response_bps"] *= 2
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "undeclared parent"):
                validate_protocol(bad, parent)

    def test_full_per_seed_ledger_source_and_account_denominators(self):
        cfg, parent = protocol_fixture()
        joined = {s: [dict(observed_return=.01, execution_available=True) for _ in parent["dates"]] for s in parent["stocks"]}
        scope = resource_audit_scope(parent, joined)
        self.assertEqual(scope["conditions"], 96)
        self.assertEqual(scope["full_path_ledger_records"] * 5, 1141440)
        self.assertEqual(scope["asset_calls"] * 5, 3424320)
        self.assertEqual(scope["complete_wealth_drawdown_risk_accounts"] * 5, 944640)
        self.assertEqual(scope["independently_verified_initial_source_prices"], 123)

    def test_all_720_pairs_and_36_complete_strata_preserve_undefined_statistics(self):
        cfg, rows = full_rows()
        rows[0]["common_factor_metrics"]["synthetic"]["mean_pairwise_stock_return_correlation"] = None
        result = summarize_resource_pairs(rows, cfg["seeds"])
        self.assertEqual(len(result["resource_pairs"]), 720)
        self.assertEqual(len(result["strata"]), 36)
        self.assertTrue(any(r["metric_changes"]["stock_correlation"] is None for r in result["resource_pairs"]))
        self.assertTrue(any(s["metric_medians"]["stock_correlation"] is None for s in result["strata"]))
        for bad in (rows[:-1], rows[:-1] + [rows[0]]):
            with self.assertRaises(ValueError): summarize_resource_pairs(bad, cfg["seeds"])

    def test_relabeling_parent_identity_rejected(self):
        cfg, rows = full_rows()
        rows[0]["parent_cell_index"] = 1
        with self.assertRaises(ValueError): summarize_resource_pairs(rows, cfg["seeds"])


if __name__ == "__main__":
    unittest.main()
