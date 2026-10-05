import copy
import json
import unittest
from pathlib import Path

from research.data_pipeline.policy_events import DEFAULT_CATALOG, audit_sources, validate_catalog


class PolicyEventCatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))

    def test_catalog_keeps_event_clocks_and_scope_separate(self):
        events = validate_catalog(self.catalog)
        self.assertEqual(len(events), 3)
        wuhan = next(row for row in events if row["event_id"] == "wuhan_transport_restrictions_2020")
        self.assertEqual(wuhan["publication"]["timestamp"], "2020-01-23T03:15:55+08:00")
        self.assertEqual(wuhan["visibility"]["time_status"], "observed_exact_timestamp")
        self.assertEqual(wuhan["effective_period"]["from"], "2020-01-23T10:00:00+08:00")
        self.assertEqual(wuhan["scope"]["issuer_codes"], [])
        self.assertFalse(wuhan["use_policy"]["agent_signal_enabled"])

    def test_wuhan_exact_page_time_and_approximate_issuer_time_are_archived_separately(self):
        audit = audit_sources()
        self.assertEqual(audit["source_archives_verified"], 3)
        wuhan = next(row for row in self.catalog["events"]
                     if row["event_id"] == "wuhan_transport_restrictions_2020")
        page = (Path(__file__).resolve().parents[1] / wuhan["source"]["archive_path"]).read_bytes()
        retrospective_path = (Path(__file__).resolve().parents[1]
                              / "research_outputs/observed_2020/evidence/wuhan_official_release_time_retrospective.md")
        retrospective = retrospective_path.read_text(encoding="utf-8")
        self.assertIn(b"2020-01-23 03:15:55", page)
        self.assertIn("1月23日凌晨2时许", retrospective)

    def test_catalog_rejects_duplicate_event_ids_and_naive_timestamps(self):
        duplicate = copy.deepcopy(self.catalog)
        duplicate["events"].append(copy.deepcopy(duplicate["events"][0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_catalog(duplicate)
        naive = copy.deepcopy(self.catalog)
        naive["events"][0]["publication"]["timestamp"] = "2018-04-27T18:38:49"
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            validate_catalog(naive)

    def test_catalog_rejects_source_path_escape_and_enabling_unverified_signal(self):
        escaped = copy.deepcopy(self.catalog)
        escaped["events"][0]["source"]["archive_path"] = "../outside.html"
        with self.assertRaisesRegex(ValueError, "relative"):
            validate_catalog(escaped)
        enabled = copy.deepcopy(self.catalog)
        enabled["events"][0]["use_policy"]["agent_signal_enabled"] = True
        with self.assertRaisesRegex(ValueError, "context-only"):
            validate_catalog(enabled)

    def test_catalog_rejects_invalid_source_urls_and_reversed_effective_period(self):
        invalid_url = copy.deepcopy(self.catalog)
        invalid_url["events"][0]["source"]["url"] = "not-a-url"
        with self.assertRaisesRegex(ValueError, r"HTTP\(S\)"):
            validate_catalog(invalid_url)
        reversed_period = copy.deepcopy(self.catalog)
        event = reversed_period["events"][2]
        event["effective_period"]["from"] = "2020-02-03"
        event["effective_period"]["through"] = "2020-02-02"
        with self.assertRaisesRegex(ValueError, "reversed"):
            validate_catalog(reversed_period)


if __name__ == "__main__":
    unittest.main()
