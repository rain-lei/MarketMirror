import copy
import json
import unittest

from research.data_pipeline.issuer_event_exposure import DEFAULT_CATALOG, validate_catalog


class IssuerEventExposureTest(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))

    def test_pilot_maps_three_issuers_without_inventing_impacts(self):
        issuers, _ = validate_catalog(self.catalog)
        self.assertEqual({row["issuer_code"] for row in issuers}, {"000001", "000002", "600519"})
        self.assertEqual(sum(row["direct_scope_match"] == "yes" for row in issuers), 1)
        self.assertTrue(all(row["impact_direction"] == "unknown" for row in issuers))
        self.assertTrue(all(row["agent_signal_enabled"] is False for row in issuers))

    def test_business_evidence_must_precede_event_but_identity_crosswalk_may_be_retrospective(self):
        issuers, sources = validate_catalog(self.catalog)
        identity = sources["bao_code_name_crosswalk"]
        self.assertEqual(identity["available_by"], "2026-09-28")
        self.assertEqual(identity["role"], "issuer_identity")
        later = copy.deepcopy(self.catalog)
        source = next(row for row in later["sources"] if row["source_id"] == "vanke_q3_2017")
        source["available_by"] = "2019-01-01"
        with self.assertRaisesRegex(ValueError, "not available"):
            validate_catalog(later)

    def test_rejects_source_path_escape_and_enabled_signal(self):
        escaped = copy.deepcopy(self.catalog)
        escaped["sources"][1]["archive_path"] = "../outside.csv"
        with self.assertRaisesRegex(ValueError, "project-relative"):
            validate_catalog(escaped)
        enabled = copy.deepcopy(self.catalog)
        enabled["issuers"][0]["agent_signal_enabled"] = True
        with self.assertRaisesRegex(ValueError, "economic signal"):
            validate_catalog(enabled)

    def test_rejects_unknown_evidence_and_duplicate_issuer(self):
        unknown = copy.deepcopy(self.catalog)
        unknown["issuers"][0]["evidence_source_ids"].append("not-in-catalog")
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            validate_catalog(unknown)
        duplicate = copy.deepcopy(self.catalog)
        duplicate["issuers"][1]["issuer_code"] = "000001"
        with self.assertRaisesRegex(ValueError, "unique six-digit"):
            validate_catalog(duplicate)


if __name__ == "__main__":
    unittest.main()
