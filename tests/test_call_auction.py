import copy
import random
import unittest

from research.simulation.call_auction import AuctionAccount, CallAuction, LimitOrder


def order(owner, side, quantity, price=1000, sequence=0):
    return LimitOrder(f"order:{owner}", owner, side, quantity, price, sequence)


class CallAuctionTest(unittest.TestCase):
    def venue(self, accounts, fee=10, lot=1, price=1000):
        return CallAuction(accounts, price, lot, 1, fee, 2000)

    def test_bilateral_trade_integer_cash_shares_and_fee_conservation(self):
        venue = self.venue({"buyer": AuctionAccount(10000, 0, 0), "seller": AuctionAccount(100, 10, 10)})
        result = venue.clear(0, [order("buyer", "buy", 5, 1100), order("seller", "sell", 5, 900, 1)])
        self.assertEqual(result["price_after_minor"], 1000)
        self.assertEqual(result["matched_volume"], 5)
        self.assertEqual(result["trades"], [{"buyer": "buyer", "seller": "seller", "buy_order_id": "order:buyer",
                         "sell_order_id": "order:seller", "quantity": 5, "price_minor": 1000}])
        self.assertEqual(venue.accounts["buyer"].cash_minor, 4995)
        self.assertEqual(venue.accounts["buyer"].shares, 5)
        self.assertEqual(venue.accounts["buyer"].sellable_shares, 0)
        self.assertEqual(venue.accounts["seller"].cash_minor, 5095)
        self.assertEqual(venue.fee_pool_minor, 10)
        self.assertEqual(result["cash_total_minor"] + result["fee_pool_minor"], 10100)
        self.assertEqual(sum(row["shares"] for row in result["accounts"].values()), 10)

    def test_cash_reservation_includes_limit_price_fee_and_inventory_caps(self):
        venue = self.venue({"buyer": AuctionAccount(2202, 0, 0), "seller": AuctionAccount(0, 10, 2)})
        result = venue.clear(0, [order("buyer", "buy", 10, 1100), order("seller", "sell", 10, 900, 1)])
        by_owner = {row["owner"]: row for row in result["orders"]}
        self.assertEqual(by_owner["buyer"]["accepted_quantity"], 1)  # Two units cost 2200 plus a fee rounded to 3.
        self.assertEqual(by_owner["seller"]["accepted_quantity"], 2)
        self.assertIn("cash_and_fee_reservation", by_owner["buyer"]["reasons"])
        self.assertIn("sellable_inventory", by_owner["seller"]["reasons"])
        self.assertGreaterEqual(venue.accounts["buyer"].cash_minor, 0)
        boundary = self.venue({"buyer": AuctionAccount(2203, 0, 0), "seller": AuctionAccount(0, 10, 2)})
        accepted = boundary.clear(0, [order("buyer", "buy", 10, 1100), order("seller", "sell", 10, 900, 1)])
        self.assertEqual(accepted["orders"][0]["accepted_quantity"], 2)

    def test_no_counterparty_or_uncrossed_quotes_cannot_move_price(self):
        venue = self.venue({"buyer": AuctionAccount(10000, 0, 0), "seller": AuctionAccount(0, 10, 10)})
        for index, orders in enumerate(([order("buyer", "buy", 5, 1100)],
                                       [order("buyer", "buy", 5, 950), order("seller", "sell", 5, 1050, 1)], [])):
            result = venue.clear(index, orders)
            self.assertEqual(result["matched_volume"], 0)
            self.assertEqual(result["price_after_minor"], 1000)
            self.assertEqual(result["fee_pool_minor"], 0)
        self.assertEqual(venue.accounts["buyer"].shares, 0)

    def test_price_then_sequence_priority_and_order_list_permutation(self):
        accounts = {"a": AuctionAccount(10000, 0, 0), "b": AuctionAccount(10000, 0, 0),
                    "seller": AuctionAccount(0, 5, 5)}
        orders = [order("a", "buy", 5, 1100, 1), order("b", "buy", 5, 1200, 0), order("seller", "sell", 5, 1000, 2)]
        result = self.venue(accounts, fee=0).clear(0, orders)
        self.assertEqual({row["owner"]: row["filled_quantity"] for row in result["orders"]}, {"a": 0, "b": 5, "seller": 5})
        self.assertEqual(result, self.venue(accounts, fee=0).clear(0, list(reversed(orders))))
        orders[0] = order("a", "buy", 5, 1200, 1)
        tied = self.venue(accounts, fee=0).clear(0, orders)
        self.assertEqual({row["owner"]: row["filled_quantity"] for row in tied["orders"]}["b"], 5)

    def test_bought_inventory_unlocks_only_next_session_and_duplicate_session_rejected(self):
        venue = self.venue({"a": AuctionAccount(10000, 0, 0), "b": AuctionAccount(10000, 10, 10)}, fee=0)
        venue.clear(0, [order("a", "buy", 5), order("b", "sell", 5, sequence=1)])
        self.assertEqual(venue.accounts["a"].sellable_shares, 0)
        original = copy.deepcopy(venue.accounts)
        with self.assertRaisesRegex(ValueError, "session"):
            venue.clear(0, [order("a", "sell", 5), order("b", "buy", 5, sequence=1)])
        self.assertEqual(venue.accounts, original)
        result = venue.clear(1, [order("a", "sell", 5), order("b", "buy", 5, sequence=1)])
        self.assertEqual(result["matched_volume"], 5)
        self.assertEqual(venue.accounts["a"].shares, 0)

    def test_halt_invalid_lots_outside_band_and_duplicate_owner(self):
        accounts = {"a": AuctionAccount(100000, 0, 0), "b": AuctionAccount(0, 100, 100)}
        venue = self.venue(accounts, lot=10)
        with self.assertRaisesRegex(ValueError, "lots"):
            venue.clear(0, [order("a", "buy", 11)])
        with self.assertRaisesRegex(ValueError, "one side"):
            venue.clear(0, [order("a", "buy", 10), LimitOrder("another", "a", "sell", 10, 1000, 1)])
        self.assertEqual(venue.session, -1)
        halted = venue.clear(0, [order("a", "buy", 100), order("b", "sell", 100, sequence=1)], False)
        self.assertEqual(halted["accounts"], {name: vars(row) for name, row in accounts.items()})
        self.assertEqual(halted["matched_volume"], 0)
        self.assertTrue(all(row["reasons"] == ["trading_halted"] for row in halted["orders"]))
        outside = venue.clear(1, [order("a", "buy", 100, 1500), order("b", "sell", 100, sequence=1)])
        self.assertEqual(outside["matched_volume"], 0)
        self.assertIn("price_outside_model_band", outside["orders"][0]["reasons"])

    def test_fee_rounding_once_per_order_not_per_counterparty(self):
        accounts = {"a": AuctionAccount(1000, 0, 0), "b": AuctionAccount(0, 1, 1), "c": AuctionAccount(0, 1, 1)}
        venue = self.venue(accounts, fee=1, price=100)
        result = venue.clear(0, [order("a", "buy", 2, 100), order("b", "sell", 1, 100, 1), order("c", "sell", 1, 100, 2)])
        self.assertEqual(len(result["trades"]), 2)
        self.assertEqual(result["orders"][0]["fee_minor"], 1)
        self.assertEqual(venue.fee_pool_minor, 3)

    def test_candidate_search_matches_independent_full_tick_enumeration(self):
        rng = random.Random(20260927)
        for _ in range(200):
            accounts = {"buy0": AuctionAccount(100000, 0, 0), "buy1": AuctionAccount(100000, 0, 0),
                        "sell0": AuctionAccount(0, 100, 100), "sell1": AuctionAccount(0, 100, 100)}
            venue = CallAuction(accounts, 10, 1, 1, 0, 5000)
            orders = [order(owner, "buy" if owner.startswith("buy") else "sell", rng.randint(1, 15),
                            rng.randint(5, 15), i) for i, owner in enumerate(accounts)]
            scores = []
            for price in range(5, 16):
                demand = sum(row.quantity for row in orders if row.side == "buy" and row.limit_price_minor >= price)
                supply = sum(row.quantity for row in orders if row.side == "sell" and row.limit_price_minor <= price)
                scores.append((price, min(demand, supply), demand - supply))
            expected = min(scores, key=lambda row: (-row[1], abs(row[2]), abs(row[0] - 10), row[0]))
            result = venue.clear(0, orders)
            self.assertEqual(result["matched_volume"], expected[1])
            self.assertEqual(result["price_after_minor"], expected[0] if expected[1] else 10)
            for trade in result["trades"]:
                self.assertNotEqual(trade["buyer"], trade["seller"])


if __name__ == "__main__":
    unittest.main()
