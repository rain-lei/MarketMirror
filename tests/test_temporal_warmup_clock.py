from datetime import date, timedelta
from types import SimpleNamespace
import unittest

from research.simulation.temporal_warmup_clock import prepare_clock, draw_session, clock_metadata
from research.simulation.feedback_auction import arrival_order
from research.simulation.participant_market import background_demand
from research.simulation.call_auction import AuctionAccount


def calendar(n=100):
    days = [(date(2020, 11, 1) + timedelta(days=i)).isoformat() for i in range(n)]
    return [(d, (date.fromisoformat(d) - timedelta(days=2)).isoformat(),
        (date.fromisoformat(d) - timedelta(days=1)).isoformat()) for d in days]


class TemporalWarmupClockTests(unittest.TestCase):
    def test_every_evaluation_arrival_and_private_background_draw_matches_cold_keys(self):
        dates = calendar()
        clock = prepare_clock(dates, dates[42][0], 20, [SimpleNamespace(rebalance_interval=3)])
        names = ["aggressive_00", "institutional_00", "background_000001_000"]
        bg = {"mode": "active", "seed": 11, "initial_shares": 500, "max_order_lots": 4, "target_range_lots": 4, "urgency_bps": 25}
        venue = {"lot_size": 100, "tick_minor": 1}
        for i in range(58):
            draw = draw_session(i + 42, clock)
            self.assertEqual(draw, i)
            self.assertEqual(arrival_order(names, "hashed", 7, i + 42 - clock["warmup_sessions"]), arrival_order(names, "hashed", 7, i))
            self.assertEqual(background_demand("000001", draw, names[-1], AuctionAccount(5000000, 500, 500), 10000, (9000, 11000), venue, bg),
                background_demand("000001", i, names[-1], AuctionAccount(5000000, 500, 500), 10000, (9000, 11000), venue, bg))
        self.assertEqual(draw_session(0, clock), -42)
        self.assertEqual(clock_metadata(41, clock)["phase"], "warmup")
        self.assertEqual(clock_metadata(42, clock)["phase"], "evaluation")

    def test_no_warmup_retains_original_random_draws_and_has_no_added_metadata(self):
        self.assertIsNone(prepare_clock(calendar(), None, 20, []))
        self.assertEqual(draw_session(13, None), 13)
        self.assertIsNone(clock_metadata(13, None))

    def test_short_history_missing_start_and_shifted_rebalance_schedule_are_rejected(self):
        days = calendar()
        for d in (days[20][0], days[41][0], "2023-01-01"):
            with self.assertRaises(ValueError):
                prepare_clock(days, d, 20, [SimpleNamespace(rebalance_interval=3)])
        with self.assertRaises(ValueError):
            draw_session(True, None)


if __name__ == "__main__":
    unittest.main()
