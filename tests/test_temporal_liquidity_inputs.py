import copy
from fractions import Fraction
import unittest

from research.simulation.temporal_liquidity_inputs import capacity_lots, compile_liquidity, validate_capacity_path


def fixture():
    calendar = [f"2020-12-{i:02d}" for i in range(1, 13)]
    stocks = ["000001", "000002", "000003"]
    rows = []
    for s in stocks:
        for i, d in enumerate(calendar):
            f = {f"f{n}": "1" for n in range(51, 62)}
            f["f51"], f["f56"], f["f57"], f["f61"] = d, str(i + 10), str(i + 100), str(int(s[-1]) * 2)
            raw = {"secid": "0." + s, "trade_date": d, "fqt": 0, "provider_fields": f, "raw_line": ",".join(f[f"f{n}"] for n in range(51, 62))}
            rows.append({"secid": "0." + s, "trade_date": d, "unadjusted_source": raw})
    return rows, stocks, calendar[7:], calendar


class TemporalLiquidityInputsTest(unittest.TestCase):
    def test_reference_and_every_observation_stop_at_their_information_cutoff(self):
        rows, stocks, dates, calendar = fixture()
        p = compile_liquidity(rows, stocks, dates, calendar, 3)
        self.assertEqual(p["reference_cutoff_date"], calendar[5])
        self.assertEqual(p["turnover_reference_fraction"], [1, 25])
        for s in stocks:
            for row in p["by_stock"][s]:
                self.assertLessEqual(row["latest_history_date"], row["signal_cutoff_date"])
        validate_capacity_path(p, stocks, [(d, calendar[calendar.index(d) - 2], calendar[calendar.index(d) - 1]) for d in dates])

    def test_future_fields_do_not_change_prior_capacity_or_history(self):
        rows, stocks, dates, calendar = fixture()
        before = compile_liquidity(rows, stocks, dates, calendar, 3)
        changed = copy.deepcopy(rows)
        for r in changed:
            if r["trade_date"] > calendar[5]:
                f = r["unadjusted_source"]["provider_fields"]
                f["f61"], f["f56"], f["f57"] = "90", "999999", "99999999"
                r["unadjusted_source"]["raw_line"] = ",".join(f[f"f{n}"] for n in range(51, 62))
        after = compile_liquidity(changed, stocks, dates, calendar, 3)
        self.assertEqual(before["turnover_reference_fraction"], after["turnover_reference_fraction"])
        for s in stocks:
            self.assertEqual(before["by_stock"][s][0], after["by_stock"][s][0])

    def test_absent_source_keeps_actual_dates_and_staleness_without_zero_fill(self):
        rows, stocks, dates, calendar = fixture()
        for r in rows:
            if r["secid"] == "0.000001" and r["trade_date"] == calendar[5]:
                r["unadjusted_source"]["raw_line"] = None
                r["unadjusted_source"]["provider_fields"] = dict.fromkeys([f"f{n}" for n in range(51, 62)])
        p = compile_liquidity(rows, stocks, dates, calendar, 3)
        row = p["by_stock"]["000001"][0]
        self.assertEqual(row["latest_history_date"], calendar[4])
        self.assertEqual(row["stale_calendar_sessions"], 1)
        self.assertNotIn(calendar[5], [h["trade_date"] for h in row["history"]])
        self.assertFalse(row["source_unknowns_filled"])

    def test_capacity_bounds_and_half_up_rounding_are_explicit(self):
        self.assertEqual(capacity_lots(Fraction(0), Fraction(1))[0], 2)
        self.assertEqual(capacity_lots(Fraction(100), Fraction(1))[0], 8)
        self.assertEqual(capacity_lots(Fraction(9, 8), Fraction(1))[0], 5)

    def test_modified_capacity_and_future_history_are_rejected(self):
        rows, stocks, dates, calendar = fixture()
        p = compile_liquidity(rows, stocks, dates, calendar, 3)
        clocks = [(d, calendar[calendar.index(d) - 2], calendar[calendar.index(d) - 1]) for d in dates]
        q = copy.deepcopy(p)
        q["by_stock"]["000001"][0]["applied_max_order_lots"] += 1
        with self.assertRaises(ValueError):
            validate_capacity_path(q, stocks, clocks)
        q = copy.deepcopy(p)
        q["by_stock"]["000001"][0]["latest_history_date"] = dates[0]
        with self.assertRaises(ValueError):
            validate_capacity_path(q, stocks, clocks)


if __name__ == "__main__":
    unittest.main()
