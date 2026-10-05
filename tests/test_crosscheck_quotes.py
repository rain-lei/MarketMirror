import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from research.data_pipeline.crosscheck_quotes import compare_series, parse_eastmoney_capture


class CrossProviderQuoteTest(unittest.TestCase):
    def test_capture_identity_order_and_daily_percent_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.md"
            start = date(2018, 1, 1)
            klines = []
            for index in range(130):
                day = start + timedelta(days=index)
                klines.append(f"{day.isoformat()},10,11,12,9,100,1000,3,1.23,0.1,0.5")
            payload = {"rc": 0, "data": {"code": "000001", "market": 0, "klines": klines}}
            path.write_text("```json\n" + json.dumps(payload) + "\n```", encoding="utf-8")
            parsed = parse_eastmoney_capture(path, "000001", start, start + timedelta(days=130))
            self.assertEqual(len(parsed), 130)
            self.assertEqual(parsed[start.isoformat()], 1.23)
            payload["data"]["market"] = 1
            path.write_text("```json\n" + json.dumps(payload) + "\n```", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "identity"):
                parse_eastmoney_capture(path, "000001", start, start + timedelta(days=130))
            payload["data"]["market"] = 0
            payload["data"]["klines"][1] = payload["data"]["klines"][0]
            path.write_text("```json\n" + json.dumps(payload) + "\n```", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "strictly ordered"):
                parse_eastmoney_capture(path, "000001", start, start + timedelta(days=130))

    def test_aligned_rounding_and_material_mismatch_are_distinct(self):
        east = {"2018-05-02": 1.23, "2018-05-03": -2.91}
        bao = {"2018-05-02": 1.2251, "2018-05-03": 0.9969}
        result = compare_series(east, bao, 0.0052, 0.02)
        self.assertEqual(result["sessions"], 2)
        self.assertEqual(result["beyond_rounding_days"], 1)
        self.assertEqual([row["trade_date"] for row in result["material_differences"]], ["2018-05-03"])
        with self.assertRaisesRegex(ValueError, "calendars differ"):
            compare_series(east, {"2018-05-02": 1.23}, 0.0052, 0.02)


if __name__ == "__main__":
    unittest.main()
