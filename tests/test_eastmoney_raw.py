import json
import unittest

from research.data_pipeline.fetch_eastmoney_raw import inspect_response, request_url


class EastMoneyRawBoundaryTests(unittest.TestCase):
    dates = ["2020-09-01", "2020-09-02"]

    def packet(self, rows=None, code="000001", market=0):
        if rows is None:
            rows = [f"{d},14,14.1,14.2,13.9,100,140000,2,0.7,0.1,0.1" for d in self.dates]
        return json.dumps({"rc": 0, "data": {"code": code, "market": market, "klines": rows}}).encode()

    def inspect(self, raw):
        return inspect_response(raw, "0.000001", *[self.dates[0], self.dates[-1]], self.dates)

    def test_complete_raw_calendar_does_not_open_model_gate(self):
        result = self.inspect(self.packet())
        self.assertTrue(result["calendar_coverage_complete"])
        self.assertFalse(result["model_eligible"])
        self.assertFalse(result["basis_verified_for_model_use"])
        self.assertFalse(result["explicit_trade_status_supplied"])

    def test_calendar_gap_is_preserved_without_zero_rows(self):
        result = self.inspect(self.packet(["2020-09-01,14,14.1,14.2,13.9,100,140000,2,0.7,0.1,0.1"]))
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["missing_expected_sessions"], ["2020-09-02"])
        self.assertFalse(result["calendar_coverage_complete"])
        self.assertFalse(result["model_eligible"])

    def test_wrong_stock_and_exchange_are_rejected(self):
        for packet in [self.packet(code="000002"), self.packet(market=1)]:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                self.inspect(packet)

    def test_future_duplicate_and_nonfinite_records_are_rejected(self):
        base = "2020-09-01,14,14.1,14.2,13.9,100,140000,2,0.7,0.1,0.1"
        for rows in [[base, base], [base.replace("2020-09-01", "2020-09-03")], [base.replace("14.1", "nan")]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.inspect(self.packet(rows))

    def test_zero_volume_does_not_infer_suspension_and_requests_are_bounded(self):
        result = self.inspect(self.packet(["2020-09-01,14,14,14,14,0,0,0,0,0,-"]))
        self.assertEqual(result["zero_volume_dates_without_explicit_trade_status"], ["2020-09-01"])
        self.assertFalse(result["explicit_trade_status_supplied"])
        self.assertFalse(result["model_eligible"])
        for secid, fqt, start, end in [("0.000001", True, *self.dates), ("2.000001", 0, *self.dates),
            ("0.000001", 0, self.dates[1], self.dates[0])]:
            with self.subTest(secid=secid, fqt=fqt, start=start), self.assertRaises(ValueError):
                request_url(secid, fqt, start, end)


if __name__ == "__main__":
    unittest.main()
