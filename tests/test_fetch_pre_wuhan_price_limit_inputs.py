import json
import socket
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from research.data_pipeline import fetch_pre_wuhan_price_limit_inputs as fetcher
from research.data_pipeline.provenance import file_sha256

from research.data_pipeline.fetch_pre_wuhan_price_limit_inputs import (
    END_DATE,
    FIELDS,
    START_DATE,
    prepare_calendar,
    validate_daily_rows,
)


class FetchPreWuhanPriceLimitInputsTest(unittest.TestCase):
    def calendar_fixture(self):
        start, end = date.fromisoformat(START_DATE), date.fromisoformat(END_DATE)
        rows = []
        day = start
        while day <= end:
            rows.append([day.isoformat(), "1" if day.weekday() < 5 else "0"])
            day += timedelta(days=1)
        return ["calendar_date", "is_trading_day"], rows

    def row(self, day):
        return [day, "sh.600037", "10.00", "10.10", "9.90", "10.00", "10.00", "1000",
                "10000.00", "3", "1", "0.000000", "0"]

    def test_calendar_window_is_complete_and_frozen(self):
        fields, rows = self.calendar_fixture()
        sessions = prepare_calendar(fields, rows, START_DATE, END_DATE)
        self.assertEqual(len(sessions), 44)
        self.assertEqual((sessions[0], sessions[-1]), (START_DATE, END_DATE))
        with self.assertRaisesRegex(ValueError, "cover"):
            prepare_calendar(fields, rows[:-1], START_DATE, END_DATE)

    def test_raw_bars_validate_unadjusted_identity_and_return_consistency(self):
        sessions = ["2019-10-31"]
        rows = validate_daily_rows(list(FIELDS), [self.row(sessions[0])], "sh.600037", sessions)
        self.assertEqual(rows[0]["adjustflag"], "3")
        self.assertEqual(rows[0]["isST"], "0")
        bad_adjustment = [self.row(sessions[0])]
        bad_adjustment[0][FIELDS.index("adjustflag")] = "1"
        with self.assertRaisesRegex(ValueError, "unadjusted"):
            validate_daily_rows(list(FIELDS), bad_adjustment, "sh.600037", sessions)
        wrong_pct = [self.row(sessions[0])]
        wrong_pct[0][FIELDS.index("pctChg")] = "1.0"
        with self.assertRaisesRegex(ValueError, "consistency"):
            validate_daily_rows(list(FIELDS), wrong_pct, "sh.600037", sessions)

    def test_rejects_missing_or_wrong_security_days(self):
        sessions = ["2019-10-31"]
        with self.assertRaisesRegex(ValueError, "identity"):
            validate_daily_rows(list(FIELDS), [self.row(sessions[0])], "sz.600037", sessions)
        with self.assertRaisesRegex(ValueError, "cover"):
            validate_daily_rows(list(FIELDS), [], "sh.600037", sessions)

    def test_interrupted_download_publishes_nothing_and_restores_socket_timeout(self):
        class Result:
            error_code, error_msg = "0", "success"

            def __init__(self, fields, rows):
                self.fields, self.rows, self.index = fields, rows, -1

            def next(self):
                self.index += 1
                return self.index < len(self.rows)

            def get_row_data(self):
                return self.rows[self.index]

        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            codes = [f"{i:06d}" for i in range(1, 124)]
            old = root / "old.json"
            old.write_text(json.dumps({"benchmark_symbol": "sh.000300", "quote_checks": [
                {"symbol": f"sz.{code}"} for code in codes]}), encoding="utf-8")
            source = root / "source.json"
            source.write_text(json.dumps({"result": {"pipeline_version": "pre-wuhan-common-shock-balanced-sensitivity-v1",
                                                      "sample": {"selected_stock_codes": codes}},
                                          "input_sha256": {str(old.resolve()): file_sha256(old)}}), encoding="utf-8")
            fields, rows = self.calendar_fixture()
            sessions = prepare_calendar(fields, rows, START_DATE, END_DATE)
            first_stock_rows = [self.row(day) for day in sessions]
            for row in first_stock_rows:
                row[FIELDS.index("code")] = "sz.000001"
            sdk = SimpleNamespace(login=Mock(return_value=SimpleNamespace(error_code="0", error_msg="success")),
                                  logout=Mock(), query_trade_dates=Mock(return_value=Result(fields, rows)),
                                  query_history_k_data_plus=Mock(side_effect=[Result(list(FIELDS), first_stock_rows),
                                                                            ConnectionError("interrupted provider")]))
            output = root / "research_outputs" / "partial"
            previous = socket.getdefaulttimeout()
            with patch.object(fetcher, "ROOT", root), patch.object(fetcher, "SOURCE_ARCHIVE", source), \
                    patch.object(fetcher, "SOURCE_DOWNLOAD_MANIFEST", old):
                with self.assertRaisesRegex(ConnectionError, "interrupted"):
                    fetcher.fetch_pre_wuhan_price_limit_inputs(output, sdk)
            self.assertFalse(output.exists())
            self.assertEqual(socket.getdefaulttimeout(), previous)
            sdk.logout.assert_called_once()


if __name__ == "__main__":
    unittest.main()
