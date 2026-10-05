import hashlib
import json
import unittest

import test_issuer_valuation
from research.simulation.portfolio_price_feedback_channels import simulate_portfolio
from research.simulation.semantic_memory_sensitivity import canonical_hash
from research.simulation.verify_pre_wuhan_price_feedback_channels_2019 import digest as v1_digest
from research.simulation.verify_pre_wuhan_price_feedback_channels_2019_v2 import digest


class PriceFeedbackChannelVerifierV2Test(unittest.TestCase):
    def test_hash_uses_the_existing_archive_contract_including_json_spacing_and_unicode(self):
        value = {"state": {"股票": None, "sellable": 100, "ratio": -0.0}, "active": True}
        expected_bytes = '{"active": true, "state": {"ratio": -0.0, "sellable": 100, "股票": null}}'.encode("utf-8")
        self.assertEqual(digest(value), hashlib.sha256(expected_bytes).hexdigest())
        self.assertEqual(digest(value), canonical_hash(value))
        self.assertNotEqual(v1_digest(value), digest(value))

    def test_compact_and_contract_hashes_cover_identical_values_but_cannot_be_interchanged(self):
        value = {"sign": 0, "streak": 0, "wallets": {"000001": 30000000}}
        compact = json.dumps(value, sort_keys=True, separators=(",", ":"))
        existing = json.dumps(value, sort_keys=True)
        self.assertEqual(json.loads(compact), json.loads(existing))
        self.assertNotEqual(hashlib.sha256(compact.encode()).hexdigest(), digest(value))
        self.assertEqual(hashlib.sha256(existing.encode()).hexdigest(), digest(value))

    def test_reconstructed_full_trace_hash_matches_real_engine_summary_for_split_channels(self):
        args, shocks, industry, response, _, _, issuer = test_issuer_valuation.fixture()
        for target, quote in ((1, 1), (0, 1), (1, 0), (0, 0)):
            result = simulate_portfolio(*args, scenario_shocks=shocks, industry_shocks=industry,
                                        background_response=response, issuer_valuation=issuer,
                                        target_feedback_scale=target, quote_feedback_scale=quote)
            self.assertEqual(digest(result["trace"]), result["summary"]["trace_sha256"])


if __name__ == "__main__":
    unittest.main()
