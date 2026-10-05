import copy
import unittest

from research.semantic.source_case_facts import canonical,digest,normalize_facts
from research.simulation.source_case_decision_link import load_mapping,mapped_information,case_inputs


def case():
    value={"case_id":"qa_test","source_kind":"company_reply_workbook",
        "available_at":"2020-07-24T23:59:59.999999+08:00",
        "segments":[{"source":"question","text":"是否会收购新的公司？"},
                    {"source":"reply","text":"公司通过产业基金间接持有国盾量子。请关注公告。"}]}
    value["case_text_sha256"]=digest(canonical(value["segments"]))
    return value


def static_record(c):
    return normalize_facts(c,canonical({"facts":[{"kind":"static_information","status":"static",
        "claim":"公司间接持有股份。","evidence":[{"source":"reply","quote":"公司通过产业基金间接持有国盾量子。"}]}]}).decode())


class SourceCaseDecisionLinkTest(unittest.TestCase):
    def test_future_source_and_no_text_do_not_produce_numeric_effect(self):
        c=case();r=static_record(c);m=load_mapping()
        before=mapped_information(c,r,m,"keywords","2020-07-24T12:00:00+08:00")
        self.assertFalse(before["available"]);self.assertEqual(before["signal"],0)
        no_text=mapped_information(c,r,m,"no_text","2020-07-25T00:00:00+08:00")
        self.assertEqual(no_text["signal"],0);self.assertEqual(no_text["uncertainty"],0)
        self.assertEqual(no_text["evidence"],[])

    def test_static_holding_has_no_new_llm_economic_increment_but_keyword_baseline_can_trigger(self):
        c=case();r=static_record(c);m=load_mapping()
        llm=mapped_information(c,r,m,"reviewed_llm","2020-07-25T00:00:00+08:00")
        keyword=mapped_information(c,r,m,"keywords","2020-07-25T00:00:00+08:00")
        self.assertEqual(llm["signal"],0)
        self.assertEqual(keyword["signal"],0.25)
        self.assertEqual(keyword["keyword_matches"],["持有"])
        self.assertNotIn("收购",keyword["keyword_matches"])

    def test_fact_splitting_does_not_amplify_assumed_signal(self):
        c=case();c["segments"][1]["text"]="第一项审批不确定。第二项审批也不确定。"
        c["case_text_sha256"]=digest(canonical(c["segments"]))
        payload={"facts":[{"kind":"approval_uncertainty","status":"uncertain","claim":"审批有不确定性。",
             "evidence":[{"source":"reply","quote":quote}]} for quote in
            ("第一项审批不确定。","第二项审批也不确定。")]}
        r=normalize_facts(c,canonical(payload).decode());m=load_mapping()
        info=mapped_information(c,r,m,"reviewed_llm","2020-07-25T00:00:00+08:00")
        self.assertEqual(info["uncertainty"],0.6)
        self.assertEqual(len(info["used_fact_indices"]),2)

    def test_unknown_mapping_is_recorded_and_not_filled_with_an_estimated_effect(self):
        c=case();m=load_mapping();r=static_record(c);r["facts"][0]["kind"]="other"
        info=mapped_information(c,r,m,"reviewed_llm","2020-07-25T00:00:00+08:00")
        self.assertEqual(info["unresolved_fact_indices"],[0])
        self.assertEqual(info["used_fact_indices"],[])
        self.assertIsNone(m["fact_mapping"]["other"]["signal"])

    def test_full_source_calendar_and_asset_placebo_preserve_visibility(self):
        c=case();r=static_record(c);r["facts"][0]["kind"]="approval_uncertainty";m=load_mapping()
        original,links=case_inputs(c,r,m,"reviewed_llm")
        placebo,placebo_links=case_inputs(c,r,m,"llm_asset_placebo")
        self.assertEqual(len(original["A"]),18)
        self.assertTrue(all(l["source_visible"]==p["source_visible"] for l,p in zip(links,placebo_links)))
        for a,b in zip(original["A"],placebo["B"]):
            self.assertEqual(a["text_signal"],b["text_signal"])
            self.assertEqual(a["text_uncertainty"],b["text_uncertainty"])
            self.assertLess(a["signal_cutoff_date"],a["execution_reference_date"])
            self.assertLess(a["execution_reference_date"],a["trade_date"])
        self.assertTrue(any(l["source_visible"] is False for l in links))
        self.assertEqual(sum(l["information_active"] for l in links),6)


if __name__=="__main__":
    unittest.main()
