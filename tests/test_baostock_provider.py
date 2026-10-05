import csv
import json
import socket
import tempfile
import unittest
from datetime import date
from pathlib import Path

from research.data_pipeline.fetch_baostock import (
    INDEX_FIELDS, STOCK_FIELDS, consume, fetch_baostock, prepare_calendar, prepare_series,
)


class FakeResult:
    def __init__(self, fields, rows, error="0"):
        self.fields, self.rows = fields, rows
        self.error_code, self.error_msg = error, "test error" if error != "0" else "success"
        self.index = -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class FakeSDK:
    def __init__(self, error=False, ipo_date="2020-01-02", out_date=""):
        self.error, self.logout_calls = error, 0
        self.ipo_date, self.out_date = ipo_date, out_date

    def login(self):
        return FakeResult([], [])

    def logout(self):
        self.logout_calls += 1

    def query_trade_dates(self, **kwargs):
        return FakeResult(["calendar_date", "is_trading_day"], [["2020-01-02", "1"], ["2020-01-03", "1"]], "1" if self.error else "0")

    def query_stock_basic(self):
        return FakeResult(["code", "code_name", "ipoDate", "outDate", "type", "status"],
                          [["sz.000001", "Test Co", self.ipo_date, self.out_date, "1", "1"]])

    def query_history_k_data_plus(self, symbol, fields, **kwargs):
        columns = fields.split(",")
        values = []
        for i, day in enumerate(["2020-01-02", "2020-01-03"]):
            if day < kwargs.get("start_date", "2020-01-02") or day > kwargs.get("end_date", "2020-01-03"):
                continue
            # The stock has a discontinuous adjusted-price scale, but the daily
            # reference-close return is well defined: 121 / 110 - 1 = 10%.
            record = {"date": day, "code": symbol, "close": "100" if i == 0 else "121",
                      "preclose": "100" if i == 0 else "110", "volume": "100", "amount": "1000",
                      "adjustflag": "1", "tradestatus": "1", "pctChg": "0" if i == 0 else "10"}
            if symbol == "sh.000300":
                record.update({"close": "100" if i == 0 else "110", "preclose": "100"})
            values.append([record[c] for c in columns])
        return FakeResult(columns, values)


class BaoStockProviderTest(unittest.TestCase):
    def test_daily_return_field_is_preserved_and_bad_price_levels_are_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "download"
            sdk = FakeSDK()
            before = socket.getdefaulttimeout()
            manifest = fetch_baostock(["sz.000001"], "sh.000300", "2020-01-02", "2020-01-03", output, sdk)
            self.assertEqual(socket.getdefaulttimeout(), before)
            self.assertEqual(sdk.logout_calls, 1)
            quote = manifest["quote_checks"][0]
            self.assertEqual(quote["same_day_return_gate"], "passed")
            self.assertEqual(quote["adjacent_price_gate"], "failed")
            self.assertAlmostEqual(quote["adjacent_price_anomalies"][0]["absolute_difference"], 0.11)
            with (output / "stock_returns.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[1]["stock_return_pct"], "10")
            settings = json.loads((output / "market_import.json").read_text())
            self.assertEqual(settings["stock_format"], "returns")
            self.assertEqual(settings["stock_return_unit"], "percent")
            self.assertNotIn("stock_price_basis", settings)
            self.assertTrue((output / "sz_000001_raw.csv").exists())
            self.assertTrue((output / "security_basic_raw.csv").exists())
            with self.assertRaisesRegex(ValueError, "fresh empty"):
                fetch_baostock(["sz.000001"], "sh.000300", "2020-01-02", "2020-01-03", output, sdk)

    def test_history_bounds_follow_listing_and_delisting_dates(self):
        for ipo_date, out_date, expected_dates in (
                ("2020-01-03", "", ["2020-01-03"]),
                ("2020-01-02", "2020-01-02", ["2020-01-02"])):
            with self.subTest(ipo_date=ipo_date, out_date=out_date), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "download"
                manifest = fetch_baostock(["sz.000001"], "sh.000300", "2020-01-02", "2020-01-03",
                                          output, FakeSDK(ipo_date=ipo_date, out_date=out_date))
                quote = manifest["quote_checks"][0]
                self.assertEqual(quote["ipo_date"], ipo_date)
                self.assertEqual(quote["query_start_date"], ipo_date)
                self.assertEqual(quote["query_end_date"], out_date or "2020-01-03")
                query = next(q for q in manifest["queries"]
                             if q["function"] == "query_history_k_data_plus" and q["symbol"] == "sz.000001")
                self.assertEqual(query["start_date"], quote["query_start_date"])
                self.assertEqual(query["end_date"], quote["query_end_date"])
                with (output / "sz_000001_raw.csv").open(encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual([row["date"] for row in rows], expected_dates)

    def test_provider_errors_and_bad_rows_abort_without_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "download"
            sdk = FakeSDK(error=True)
            before = socket.getdefaulttimeout()
            with self.assertRaisesRegex(RuntimeError, "query failed"):
                fetch_baostock(["sz.000001"], "sh.000300", "2020-01-02", "2020-01-03", output, sdk)
            self.assertFalse(output.exists())
            self.assertEqual(sdk.logout_calls, 1)
            self.assertEqual(socket.getdefaulttimeout(), before)
            with self.assertRaisesRegex(ValueError, "row width"):
                consume(FakeResult(["a", "b"], [["a"]]))

    def test_same_day_reference_and_benchmark_continuity_must_pass(self):
        sdk = FakeSDK()
        fields, rows = consume(sdk.query_history_k_data_plus("sz.000001", STOCK_FIELDS))
        sessions = [date(2020, 1, 2), date(2020, 1, 3)]
        broken = [r[:] for r in rows]
        broken[1][fields.index("pctChg")] = "9"
        with self.assertRaisesRegex(ValueError, "same-day"):
            prepare_series(fields, broken, "sz.000001", sessions, True)
        broken = [r[:] for r in rows]
        broken[0][fields.index("adjustflag")] = "3"
        with self.assertRaisesRegex(ValueError, "flag 1"):
            prepare_series(fields, broken, "sz.000001", sessions, True)
        with self.assertRaisesRegex(ValueError, "cover"):
            prepare_series(fields, rows[:1], "sz.000001", sessions, True)
        fields_b, rows_b = consume(sdk.query_history_k_data_plus("sh.000300", INDEX_FIELDS))
        rows_b[1][fields_b.index("close")] = "121"
        rows_b[1][fields_b.index("preclose")] = "110"
        with self.assertRaisesRegex(ValueError, "benchmark prices"):
            prepare_series(fields_b, rows_b, "sh.000300", sessions, False)

    def test_calendar_flags_missing_days_and_symbol_collisions_fail(self):
        fields = ["calendar_date", "is_trading_day"]
        rows = [["2020-01-02", "1"], ["2020-01-03", "1"]]
        start, end = date(2020, 1, 2), date(2020, 1, 3)
        self.assertEqual(prepare_calendar(fields, rows, start, end), [start, end])
        with self.assertRaisesRegex(ValueError, "cover every"):
            prepare_calendar(fields, rows[:1], start, end)
        with self.assertRaisesRegex(ValueError, "invalid trading"):
            prepare_calendar(fields, [["2020-01-02", "bad"]], start, end)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "collide"):
                fetch_baostock(["sz.000001", "sh.000001"], "sh.000300", "2020-01-02", "2020-01-03", Path(tmp) / "out", FakeSDK())


if __name__ == "__main__":
    unittest.main()
