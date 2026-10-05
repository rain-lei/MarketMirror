import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research.semantic.wuhan_fresh_holdout_review import (
    _assert_no_local_model_run,
    _expected_pack,
)


class WuhanFreshHoldoutReviewTest(unittest.TestCase):
    def test_pack_path_is_frozen_before_source_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("research.semantic.wuhan_fresh_holdout_review.verify_pack") as verify:
                with self.assertRaisesRegex(ValueError, "restricted to the frozen fresh company cohort"):
                    _expected_pack(Path(temporary))
                verify.assert_not_called()

    def test_review_refuses_to_start_after_model_output_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "research_outputs" / "prior_run"
            output.mkdir(parents=True)
            (output / "model_run_manifest.json").write_text(
                json.dumps({"pack_experiment_id": "fresh-pack-id"}), encoding="utf-8")
            with patch("research.semantic.wuhan_fresh_holdout_review.ROOT", root):
                with self.assertRaisesRegex(ValueError, "before model outputs exist"):
                    _assert_no_local_model_run("fresh-pack-id")


if __name__ == "__main__":
    unittest.main()
