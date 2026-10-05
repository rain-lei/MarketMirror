import json
import unittest

from research.data_pipeline.sina_supplement import parse_sina_daily_response


def row(day, close, volume="100"):
    return {"day": day, "open": close, "high": close, "low": close,
            "close": close, "volume": volume}


class SinaSupplementTest(unittest.TestCase):
    def test_adjacent_close_returns_use_pre_window_warmup_and_exact_calendar(self):
        raw = json.dumps([
            row("2019-05-31", "10.0"),
            row("2019-06-03", "11.0"),
            row("2019-06-04", "10.0"),
        ]).encode("gbk")
        rows, check = parse_sina_daily_response(
            raw, "200468", "2019-06-01", "2019-06-04", ["2019-06-03", "2019-06-04"])
        self.assertEqual([item["trade_date"] for item in rows], ["2019-06-03", "2019-06-04"])
        self.assertAlmostEqual(float(rows[0]["stock_return_pct"]), 10.0)
        self.assertAlmostEqual(float(rows[1]["stock_return_pct"]), (10 / 11 - 1) * 100)
        self.assertEqual(check["adjustment_basis"], "unadjusted_close_to_close_price_return")
        self.assertEqual(check["suspended_sessions"], [])

    def test_missing_session_or_prior_close_is_rejected_without_filling(self):
        rows = [row("2019-05-31", "10"), row("2019-06-03", "11")]
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            parse_sina_daily_response(json.dumps(rows).encode("gbk"), "200468", "2019-06-01",
                                      "2019-06-04", ["2019-06-03", "2019-06-04"])
        with self.assertRaisesRegex(ValueError, "prior close"):
            parse_sina_daily_response(json.dumps([row("2019-06-03", "11")]).encode("gbk"),
                                      "200468", "2019-06-01", "2019-06-03", ["2019-06-03"])

    def test_invalid_identity_date_order_duplicate_and_price_are_rejected(self):
        sessions = ["2019-06-03", "2019-06-04"]
        base = [row("2019-05-31", "10"), row("2019-06-03", "11"), row("2019-06-04", "12")]
        with self.assertRaisesRegex(ValueError, "frozen to B-share"):
            parse_sina_daily_response(json.dumps(base).encode("gbk"), "000001", "2019-06-01",
                                      "2019-06-04", sessions)
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            parse_sina_daily_response(json.dumps([base[0], base[2], base[1]]).encode("gbk"),
                                      "200468", "2019-06-01", "2019-06-04", sessions)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_sina_daily_response(json.dumps(base + [base[-1]]).encode("gbk"), "200468",
                                      "2019-06-01", "2019-06-04", sessions)
        bad = [dict(item) for item in base]
        bad[1]["close"] = "0"
        with self.assertRaisesRegex(ValueError, "positive"):
            parse_sina_daily_response(json.dumps(bad).encode("gbk"), "200468", "2019-06-01",
                                      "2019-06-04", sessions)


if __name__ == "__main__":
    unittest.main()
