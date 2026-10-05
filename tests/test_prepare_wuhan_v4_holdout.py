import unittest

from research.semantic.prepare_wuhan_v4_holdout import select_fresh_codes


class PrepareWuhanV4HoldoutTest(unittest.TestCase):
    def test_selection_is_deterministic_and_excludes_all_prior_groups(self):
        groups = [{f"{index:06d}" for index in range(start, start + 5)}
                  for start in (0, 10, 20)]
        candidates = {f"{index:06d}" for index in range(40)}

        first, excluded = select_fresh_codes(candidates, groups, "frozen-seed", 12)
        second, _ = select_fresh_codes(candidates, groups, "frozen-seed", 12)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 12)
        self.assertEqual(len(excluded), 15)
        self.assertTrue(all(not (set(first) & group) for group in groups))

    def test_selection_rejects_overlapping_old_cohorts(self):
        with self.assertRaisesRegex(ValueError, "previous cohorts overlap"):
            select_fresh_codes({"000001", "000002"},
                               [{"000001"}, {"000001"}], "frozen-seed", 1)

    def test_selection_rejects_short_remaining_frame(self):
        with self.assertRaisesRegex(ValueError, "smaller than the requested sample"):
            select_fresh_codes({"000001", "000002"}, [{"000001"}], "frozen-seed", 2)


if __name__ == "__main__":
    unittest.main()
