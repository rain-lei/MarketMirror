import csv
import json
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from research.baselines.activity_event_study import summarize_run
from research.data_pipeline.fetch_baostock import DOCUMENTATION, fetch_baostock
from research.data_pipeline.market_activity import import_activity, parse_activity_row
from research.data_pipeline.market_data import import_market
from research.data_pipeline.provenance import file_sha256


class FakeResult:
    def __init__(self, fields, rows):
        self.fields, self.rows, self.error_code, self.error_msg, self.index = fields, rows, "0", "success", -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class ActivitySDK:
    def login(self):
        return FakeResult([], [])

    def logout(self):
        pass

    def query_trade_dates(self, **kwargs):
        return FakeResult(["calendar_date", "is_trading_day"], [["2020-01-02", "1"], ["2020-01-03", "1"]])

    def query_stock_basic(self):
        return FakeResult(["code", "code_name", "ipoDate", "outDate", "type", "status"],
                          [["sz.000001", "Activity Fixture", "2000-01-01", "", "1", "1"]])

    def query_history_k_data_plus(self, symbol, fields, **kwargs):
        columns = fields.split(",")
        rows = []
        for i, day in enumerate(("2020-01-02", "2020-01-03")):
            base = {"date": day, "code": symbol, "close": "100" if i == 0 else "110",
                    "preclose": "100", "volume": "100" if i == 0 else "200",
                    "amount": "1000.00" if i == 0 else "2200.00",
                    "adjustflag": "1", "tradestatus": "1", "pctChg": "0" if i == 0 else "10"}
            rows.append([base[name] for name in columns])
        return FakeResult(columns, rows)


def setup_inputs(root):
    download = root / "download"
    fetch_baostock(["sz.000001"], "sh.000300", "2020-01-02", "2020-01-03", download, ActivitySDK())
    import_market(download / "market_import.json", root / "prepared")
    evidence = root / "evidence"
    evidence.mkdir()
    document = evidence / "api.md"
    document.write_text("| volume | 成交量（累计，单位：股） |\n| amount | 成交额（单位：人民币元） |", encoding="utf-8")
    (evidence / "evidence_manifest.json").write_text(json.dumps({"sources": [
        {"url": DOCUMENTATION, "path": "api.md", "sha256": file_sha256(document)}]}), encoding="utf-8")
    config = {"run_id": "test", "data_kind": "observed", "download_dir": "../download",
              "market_manifest": "../prepared/market_manifest.json", "evidence_manifest": "../evidence/evidence_manifest.json",
              "stock_symbols": ["sz.000001"]}
    (root / "configs").mkdir()
    path = root / "configs/config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path, download, evidence


def activity_row(day, volume, amount):
    return {"date": day, "code": "sz.000001", "close": "100", "preclose": "100", "volume": volume,
            "amount": amount, "adjustflag": "1", "tradestatus": "1", "pctChg": "0"}


class MarketActivityTest(unittest.TestCase):
    def test_units_status_and_exact_decimal_are_checked(self):
        self.assertEqual(parse_activity_row(activity_row("2020-01-02", "100", "1000.0100"), "sz.000001")["amount_cny"], "1000.0100")
        for volume, amount in (("-1", "100"), ("1.5", "100"), ("10", "NaN"), ("10", "Infinity"), ("0", "0")):
            with self.assertRaises(ValueError):
                parse_activity_row(activity_row("2020-01-02", volume, amount), "sz.000001")
        suspended = activity_row("2020-01-02", "0", "0")
        suspended["tradestatus"] = "0"
        self.assertEqual(parse_activity_row(suspended, "sz.000001")["trading_status"], "suspended")
        suspended["amount"] = "1"
        with self.assertRaisesRegex(ValueError, "zero volume and amount"):
            parse_activity_row(suspended, "sz.000001")
        suspended["amount"], suspended["close"] = "0", "101"
        with self.assertRaisesRegex(ValueError, "nonflat"):
            parse_activity_row(suspended, "sz.000001")

    def test_end_to_end_download_market_document_and_tamper_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, download, evidence = setup_inputs(root)
            report = import_activity(config, root / "activity")
            self.assertEqual(report["counts"], {"rows": 2, "stocks": 1, "sessions": 2})
            self.assertEqual(report["units"], {"volume_shares": "share", "amount_cny": "CNY"})
            with (root / "activity/market_activity.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([r["volume_shares"] for r in rows], ["100", "200"])
            self.assertEqual([r["amount_cny"] for r in rows], ["1000.00", "2200.00"])
            self.assertEqual(report["artifacts"]["market_activity.csv"]["sha256"], file_sha256(root / "activity/market_activity.csv"))
            with self.assertRaisesRegex(ValueError, "new empty"):
                import_activity(config, root / "activity")
            doc = evidence / "api.md"
            doc.write_text("units changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "documentation hash"):
                import_activity(config, root / "second")
            doc.write_text("| volume | 成交量（累计，单位：股） |\n| amount | 成交额（单位：人民币元） |", encoding="utf-8")
            raw = download / "sz_000001_raw.csv"
            raw.write_text(raw.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "download artifact changed"):
                import_activity(config, root / "second")

    def test_event_window_uses_only_earlier_estimation_median(self):
        days = [date(2020, 1, 1) + timedelta(days=i) for i in range(30)]
        series = {d.isoformat(): {"volume_shares": 100, "amount_cny": Decimal("1000"), "trading_status": "trading"} for d in days}
        series[days[26].isoformat()] = {"volume_shares": 250, "amount_cny": Decimal("2200"), "trading_status": "trading"}
        window = [{"trade_date": days[i].isoformat(), "relative_trade_day": i - 26, "abnormal_return": 0.0} for i in (25, 26, 27)]
        run = {"event_id": "test", "stock_code": "000001", "event_date_requested": days[26].isoformat(),
               "event_date_used": days[26].isoformat(), "visibility_alignment_rule": "fixture",
               "event_definition": {"event_type": "fixture"}, "estimation_start": days[0].isoformat(),
               "estimation_end": days[19].isoformat(), "estimation_observations": 20,
               "window_before": 1, "window_after": 1, "abnormal_returns": window,
               "cumulative_abnormal_return": 0.0}
        summary, observations = summarize_run(run, {"000001": series}, 20)
        self.assertEqual(summary["estimation_median_amount_cny"], "1000")
        self.assertEqual(summary["event_day_volume_fold"], 2.5)
        self.assertEqual(summary["event_day_amount_fold"], 2.2)
        self.assertEqual(len(observations), 3)
        invalid = {**run, "estimation_end": days[25].isoformat(), "estimation_observations": 26}
        with self.assertRaisesRegex(ValueError, "overlaps"):
            summarize_run(invalid, {"000001": series}, 20)
        invalid = {**run, "estimation_observations": 21}
        with self.assertRaisesRegex(ValueError, "differ"):
            summarize_run(invalid, {"000001": series}, 20)
        series.pop(days[26].isoformat())
        with self.assertRaisesRegex(ValueError, "lacks observed"):
            summarize_run(run, {"000001": series}, 20)


if __name__ == "__main__":
    unittest.main()
