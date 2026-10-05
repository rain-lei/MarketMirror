import itertools
import unittest

from research.simulation.own_price_feedback_coverage import expected_coverage


CELLS = [{"name": anchor + str(scale), "background_anchor": anchor, "scale": scale}
         for anchor in ("initial_inventory", "current_inventory") for scale in (1.0, 0.5, 0.0)]


class OwnPriceFeedbackCoverageTest(unittest.TestCase):
    def test_five_seed_receipt_count_includes_every_bundle(self):
        expected = expected_coverage(5, CELLS, 41, 43, 12, 3, 12)
        self.assertEqual(expected["paired_private_receipt_values"], 3173400)
        self.assertNotEqual(expected["paired_private_receipt_values"], 634680)
        self.assertEqual(expected["same_state_strategy_allocations_audited"], 634680)
        self.assertEqual(expected["full_path_ledger_records"], 52890)
        self.assertEqual(expected["same_state_scaled_records"], 35260)

    def test_tiny_dimensions_match_independently_enumerated_identities(self):
        expected = expected_coverage(3, CELLS, 2, 4, 2, 2, 3)
        paths = list(itertools.product(range(3), range(6), range(2)))
        days = list(itertools.product(range(3), range(6), range(2), range(4)))
        positions = [("strategy", actor, asset) for actor in range(2) for asset in range(2)]
        positions += [("background", actor, asset) for asset in range(2) for actor in range(3)]
        paired = [(seed, cell, basket, day, position) for seed, cell, basket, day in days if cell != 0 for position in positions]
        counter_orders = [(seed, cell, basket, day, scale) for seed, cell, basket, day in days
                          if CELLS[cell]["scale"] == 1.0 for scale in (0.5, 0.0)]
        self.assertEqual(expected["paths"], len(paths))
        self.assertEqual(expected["full_path_ledger_records"], len(days))
        self.assertEqual(expected["paired_private_receipt_values"], len(paired))
        self.assertEqual(expected["same_state_scaled_records"], len(counter_orders))

    def test_single_seed_preserves_reference_grid_and_replicates_scale_linearly(self):
        first = expected_coverage(1, CELLS, 41, 43, 12, 3, 12)
        five = expected_coverage(5, CELLS, 41, 43, 12, 3, 12)
        self.assertEqual(first["paired_private_receipt_values"], 634680)
        self.assertEqual(first["complete_original_daily_records_matched"], five["complete_original_daily_records_matched"])
        for field in set(first) - {"complete_original_daily_records_matched"}:
            self.assertEqual(first[field] * 5, five[field])

    def test_missing_duplicate_and_mislabeled_cells_cannot_reduce_required_coverage(self):
        for cells in (CELLS[:-1], CELLS + [CELLS[0]], [{**cell, "scale": 0.2} for cell in CELLS]):
            with self.assertRaises(ValueError): expected_coverage(5, cells, 41, 43, 12, 3, 12)

    def test_bool_zero_and_negative_replications_are_rejected(self):
        for value in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError): expected_coverage(value, CELLS, 41, 43, 12, 3, 12)


if __name__ == "__main__":
    unittest.main()
