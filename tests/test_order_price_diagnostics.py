import copy
import random
import unittest

from research.simulation.call_auction import AuctionAccount, CallAuction, LimitOrder
from research.simulation.order_price_diagnostics import diagnose_book, order_metrics, summarize
from research.simulation.verify_pre_wuhan_order_price_diagnostics_2019 import (
    IndependentTotals, compare, independent_book, independent_measures)
from research.simulation.run_pre_wuhan_order_price_diagnostics_2019 import condition_measures


def fixture(entries, tick=1, execute=True, resources=None):
    accounts, specs, decisions, receipts, orders = {}, {}, {}, {}, []
    for i, (name, side, qty, price) in enumerate(entries):
        cash, shares = (resources or {}).get(name, (1000000, 10000))
        accounts[name] = AuctionAccount(cash, shares, shares)
        if name.startswith("background_"):
            specs[name] = {"kind": "background", "asset": "000001"}
        else:
            specs[name] = {"kind": "strategy", "parameters": {"role": name.rsplit("_", 1)[0]}}
            decisions[name] = {"risk_liquidation": False, "reasons": []}
        receipts[name] = {"000001": {"received": bool(i % 2)}}
        orders.append(LimitOrder(str(i), name, side, qty, price, i))
    call = CallAuction(accounts, 100, 1, tick, 0, 2000).clear(0, orders, execution_available=execute)
    return call, tick, specs, decisions, receipts, "000001"


