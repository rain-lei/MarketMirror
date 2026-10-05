import unittest

from research.simulation.temporal_book_diagnostics import alternative_prices, add_group, finish_group, scope_group
from research.simulation.order_price_diagnostics import diagnose_book
from tests.test_order_price_diagnostics import fixture


class TemporalBookDiagnosticTest(unittest.TestCase):
    def test_traded_flat_interval_changes_same_book_price_without_changing_objectives(self):
        d = diagnose_book(*fixture([("aggressive_00", "buy", 100, 110), ("conservative_00", "sell", 100, 95)]))
        self.assertEqual(alternative_prices(d, 1), {"nearest_prior": 100, "sse_midpoint": 103, "accepted_order_pressure": 100})
        g = scope_group()
        add_group(g, d, alternative_prices(d, 1), 1)
        s = finish_group(g)
        self.assertEqual(s["counts"]["traded_flat_multiple_optima"], 1)
        self.assertEqual(s["same_book_rules"]["sse_midpoint"]["traded_flat_moved"], 1)
        self.assertEqual(s["same_book_rules"]["accepted_order_pressure"]["traded_flat_moved"], 0)

    def test_unique_prior_optimum_is_not_moved_by_any_rule(self):
        d = diagnose_book(*fixture([("aggressive_00", "buy", 100, 100), ("conservative_00", "sell", 100, 100)]))
        self.assertEqual(set(alternative_prices(d, 1).values()), {100})
        g = scope_group()
        add_group(g, d, alternative_prices(d, 1), 1)
        self.assertEqual(finish_group(g)["counts"]["traded_flat_unique_prior"], 1)

    def test_no_match_and_halt_cannot_create_a_counterfactual_return(self):
        entries = [("aggressive_00", "buy", 100, 95), ("conservative_00", "sell", 100, 105)]
        for execute in (True, False):
            d = diagnose_book(*fixture(entries, execute=execute))
            self.assertEqual(set(alternative_prices(d, 1).values()), {100})
            g = scope_group()
            add_group(g, d, alternative_prices(d, 1), 1)
            self.assertEqual(finish_group(g)["fractions"]["no_trade"], 1)


if __name__ == "__main__":
    unittest.main()
