import unittest

from research.semantic.annotation_pack import canonical_hash as canonical_cohort_hash
from research.simulation.semantic_memory_sensitivity import canonical_hash
from research.simulation.wuhan_portfolio_baseline import _validate_frozen_cohort


class WuhanPortfolioBaselineSelectionTest(unittest.TestCase):
    def test_existing_independent_cohort_format_remains_supported(self):
        codes = ["000001", "600001"]
        config = {"selection": {
            "selection_rule": "sha256_rank_of_pre_event_question_active_issuers",
            "sample_size": 2,
        }, "stock_codes": codes}
        self.assertEqual(_validate_frozen_cohort(config, codes), codes)

    def test_fresh_holdout_selection_audit_is_verified_without_rewriting_membership(self):
        codes = [f"{index:06d}" for index in range(126)]
        config = {"selection_audit": {
            "selection_rule": "sha256_rank_after_excluding_prior_development_and_v3_validation_cohorts",
            "sample_size": 126,
            "selected_stock_codes_sha256": canonical_cohort_hash(codes),
            "status": "membership_frozen_text_unreviewed_model_unrun",
        }, "stock_codes": codes}
        self.assertEqual(_validate_frozen_cohort(config, codes), codes)
        config["selection_audit"]["selected_stock_codes_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "membership hash"):
            _validate_frozen_cohort(config, codes)

    def test_a_replay_cannot_silently_drop_or_reorder_frozen_companies(self):
        codes = ["000001", "600001"]
        config = {"selection": {
            "selection_rule": "sha256_rank_of_pre_event_question_active_issuers",
            "sample_size": 2,
        }, "stock_codes": codes}
        with self.assertRaisesRegex(ValueError, "frozen pre-event"):
            _validate_frozen_cohort(config, ["600001", "000001"])
        with self.assertRaisesRegex(ValueError, "frozen pre-event"):
            _validate_frozen_cohort(config, ["000001"])


if __name__ == "__main__":
    unittest.main()
