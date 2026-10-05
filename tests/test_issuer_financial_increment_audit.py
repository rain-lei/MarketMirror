import tempfile
import unittest
from pathlib import Path

import numpy as np

from research.baselines.audit_issuer_financial_increment import (
    check_metrics, independent_metrics, read_csv, svd_fit,
)


class FinancialIncrementIndependentAuditTest(unittest.TestCase):
    def test_svd_recovers_known_line_with_training_only_scaling(self):
        predictions, design = svd_fit([[0.], [1.], [2.], [3.]], [1., 3., 5., 7.], [[4.], [1e6]])
        np.testing.assert_allclose(predictions, [9., 2000001.], rtol=1e-12)
        self.assertEqual(design["means"][0], 1.5)
        self.assertEqual(design["rank"], 2)
        self.assertAlmostEqual(design["scales"][0], np.std([0., 1., 2., 3.], ddof=1))

    def test_svd_rejects_nonconstant_but_collinear_financial_block(self):
        with self.assertRaisesRegex(ValueError, "rank deficient"):
            svd_fit([[1., 2.], [2., 4.], [3., 6.], [4., 8.]], [1., 2., 3., 4.], [[5., 10.]])

    def test_nonfinite_target_cannot_be_approved(self):
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            svd_fit([[0.], [1.], [2.]], [0., float("nan"), 2.], [[3.]])

    def test_independent_metrics_retain_negative_predictions_and_session_weighting(self):
        score = independent_metrics([0., 0., 0., 0.], [-1., -1., -1., -3.], ["d1", "d1", "d1", "d2"])
        self.assertEqual(score["negative_prediction_count"], 4)
        self.assertEqual(score["pooled_mae"], 1.5)
        self.assertEqual(score["equal_session_mae"], 2.)
        self.assertEqual(score["daily_mean_target_mae"], 2.)

    def test_changed_count_and_error_metric_fail_independent_comparison(self):
        score = independent_metrics([.1, .2], [.1, .1], ["d1", "d2"])
        with self.assertRaisesRegex(ValueError, "metric count"):
            check_metrics({**score, "rows": 3}, score, "model")
        with self.assertRaisesRegex(ValueError, "reconstruction differs"):
            check_metrics({**score, "pooled_mae": .9}, score, "model")

    def test_raw_csv_duplicate_headers_and_short_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.csv"
            path.write_text("date,date,amount\nd1,d1,10\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                read_csv(path)
            path.write_text("date,amount\nd1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "malformed"):
                read_csv(path)


if __name__ == "__main__":
    unittest.main()