class OrderPriceDiagnosticsTest(unittest.TestCase):
    def down_volume(self):
        return fixture([("aggressive_00", "buy", 100, 110), ("institutional_01", "buy", 200, 97),
                        ("aggressive_02", "buy", 1000, 90), ("background_000001_000", "sell", 400, 95)])

    def test_buy_excess_can_fall_when_marginal_bid_adds_maximum_volume(self):
        args = self.down_volume()
        d = diagnose_book(*args)
        self.assertEqual((d["clearing_minor"], d["maximum_volume"], d["prior_volume"]), (97, 300, 100))
        self.assertEqual((d["criterion"], d["optimal_geometry"]), ("volume_requires_move", "only_below"))
        self.assertEqual(d["optimal_intervals"], [{"lower_minor": 95, "upper_minor": 97, "demand": 300, "supply": 400}])
        self.assertGreater(sum((1 if o["side"] == "buy" else -1) * o["accepted"] for o in d["orders"]), 0)
        metrics = order_metrics(next(o for o in d["orders"] if o["owner"] == "institutional_01"))
        self.assertEqual(metrics["entering_buy"], 200)
        compare(d, independent_book(*args))

    def test_equal_volume_can_move_down_to_reduce_imbalance(self):
        args = fixture([("aggressive_00", "buy", 100, 110), ("conservative_00", "sell", 100, 95),
                        ("conservative_01", "sell", 100, 100)])
        d = diagnose_book(*args)
        self.assertEqual((d["clearing_minor"], d["maximum_volume"], d["prior_volume"]), (99, 100, 100))
        self.assertEqual(d["criterion"], "imbalance_requires_move")
        self.assertEqual((d["prior_demand"] - d["prior_supply"], d["minimum_abs_imbalance"]), (-100, 0))
        self.assertEqual(order_metrics(d["orders"][-1])["exiting_sell"], 100)
        compare(d, independent_book(*args))

    def test_equal_volume_can_move_up_to_reduce_imbalance(self):
        args = fixture([("aggressive_00", "buy", 100, 105), ("aggressive_01", "buy", 100, 100),
                        ("conservative_00", "sell", 100, 95)])
        d = diagnose_book(*args)
        self.assertEqual((d["clearing_minor"], d["criterion"], d["optimal_geometry"]), (101, "imbalance_requires_move", "only_above"))
        compare(d, independent_book(*args))

    def test_trading_flat_and_no_match_are_separate(self):
        a = diagnose_book(*fixture([("aggressive_00", "buy", 100, 105), ("conservative_00", "sell", 100, 95)]))
        b = diagnose_book(*fixture([("aggressive_00", "buy", 100, 95), ("conservative_00", "sell", 100, 105)]))
        self.assertEqual((a["clearing_minor"], b["clearing_minor"]), (100, 100))
        self.assertEqual((a["criterion"], b["criterion"]), ("prior_optimal", "no_match"))
        self.assertEqual((a["maximum_volume"], b["maximum_volume"]), (100, 0))

    def test_halt_has_no_accepted_or_filled_quantity_and_reader_does_not_mutate(self):
        args = fixture([("aggressive_00", "buy", 100, 105), ("conservative_00", "sell", 100, 95)], execute=False)
        before = copy.deepcopy(args)
        d = diagnose_book(*args)
        self.assertEqual((d["criterion"], d["maximum_volume"], d["clearing_minor"]), ("halted", 0, 100))
        self.assertTrue(all(o["accepted"] == o["filled"] == 0 for o in d["orders"]))
        self.assertEqual(args, before)
        compare(d, independent_book(*args))

    def test_resource_clipping_is_separate_from_unfilled_accepted_orders(self):
        args = fixture([("aggressive_00", "buy", 20, 100), ("conservative_00", "sell", 20, 100)],
                       resources={"aggressive_00": (1000, 0), "conservative_00": (0, 7)})
        d = diagnose_book(*args)
        buy, sell = [order_metrics(o) for o in d["orders"]]
        self.assertEqual((buy["accepted_buy"], buy["filled_buy"], buy["cash_clipped_buy"]), (10, 7, 10))
        self.assertEqual((sell["accepted_sell"], sell["filled_sell"], sell["inventory_clipped_sell"]), (7, 7, 13))
        compare(d, independent_book(*args))

    def test_three_hundred_small_books_match_all_integer_ticks(self):
        rng = random.Random(44733)
        names = [f"{role}_{i:02d}" for role in ("aggressive", "conservative", "institutional") for i in range(4)]
        for i in range(300):
            tick = 5 if i % 2 else 1
            entries = [(name, rng.choice(("buy", "sell")), rng.randrange(1, 31), rng.randrange(80 // tick, 120 // tick + 1) * tick)
                       for name in names[:rng.randrange(2, 13)]]
            resources = {name: (rng.randrange(0, 3000), rng.randrange(0, 31)) for name, _, _, _ in entries}
            args = fixture(entries, tick=tick, resources=resources)
            d = diagnose_book(*args)
            compare(d, independent_book(*args))
            call = args[0]
            dense = []
            for p in range(call["price_bounds_minor"][0], call["price_bounds_minor"][1] + tick, tick):
                b = sum(o["accepted_quantity"] for o in call["orders"] if o["side"] == "buy" and o["limit_price_minor"] >= p)
                s = sum(o["accepted_quantity"] for o in call["orders"] if o["side"] == "sell" and o["limit_price_minor"] <= p)
                dense.append((-min(b, s), abs(b - s), abs(p - 100), p))
            best = min(dense)
            opt = {r[3] for r in dense if r[:2] == best[:2]}
            interval_ticks = {p for r in d["optimal_intervals"] for p in range(r["lower_minor"], r["upper_minor"] + tick, tick)}
            self.assertEqual(interval_ticks, opt)
            self.assertEqual(d["clearing_minor"], best[3] if best[0] else 100)

    def test_return_contributions_and_order_contexts_reconstruct_two_known_books(self):
        down = diagnose_book(*self.down_volume())
        up = diagnose_book(*fixture([("aggressive_00", "buy", 100, 105), ("aggressive_01", "buy", 100, 100), ("conservative_00", "sell", 100, 95)]))
        rows = [{"seed_id": "fixture", "variant": "case", "session": i * 10, "diagnostic": d} for i, d in enumerate((down, up))]
        g = summarize(rows)["fixture|case"]
        self.assertEqual(g["directions"], {"down": 1, "up": 1})
        self.assertAlmostEqual(g["mean_return"], -.01)
        self.assertAlmostEqual(g["return_categories"]["volume_requires_move|down"]["contribution_to_all_book_mean"], -.015)
        self.assertAlmostEqual(sum(r["contribution_to_all_book_mean"] for r in g["return_categories"].values()), g["mean_return"])
        self.assertEqual(g["net_accepted_by_direction"]["down|buy_excess"], 1)
        independent = IndependentTotals()
        for row in rows:
            independent.add(row)
        compare({"fixture|case": g}, independent.finish())
        compare(condition_measures(g), independent_measures(g))

    def test_saved_price_volume_order_and_scalar_type_tampering_rejected(self):
        args = self.down_volume()
        for field in ("price_after_minor", "matched_volume"):
            bad = copy.deepcopy(args)
            bad[0][field] += 1
            with self.assertRaises(ValueError):
                diagnose_book(*bad)
            with self.assertRaises(ValueError):
                independent_book(*bad)
        bad = copy.deepcopy(args)
        bad[0]["orders"][1]["accepted_quantity"] = 0
        with self.assertRaises(ValueError):
            independent_book(*bad)
        for a, b in ((True, 1), (None, 0), ({"a": 1}, {"b": 1}), ([], [1])):
            with self.assertRaises(ValueError):
                compare(a, b)

    def test_context_role_and_background_asset_scope_rejected(self):
        args = self.down_volume()
        bad = copy.deepcopy(args)
        bad[2]["background_000001_000"]["asset"] = "000002"
        with self.assertRaises(ValueError):
            diagnose_book(*bad)
        with self.assertRaises(ValueError):
            independent_book(*bad)


if __name__ == "__main__":
    unittest.main()
