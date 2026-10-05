import json
import tempfile
import unittest
from pathlib import Path

from research.semantic import wuhan_v6_gate as target


def scores(supports, *, model_f1=0.8, keyword_f1=0.5, parse_errors=0):
    per_type = {name: {"tp": count, "fn": 0, "f1": 0.8}
                for name, count in supports.items()}
    common = {"status": "scored", "scored_items": 250,
              "label_audit": {"reviewed_items": 250},
              "prediction_audit": {"prediction_rows": 250},
              "event_detection": {"tp": 40, "fn": 10}}
    model = {**common,
             "event_detection": {**common["event_detection"], "f1": model_f1},
             "parse_error_items": parse_errors,
             "event_type_by_class": per_type,
             "supported_event_types": list(supports),
             "event_type_macro_f1_supported": 0.8}
    keyword = {**common,
               "event_detection": {**common["event_detection"], "f1": keyword_f1}}
    return model, keyword


class WuhanV6GateTest(unittest.TestCase):
    def test_high_f1_cannot_pass_with_one_liquidity_reference(self):
        model, keyword = scores({"regulation": 5, "liquidity": 1,
                                 "earnings": 10, "governance": 5, "other": 10})
        result = target.evaluate_gate(model, keyword, 250,
                                      source_first_review=True, zero_company_overlap=True)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["all_five_reference_type_support"])
        self.assertEqual(result["reference_reply_support_by_type"]["liquidity"], 1)

    def test_gate_passes_only_with_full_support_and_provenance(self):
        model, keyword = scores({"regulation": 5, "liquidity": 5,
                                 "earnings": 10, "governance": 5, "other": 10})
        result = target.evaluate_gate(model, keyword, 250,
                                      source_first_review=True, zero_company_overlap=True)
        self.assertTrue(result["passed"])
        without_source_first = target.evaluate_gate(
            model, keyword, 250, source_first_review=False, zero_company_overlap=True)
        self.assertFalse(without_source_first["passed"])
        self.assertFalse(without_source_first["checks"]["source_first_review_complete"])

    def test_parse_errors_and_modified_policy_cannot_silently_pass(self):
        model, keyword = scores({"regulation": 5, "liquidity": 5,
                                 "earnings": 10, "governance": 5, "other": 10},
                                parse_errors=13)
        result = target.evaluate_gate(model, keyword, 250,
                                      source_first_review=True, zero_company_overlap=True)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["parse_error_rate"])
        with tempfile.TemporaryDirectory() as directory:
            policy = target.load_policy()
            policy["thresholds"]["min_reference_replies_per_event_type"] = 0
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs"):
                target.load_policy(path)
            policy["thresholds"]["min_reference_replies_per_event_type"] = 5
            policy["thresholds"]["must_not_underperform_keyword"] = 1
            path.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs"):
                target.load_policy(path)


if __name__ == "__main__":
    unittest.main()
