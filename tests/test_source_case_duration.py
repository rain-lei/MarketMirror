import copy
import unittest

from test_agent_decision_trace import fixture
from test_source_case_decision_study import sample
from research.semantic.source_case_facts import canonical, normalize_facts
from research.simulation.run_source_case_decisions import run_case, pair
from research.simulation.source_case_decision_link import load_mapping
from research.simulation.run_source_case_duration import duration_mapping, run_duration


class SourceCaseDurationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case, static_record = sample()
        cls.record = normalize_facts(cls.case, canonical({"facts": [{"kind": "approval_uncertainty",
            "status": "uncertain", "claim": "审批是否通过仍不确定。",
            "evidence": [{"source": "reply", "quote": "审批是否通过仍不确定。"}]}]}).decode())
        cls.static_record = static_record
        cls.mapping, cls.mechanism = load_mapping(), fixture()
        cls.runs = {d: run_duration(cls.case, cls.record, cls.mapping, cls.mechanism, 7, d) for d in (3, 6, 9)}

    def test_registered_durations_have_exact_active_steps_and_do_not_change_base_mapping(self):
        original = copy.deepcopy(self.mapping)
        for duration, run in self.runs.items():
            active = [r["step"] for r in run["source_links"] if r["information_active"]]
            self.assertEqual(active, list(range(3, 3 + duration)))
            for link, day in zip(run["source_links"], run["model_result"]["trace"]):
                self.assertLess(day["signal_cutoff_date"], day["execution_reference_date"])
                self.assertLess(day["execution_reference_date"], day["trade_date"])
                if not link["information_active"]:
                    self.assertTrue(all(o["text_signal"] == o["text_uncertainty"] == 0 for o in day["observations"].values()))
        self.assertEqual(self.mapping, original)
        for value in (0, 1, 12, True, 3.0):
            with self.assertRaises(ValueError):
                duration_mapping(self.mapping, value)

    def test_paths_are_identical_until_the_first_duration_difference(self):
        for session in range(6):
            traces = [r["model_result"]["trace"][session] for r in self.runs.values()]
            self.assertEqual(traces[0], traces[1])
            self.assertEqual(traces[1], traces[2])
        self.assertFalse(self.runs[3]["source_links"][6]["information_active"])
        self.assertTrue(self.runs[6]["source_links"][6]["information_active"])
        self.assertEqual(self.runs[6]["model_result"]["trace"][:9], self.runs[9]["model_result"]["trace"][:9])

    def test_static_information_is_neutral_for_all_durations(self):
        control = run_case(self.case, self.static_record, self.mapping, self.mechanism, 7, "no_text")
        for duration in (3, 6, 9):
            run = run_duration(self.case, self.static_record, self.mapping, self.mechanism, 7, duration)
            self.assertTrue(all(x["targets_changed"] == x["requests_changed"] == x["actual_fills_changed"] == 0
                                for x in pair(control, run)["role_changes"].values()))
        repeat = run_duration(self.case, self.record, self.mapping, self.mechanism, 7, 9)
        self.assertEqual(repeat, self.runs[9])


if __name__ == "__main__":
    unittest.main()
