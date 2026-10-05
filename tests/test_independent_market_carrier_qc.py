import json
from pathlib import Path
import unittest

from research.data_pipeline.audit_eastmoney_carrier import (
    bounded_path, carrier_body, compare_pair, dense_source_rows, raw_series, strict_json,
)


class IndependentMarketCarrierQCTests(unittest.TestCase):
    spec = {"secid": "0.000001", "fqt": 0, "kind": "stock"}
    calendar = ["2021-01-04", "2021-01-05"]
    row = "2021-01-04,14.00,14.01,14.02,13.99,0,0,0.21,0.07,0.01,-"

    def body(self, rows=None, **changes):
        data = {"code": "000001", "market": 0, "name": "source display name", "klines": rows if rows is not None else [self.row]}
        data.update(changes)
        return json.dumps({"rc": 0, "data": data}).encode()

    def parse(self, body):
        return raw_series(body, "0.000001", self.calendar[0], self.calendar[-1], self.calendar)

    def test_duplicate_json_keys_and_nonfinite_constants_are_rejected(self):
        for body in ['{"rc":1,"rc":0}', '{"data":{"code":"other","code":"000001"}}', '{"data":NaN}']:
            with self.subTest(body=body), self.assertRaises(ValueError):
                strict_json(body)

    def test_mismatched_code_exchange_and_boolean_response_code_are_rejected(self):
        for body in [self.body(code="000002"), self.body(market=True), self.body().replace(b'"rc": 0', b'"rc": false')]:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.parse(body)

    def test_failed_source_and_missing_row_have_distinct_unknown_records(self):
        series = self.parse(self.body())
        present, missing = list(dense_source_rows(series, self.spec, self.calendar))
        failed = list(dense_source_rows(None, self.spec, self.calendar))
        self.assertEqual(present["provider_fields"]["f56"], "0")
        self.assertEqual(missing["source_observation_status"], "SOURCE_ROW_ABSENT_NOT_CERTIFIED_SUSPENSION")
        self.assertEqual(failed[0]["source_observation_status"], "SOURCE_REQUEST_FAILED")
        for row in [missing, *failed]:
            self.assertIsNone(row["raw_line"])
            self.assertTrue(all(v is None for v in row["provider_fields"].values()))
            self.assertIsNone(row["certified_trade_status"])
            self.assertIs(row["model_eligible"], False)

    def test_blank_and_zero_source_fields_remain_distinct(self):
        series = self.parse(self.body())
        self.assertEqual(series["rows"][self.calendar[0]]["provider_fields"]["f61"], "-")
        self.assertEqual(series["inspection"]["blank_numeric_fields"], [{"trade_date": self.calendar[0], "field": "f61"}])
        self.assertEqual(series["inspection"]["zero_volume_dates_without_explicit_trade_status"], [self.calendar[0]])
        self.assertIs(series["inspection"]["explicit_trade_status_supplied"], False)

    def test_duplicate_reversed_outside_and_noncanonical_dates_are_rejected(self):
        for rows in [[self.row, self.row], [self.row.replace("01-04", "01-05"), self.row],
                     [self.row.replace("01-04", "01-06")], [self.row.replace("2021-01-04", "20210104")]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.parse(self.body(rows))

    def test_negative_and_nonfinite_volume_amount_are_rejected(self):
        for value in ["-1", "NaN", "Infinity", "not-a-number"]:
            columns = self.row.split(",")
            columns[5] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse(self.body([",".join(columns)]))

    def test_carrier_with_extra_text_and_truncation_is_rejected(self):
        body = self.body().decode()
        self.assertEqual(carrier_body("```json\n" + body + "\n```"), body.encode())
        for text in ["extra\n```json\n" + body + "\n```", "```json\n" + body + "\n```\nextra", "```json\n{\n```"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                carrier_body(text)

    def test_artifact_cannot_escape_declared_source_folder(self):
        root = Path("declared-source").resolve()
        self.assertEqual(bounded_path(root, root / "attempt_1" / "carrier.md"), root / "attempt_1" / "carrier.md")
        with self.assertRaises(ValueError):
            bounded_path(root, root / ".." / "other-source" / "carrier.md")

    def test_incomplete_pair_does_not_claim_zero_difference(self):
        first = self.parse(self.body())
        incomplete = compare_pair(first, None)
        self.assertIsNone(incomplete["field_difference_counts"])
        self.assertIsNone(incomplete["common_rows"])
        columns = self.row.split(",")
        columns[2] = "14.02"
        second = self.parse(self.body([",".join(columns)]))
        pair = compare_pair(first, second)
        self.assertEqual(pair["field_difference_counts"]["f53"], 1)
        self.assertIs(pair["basis_verified_for_model_use"], False)


if __name__ == "__main__":
    unittest.main()
