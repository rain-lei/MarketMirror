import unittest
from datetime import date, timedelta

from research.baselines.event_study import DailyObservation, event_study
from research.baselines.placebo_dates import blackout_bounds, comparison, enumerate_dates, render_report


class PlaceboDateTest(unittest.TestCase):
    def setUp(self):
        self.codes = ["000001", "000002"]
        self.dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(160)]
        self.groups = {}
        for code, beta, shock in (("000001", 1.2, 0.04), ("000002", 0.8, -0.02)):
            rows = []
            for i, day in enumerate(self.dates):
                market = (i % 7 - 3) * 0.001
                rows.append(DailyObservation(day, 0.001 + beta * market + (shock if i == 85 else 0), market))
            self.groups[code] = rows
        self.settings = {"estimation_window": 40, "pre_event_gap": 5, "window_before": 3, "window_after": 5}
        self.actual = []
        for code in self.codes:
            run = event_study(self.groups[code], self.dates[85], **self.settings)
            self.actual.append({"event_id": "known_shock", "stock_code": code, **run})

    def test_candidate_windows_are_disjoint_and_both_regimes_exist(self):
        diagnostic, detail = enumerate_dates(self.groups, self.codes, self.actual, self.settings,
                                             self.dates[0].isoformat(), self.dates[-1].isoformat(), 0)
        first, last = diagnostic["blackout_start"], diagnostic["blackout_end"]
        self.assertEqual(first, self.dates[82].isoformat())
        self.assertEqual(last, self.dates[90].isoformat())
        self.assertGreater(diagnostic["excluded_or_retained_dates"]["before_actual_event"], 0)
        self.assertGreater(diagnostic["excluded_or_retained_dates"]["after_actual_event"], 0)
        self.assertEqual(len(detail), len(diagnostic["candidate_dates"]) * len(self.codes))
        self.assertNotIn(self.dates[85].isoformat(), {r["candidate_date"] for r in detail})
        for row in detail:
            self.assertTrue(row["window_end"] < first or row["estimation_start"] > last)

    def test_known_shock_has_large_descriptive_rank_without_p_value(self):
        diagnostic, _ = enumerate_dates(self.groups, self.codes, self.actual, self.settings,
                                        self.dates[0].isoformat(), self.dates[-1].isoformat(), 0)
        rows = comparison(self.actual, diagnostic["candidate_dates"], self.codes)
        for row in rows:
            if row["stock_code_or_mean"] in self.codes:
                self.assertAlmostEqual(row["absolute_rank_fraction"], 1.0)
        report = render_report({"diagnostic": diagnostic, "comparisons": rows})
        self.assertIn("不是 p 值", report)
        self.assertIn("known_shock", report)

    def test_padding_expands_actual_event_blackout(self):
        sessions = [d.isoformat() for d in self.dates]
        self.assertEqual(blackout_bounds(self.actual, sessions, 2),
                         (self.dates[80].isoformat(), self.dates[92].isoformat()))

    def test_incomplete_stock_panel_rejected(self):
        with self.assertRaisesRegex(ValueError, "complete preselected stock panel"):
            comparison(self.actual[:1], [], self.codes)
        with self.assertRaisesRegex(ValueError, "same complete session calendar"):
            enumerate_dates({"000001": self.groups["000001"], "000002": self.groups["000002"][1:]},
                            self.codes, self.actual, self.settings,
                            self.dates[0].isoformat(), self.dates[-1].isoformat(), 0)


if __name__ == "__main__":
    unittest.main()
