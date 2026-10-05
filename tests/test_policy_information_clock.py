import copy
import unittest
from datetime import date, timedelta

from research.data_pipeline.policy_calendar import build_calendar
from research.data_pipeline.policy_information_clock import clock_model_rows, prior_clocks, shared_pairing
from research.data_pipeline.policy_rate_panel import diagnostic_rows, policy_vectors


class InformationClockTests(unittest.TestCase):
    def setUp(self):
        self.days = [(date(2019, 6, 1) + timedelta(days=i)).isoformat() for i in range(40)]
        self.closes = {day: 100 + i + (i % 3) * .1 for i, day in enumerate(self.days)}
        self.first, self.last = self.days[25], self.days[-1]

    def make_rows(self, market_lag, policy_lag):
        clocks = prior_clocks(self.days, self.first, self.last, policy_lag)
        calendar = build_calendar([], clocks)
        vector = policy_vectors([], calendar, [("reverse_repo", 7, "天")])
        return calendar, vector, clock_model_rows(self.days, self.closes, calendar, vector, market_lag, policy_lag)

    def test_one_session_cutoff_respects_long_exchange_holiday(self):
        days = ["2020-01-21", "2020-01-22", "2020-01-23", "2020-02-03", "2020-02-04"]
        clocks = prior_clocks(days, "2020-02-03", "2020-02-04", 1)
        self.assertEqual(clocks[0]["signal_cutoff_date"], "2020-01-23")

    def test_target_day_or_noninteger_lags_rejected(self):
        for lag in (0, -1, 3, True, 1.0):
            with self.subTest(lag=lag), self.assertRaisesRegex(ValueError, "prior exchange"):
                prior_clocks(self.days, self.first, self.last, lag)

    def test_old_two_day_features_and_targets_recovered(self):
        calendar, vector, current = self.make_rows(2, 2)
        prior = diagnostic_rows(self.days, self.closes, calendar, vector)
        for a, b in zip(current, prior):
            self.assertTrue(all(a[key] == b[key] for key in ("trade_date", "signal_cutoff_date", "execution_reference_date",
                               "baseline_features", "policy_features", "target_return", "target_absolute_return", "paired_exclusion_reason")))

    def test_crossed_market_and_policy_cutoffs_are_distinct(self):
        _, _, rows = self.make_rows(1, 2)
        self.assertEqual(rows[0]["signal_cutoff_date"], self.days[23])
        self.assertEqual(rows[0]["market_feature_cutoff_date"], self.days[24])

    def test_poisoned_target_price_cannot_change_either_input_clock(self):
        for lag in (1, 2):
            with self.subTest(lag=lag):
                calendar, vector, rows = self.make_rows(lag, lag)
                poisoned = dict(self.closes);poisoned[self.last] *= 1000
                after = clock_model_rows(self.days, poisoned, calendar, vector, lag, lag)
                self.assertEqual(rows[-1]["baseline_features"], after[-1]["baseline_features"])
                self.assertNotEqual(rows[-1]["target_return"], after[-1]["target_return"])

    def test_false_declared_policy_cutoff_rejected(self):
        calendar, vector, _ = self.make_rows(1, 2)
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            clock_model_rows(self.days, self.closes, calendar, vector, 1, 1)

    def test_march30_rate_not_injected_early_but_available_mar31_under_lag1(self):
        days = ["2020-03-24", "2020-03-25", "2020-03-26", "2020-03-27", "2020-03-30", "2020-03-31"]
        source = {"source_id": "cut", "title": "cut", "available_at": "2020-03-30T09:45:05+08:00",
                  "publication_timestamp": "2020-03-30T09:45:05+08:00", "signed_date": "2020-03-30",
                  "source": {"sha256": "cut"}, "gross_reverse_repo_amount_100m_yuan": None,
                  "operations": [{"instrument": "reverse_repo", "tenor_value": 7, "tenor_unit": "天",
                                  "rate_pct": 2.2, "rate_kind": "tender_interest", "market": "mainland",
                                  "change_from_previous_observed_bps": -20.0, "previous_observation": None}]}
        first = build_calendar([source], prior_clocks(days, days[2], days[-1], 1))
        second = build_calendar([source], prior_clocks(days, days[2], days[-1], 2))
        channels = [("reverse_repo", 7, "天")]
        a = policy_vectors([source], first, channels);b = policy_vectors([source], second, channels)
        self.assertEqual(a[-1]["change_bps_by_channel"], [-20.0])
        self.assertTrue(all(row["change_bps_by_channel"] == [0.0] for row in b))

    def test_union_missing_dates_excludes_same_targets_from_all_variants(self):
        _, _, before = self.make_rows(2, 2)
        panels = {"one": copy.deepcopy(before), "two": copy.deepcopy(before)}
        panels["one"][0]["intrinsic_exclusion_reason"] = "unknown_new_policy_predecessor"
        panels["two"][1]["intrinsic_exclusion_reason"] = "unknown_new_policy_predecessor"
        exclusions = shared_pairing(panels)
        self.assertEqual([row["trade_date"] for row in exclusions], [before[0]["trade_date"], before[1]["trade_date"]])
        for rows in panels.values():
            self.assertEqual([row["paired_exclusion_reason"] for row in rows[:2]], ["shared_clock_pairing_exclusion"] * 2)
            self.assertTrue(all(row["paired_exclusion_reason"] is None for row in rows[2:]))

    def test_shared_pairing_does_not_choose_dates_based_on_outcome_magnitude(self):
        _, _, rows = self.make_rows(2, 2)
        panels = {"one": copy.deepcopy(rows), "two": copy.deepcopy(rows)}
        panels["one"][0]["intrinsic_exclusion_reason"] = "unknown"
        before = shared_pairing(panels)
        for values in panels.values():
            for row in values:
                row["target_return"] += 1000000;row["target_absolute_return"] += 1000000
        after = shared_pairing(panels)
        self.assertEqual(before, after)

    def test_mismatched_targets_or_dates_cannot_be_called_paired(self):
        _, _, rows = self.make_rows(2, 2)
        a, b = copy.deepcopy(rows), copy.deepcopy(rows)
        b[-1]["target_return"] += .1
        with self.assertRaisesRegex(ValueError, "same targets"):
            shared_pairing({"a": a, "b": b})
        with self.assertRaisesRegex(ValueError, "same dates"):
            shared_pairing({"a": a, "b": a[:-1]})


if __name__ == "__main__":
    unittest.main()
