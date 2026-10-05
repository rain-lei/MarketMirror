"""Apply the pre-sample sixth-cohort support and performance gate."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .signal_validation import EVENT_TYPES


POLICY = Path(__file__).resolve().parents[1] / "configs/wuhan_v6_evaluation_policy.json"
EXPECTED_THRESHOLDS = {
    "max_parse_error_rate": 0.05,
    "min_event_detection_f1": 0.7,
    "min_supported_type_macro_f1": 0.6,
    "must_not_underperform_keyword": True,
    "min_reference_positive_replies": 30,
    "min_reference_replies_per_event_type": 5,
}
EXPECTED_BOOTSTRAP = {"unit": "company", "replicates": 5000, "seed": 20260927}


def load_policy(path: Path = POLICY) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    thresholds = policy.get("thresholds")
    bootstrap = policy.get("bootstrap")
    if (policy.get("protocol") != "wuhan-sixth-company-source-first-evaluation-v6"
            or policy.get("sample_role") != "sixth_disjoint_company_evaluation"
            or type(policy.get("sample_size")) is not int or policy["sample_size"] != 250
            or policy.get("selection_rule")
            != "visible_question_topic_pair_sha256_after_excluding_five_cohorts"
            or policy.get("event_types") != list(EVENT_TYPES)
            or policy.get("reviewer_kind") != "single_ai_assistant"
            or policy.get("review_before_model_outputs") is not True
            or policy.get("model_outputs_previously_seen") is not False
            or policy.get("human_gold_ready") is not False
            or policy.get("external_preregistration") is not False
            or thresholds != EXPECTED_THRESHOLDS
            or type(thresholds.get("must_not_underperform_keyword")) is not bool
            or any(type(thresholds.get(name)) is not int for name in (
                "min_reference_positive_replies", "min_reference_replies_per_event_type"))
            or not isinstance(bootstrap, dict)
            or any(bootstrap.get(name) != value
                   for name, value in EXPECTED_BOOTSTRAP.items())
            or type(bootstrap.get("replicates")) is not int
            or type(bootstrap.get("seed")) is not int
            or not isinstance(bootstrap.get("interpretation"), str)
            or not bootstrap["interpretation"].strip()
            or not isinstance(policy.get("review_rule"), str)
            or not policy["review_rule"].strip()
            or not isinstance(policy.get("score_interpretation"), str)
            or not policy["score_interpretation"].strip()):
        raise ValueError("sixth evaluation policy identity or threshold schema differs")
    return policy


def evaluate_gate(model: dict, keyword: dict, total_items: int, *,
                  source_first_review: bool, zero_company_overlap: bool,
                  policy: dict | None = None) -> dict:
    """A high F1 cannot pass if any reference class has too few positive replies."""
    policy = load_policy() if policy is None else policy
    thresholds = policy["thresholds"]
    if type(total_items) is not int or total_items != policy["sample_size"]:
        raise ValueError("sixth gate item count differs from the pre-sample policy")
    if type(source_first_review) is not bool or type(zero_company_overlap) is not bool:
        raise ValueError("sixth gate provenance flags must be booleans")
    if model.get("status") != "scored" or keyword.get("status") != "scored":
        raise ValueError("sixth gate requires complete scored comparison objects")
    per_type = model["event_type_by_class"]
    if set(per_type) != set(EVENT_TYPES):
        raise ValueError("sixth score does not cover all five event types")
    supports = {name: per_type[name]["tp"] + per_type[name]["fn"] for name in EVENT_TYPES}
    supported = [name for name in EVENT_TYPES if supports[name] > 0]
    macro = (sum(per_type[name]["f1"] for name in supported) / len(supported)
             if supported else None)
    if (model.get("supported_event_types") != supported
            or (macro is None) != (model.get("event_type_macro_f1_supported") is None)
            or macro is not None and not math.isclose(
                macro, model["event_type_macro_f1_supported"], rel_tol=0, abs_tol=1e-12)):
        raise ValueError("sixth supported-type macro score is inconsistent")
    positives = model["event_detection"]["tp"] + model["event_detection"]["fn"]
    keyword_positives = (keyword["event_detection"]["tp"]
                         + keyword["event_detection"]["fn"])
    if keyword_positives != positives:
        raise ValueError("model and keyword scores use different reference positives")
    detection = model["event_detection"]["f1"]
    keyword_detection = keyword["event_detection"]["f1"]
    checks = {
        "source_first_review_complete": source_first_review,
        "company_sample_disjoint": zero_company_overlap,
        "all_items_scored": (
            model["scored_items"] == total_items
            and keyword["scored_items"] == total_items
            and model["label_audit"]["reviewed_items"] == total_items
            and keyword["label_audit"]["reviewed_items"] == total_items
            and model["prediction_audit"]["prediction_rows"] == total_items
            and keyword["prediction_audit"]["prediction_rows"] == total_items),
        "parse_error_rate": model["parse_error_items"] / total_items
        <= thresholds["max_parse_error_rate"],
        "reference_positive_reply_support": positives
        >= thresholds["min_reference_positive_replies"],
        "all_five_reference_type_support": all(
            count >= thresholds["min_reference_replies_per_event_type"]
            for count in supports.values()),
        "event_detection_f1": detection >= thresholds["min_event_detection_f1"],
        "event_detection_not_below_keyword": detection >= keyword_detection,
        "supported_type_macro_f1": (macro is not None
                                    and macro >= thresholds["min_supported_type_macro_f1"]),
    }
    return {"checks": checks, "passed": all(checks.values()),
            "reference_positive_replies": positives,
            "reference_reply_support_by_type": supports,
            "thresholds": thresholds,
            "scope": policy["score_interpretation"]}
