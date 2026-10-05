import copy
import unittest

from test_agent_decision_trace import fixture
from test_source_case_decision_study import sample
from research.simulation.run_source_case_decisions import run_case
from research.simulation.source_case_decision_link import load_mapping
from research.simulation.source_case_fixed_state import (
    compare_archived_state, compare_rows, scaled_observations,
)
from research.simulation.audit_source_case_fixed_state import audit_explanation


class SourceCaseFixedStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case, cls.record = sample()
        cls.mapping, cls.mechanism = load_mapping(), fixture()
        cls.raw = run_case(cls.case, cls.record, cls.mapping, cls.mechanism, 7, "no_text")
        cls.result = compare_archived_state(cls.raw, cls.case, cls.record, cls.mapping,
                                            cls.mechanism["portfolio_case"], cls.mechanism["venue"]["lot_size"])

    def test_before_publication_and_after_duration_have_no_direct_text_effect(self):
        for group in self.result["groups"]:
            if group["checkpoint"] in ("before_publication", "first_inactive"):
                self.assertTrue(all(not p["beliefs_changed"] and not p["targets_changed"]
                                    and not p["requests_changed"] for p in group["comparisons"]))
                self.assertTrue(all(o["text_signal"] == o["text_uncertainty"] == 0
                                    for r in group["rows"] for o in r["observations"].values()))

    def test_fixed_reference_is_not_mutated_and_replays_exactly(self):
        before = copy.deepcopy((self.raw, self.case, self.record, self.mapping))
        repeat = compare_archived_state(self.raw, self.case, self.record, self.mapping,
                                       self.mechanism["portfolio_case"], 100)
        self.assertEqual(repeat, self.result)
        self.assertEqual(before, (self.raw, self.case, self.record, self.mapping))

    def test_common_role_states_share_cash_shares_market_risk_and_memory(self):
        for checkpoint in self.result["checkpoints"]:
            groups = [g for g in self.result["groups"] if g["context"] == "common_role_state"
                      and g["checkpoint"] == checkpoint]
            self.assertEqual(len(groups), 3)
            self.assertEqual(len({g["state_sha256"] for g in groups}), 1)
            for group in groups:
                self.assertEqual(group["state_source_actor"], "aggressive_00")
                self.assertTrue(all(r["explanation"]["parameters"]["role"] == group["role"]
                                    for r in group["rows"]))
                self.assertFalse(group["actual_fills_computed"])

    def test_static_facts_remain_neutral_at_all_strengths_while_keywords_can_change_beliefs(self):
        visible = [g for g in self.result["groups"] if g["checkpoint"] == "first_visible"]
        for group in visible:
            rows = {r["variant"]: r for r in group["rows"]}
            for mode in ("reviewed_llm", "llm_asset_placebo"):
                for strength in ("0.5", "1", "1.5"):
                    self.assertEqual(rows[f"{mode}:{strength}"]["decision"], rows["no_text:1"]["decision"])
        self.assertTrue(any(g["comparisons"][0]["beliefs_changed"] for g in visible))

    def test_strength_clips_both_channels_and_preserves_market_fields(self):
        base = {"A": {"market_signal": 0.25, "text_signal": 0., "text_uncertainty": 0., "text_evidence": "disabled"}}
        joined = {"A": [{"text_signal": -0.9, "text_uncertainty": 0.8, "text_evidence": "source:x"}]}
        obs, link = scaled_observations(base, joined, {}, 0, "reviewed_llm", 1.5)
        self.assertEqual((obs["A"]["text_signal"], obs["A"]["text_uncertainty"]), (-1., 1.))
        self.assertEqual(obs["A"]["market_signal"], base["A"]["market_signal"])
        self.assertEqual(link["mapping_strength"], 1.5)
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                scaled_observations(base, joined, {}, 0, "reviewed_llm", value)
        with self.assertRaises(ValueError):
            scaled_observations(base, joined, {}, 0, "keywords", 1.5)

    def test_corrupt_archived_account_is_rejected(self):
        wrong = copy.deepcopy(self.raw)
        row = next(r for r in wrong["decision_explanations"] if r["session"] == 3)
        row["account_before"]["wallets"]["shared"] += 1
        with self.assertRaises(ValueError):
            compare_archived_state(wrong, self.case, self.record, self.mapping, self.mechanism["portfolio_case"], 100)

    def test_pair_rejects_changed_prestate_and_never_reports_counterfactual_fills(self):
        group = self.result["groups"][0]
        reference, treatment = group["rows"][:2]
        self.assertFalse(compare_rows(reference, treatment)["actual_fills_computed"])
        wrong = copy.deepcopy(treatment)
        wrong["explanation"]["memory_before"]["streak"] += 1
        with self.assertRaises(ValueError):
            compare_rows(reference, wrong)

    def test_pair_rejects_non_text_information_changes(self):
        reference, treatment = self.result["groups"][0]["rows"][:2]
        wrong = copy.deepcopy(treatment)
        wrong["observations"]["A"]["market_signal"] += 0.1
        with self.assertRaises(ValueError):
            compare_rows(reference, wrong)
        wrong = copy.deepcopy(treatment)
        wrong["fixed_state_sha256"] = "incorrect covariance or state"
        with self.assertRaises(ValueError):
            compare_rows(reference, wrong)

    def test_separate_auditor_rejects_false_terms_memory_and_request_direction(self):
        group = next(g for g in self.result["groups"]
                     if g["checkpoint"] == "first_visible" and g["context"] == "within_actor")
        row = group["rows"][0]
        parameters = row["explanation"]["parameters"]
        self.assertTrue(audit_explanation(row, group["state"], parameters))
        for damage in ("term", "memory", "request"):
            wrong = copy.deepcopy(row)
            if damage == "term":
                wrong["explanation"]["belief_terms"]["A"]["market_contribution"] += 0.1
            elif damage == "memory":
                wrong["explanation"]["memory_after"]["streak"] += 1
            else:
                wrong["explanation"]["requested_orders"]["A"]["action"] = "invented_direction"
            with self.assertRaises(ValueError):
                audit_explanation(wrong, group["state"], parameters)


if __name__ == "__main__":
    unittest.main()
