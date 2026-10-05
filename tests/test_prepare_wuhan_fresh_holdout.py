import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic import prepare_wuhan_fresh_holdout as prepare


class PrepareWuhanFreshHoldoutTest(unittest.TestCase):
    def test_selection_is_deterministic_and_disjoint_from_both_used_groups(self):
        candidates = [f"{value:06d}" for value in range(1, 301)] + ["bad-code"]
        development = candidates[:40]
        prior_validation = candidates[40:80]

        selected, excluded = prepare.select_fresh_codes(
            reversed(candidates), [development, prior_validation], "fresh-seed", 50)
        repeated, repeated_excluded = prepare.select_fresh_codes(
            candidates, [development, prior_validation], "fresh-seed", 50)

        self.assertEqual(selected, repeated)
        self.assertEqual(excluded, repeated_excluded)
        self.assertEqual(len(selected), 50)
        self.assertEqual(len(excluded), 80)
        self.assertFalse(set(selected) & set(development))
        self.assertFalse(set(selected) & set(prior_validation))
        self.assertNotIn("bad-code", selected)

    def test_rejects_overlapping_exclusion_groups(self):
        with self.assertRaisesRegex(ValueError, "excluded cohorts overlap"):
            prepare.select_fresh_codes(["000001", "000002"],
                                       [["000001"], ["000001"]], "fresh-seed", 1)

    def test_rejects_insufficient_remaining_companies(self):
        with self.assertRaisesRegex(ValueError, "smaller than the requested sample"):
            prepare.select_fresh_codes(["000001", "000002"], [["000001"]], "fresh-seed", 2)

    def test_config_writer_creates_files_and_never_overwrites_frozen_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            universe = {"stock_codes": ["000001"], "selection_audit": {
                "status": "unreviewed", "excluded_company_count": 2,
                "remaining_candidate_companies": 3}}
            snapshot = {"run_id": "fixture", "universe_config": prepare.OUTPUT_UNIVERSE_RELATIVE}
            with patch.object(prepare, "CONFIG_DIR", config_dir), \
                 patch.object(prepare, "build_configs", return_value=(universe, snapshot)):
                result = prepare.write_configs()
                universe_path = config_dir / prepare.OUTPUT_UNIVERSE_RELATIVE
                snapshot_path = config_dir / prepare.OUTPUT_SNAPSHOT_RELATIVE
                self.assertEqual(json.loads(universe_path.read_text(encoding="utf-8")), universe)
                self.assertEqual(json.loads(snapshot_path.read_text(encoding="utf-8")), snapshot)
                self.assertEqual(result["companies"], 1)
                universe_path.write_text("preserve", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    prepare.write_configs()
                self.assertEqual(universe_path.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
