import copy
import unittest

from test_agent_decision_trace import fixture
from research.semantic.source_case_facts import canonical,digest,normalize_facts
from research.simulation.source_case_decision_link import load_mapping
from research.simulation.run_source_case_decisions import run_case,pair


def sample():
    case={"case_id":"qa_test","source_kind":"company_reply_workbook",
        "available_at":"2020-07-24T23:59:59.999999+08:00",
        "segments":[{"source":"question","text":"是否会收购新的公司？"},
                    {"source":"reply","text":"公司通过产业基金间接持有国盾量子。审批是否通过仍不确定。"}]}
    case["case_text_sha256"]=digest(canonical(case["segments"]))
    record=normalize_facts(case,canonical({"facts":[{"kind":"static_information","status":"static",
        "claim":"公司间接持有股份。","evidence":[{"source":"reply","quote":"公司通过产业基金间接持有国盾量子。"}]}]}).decode())
    return case,record


class SourceCaseDecisionStudyTest(unittest.TestCase):
    def test_static_company_fact_does_not_force_new_trade_but_keywords_can(self):
        case,record=sample();mapping=load_mapping();mechanism=fixture()
        no_text=run_case(case,record,mapping,mechanism,7,"no_text")
        reviewed=run_case(case,record,mapping,mechanism,7,"reviewed_llm")
        keyword=run_case(case,record,mapping,mechanism,7,"keywords")
        comparison=pair(no_text,reviewed)
        self.assertTrue(all(x["requests_changed"]==x["actual_fills_changed"]==0
                            for x in comparison["role_changes"].values()))
        difference=pair(no_text,keyword)
        self.assertTrue(any(x["requests_changed"]>0 for x in difference["role_changes"].values()))
        self.assertEqual(len(reviewed["source_links"]),18)
        self.assertEqual(len(reviewed["decision_explanations"]),216)

    def test_same_seed_text_modes_are_identical_before_source_is_available(self):
        case,record=sample();mapping=load_mapping();mechanism=fixture()
        record["facts"][0]["kind"]="approval_uncertainty"
        baseline=run_case(case,record,mapping,mechanism,23,"no_text")
        reviewed=run_case(case,record,mapping,mechanism,23,"reviewed_llm")
        prior=0
        for i,link in enumerate(reviewed["source_links"]):
            if not link["source_visible"]:
                prior+=1
                left=baseline["model_result"]["trace"][i]
                right=reviewed["model_result"]["trace"][i]
                self.assertEqual(left["decisions"],right["decisions"])
                self.assertEqual(left["portfolio_auction"],right["portfolio_auction"])
                self.assertFalse(any(right["observations"][a]["text_uncertainty"] for a in ("A","B","C")))
        self.assertEqual(prior,3)
        self.assertTrue(any(x["requests_changed"]>0 for x in pair(baseline,reviewed)["role_changes"].values()))

    def test_complete_path_seed_is_reproducible_and_information_age_is_bounded(self):
        case,record=sample();record["facts"][0]["kind"]="approval_uncertainty"
        mapping=load_mapping();mechanism=fixture()
        first=run_case(case,record,mapping,mechanism,11,"reviewed_llm")
        self.assertEqual(first,run_case(case,record,mapping,mechanism,11,"reviewed_llm"))
        self.assertEqual(sum(link["information_active"] for link in first["source_links"]),6)
        for link,day in zip(first["source_links"],first["model_result"]["trace"]):
            if not link["information_active"]:
                self.assertTrue(all(o["text_signal"]==o["text_uncertainty"]==0
                                    for o in day["observations"].values()))
            self.assertEqual(link["signal_cutoff_at"][:10],day["signal_cutoff_date"])


if __name__=="__main__":
    unittest.main()
