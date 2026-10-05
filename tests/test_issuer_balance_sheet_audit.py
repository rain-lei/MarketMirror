"""The independent border reader must retain padding, blanks and merged dates."""

import unittest
from types import SimpleNamespace

from research.data_pipeline.audit_issuer_balance_sheets import (
    ContentTable, bordered_field_rows, independent_cell, table_columns,
)


class FakeTable:
    def __init__(self, values, positions):
        self.values = values
        self.rows = [SimpleNamespace(cells=[None if v is None else (positions[i], 80 + j * 15, positions[i + 1], 95 + j * 15)
                                           for i, v in enumerate(row)]) for j, row in enumerate(values)]
        self.bbox = (positions[0], 80, positions[-1], 80 + len(values) * 15)

    def extract(self):
        return self.values


class IndependentBorderAuditTests(unittest.TestCase):
    def test_empty_padding_columns_preserve_real_date_rectangles(self):
        table = FakeTable([["", "项目", "", "", "2019年9月30日", "", "", "2018年12月31日", ""],
                           ["", "资产总计", "", "", "100.00", "", "", "90.00", ""]],
                          [50, 55, 200, 205, 210, 365, 370, 375, 530, 535])
        cleaned = ContentTable(table)
        self.assertEqual(len(cleaned.extract()[0]), 3)
        columns = table_columns(cleaned, "consolidated", 2, {"assets": ["资产总计"]})
        self.assertEqual([c["period_end"] for c in columns], ["2019-09-30", "2018-12-31"])

    def test_previous_amount_in_padded_table_is_not_shifted_left(self):
        table = FakeTable([["", "货币资金", "", "", "", "", "", "20.00", ""],
                           ["", "资产总计", "", "", "100.00", "", "", "90.00", ""]],
                          [50, 55, 200, 205, 210, 365, 370, 375, 530, 535])
        rows = bordered_field_rows(table, {"货币资金": "cash", "资产总计": "assets"}, 2)
        self.assertEqual(rows[0][3][0]["status"], "REPORTED_BLANK")
        self.assertIsNone(rows[0][3][0]["reported_value"])
        self.assertEqual(rows[0][3][1]["reported_value"], "20.00")

    def test_wrapped_border_label_includes_intermediate_previous_amount(self):
        table = FakeTable([["", "资产总计", "", "100.00", "", "90.00", ""],
                           ["", "负债和所有者权益（或股东权益）", "", "100.00", "", "", ""],
                           [None, "", None, None, "", "90.00", ""],
                           [None, "总计", None, None, None, None, None]],
                          [50, 55, 200, 205, 365, 370, 530, 535])
        rows = bordered_field_rows(table, {"资产总计": "assets", "负债和所有者权益（或股东权益）总计": "balance_total"}, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual([c["reported_value"] for c in rows[1][3]], ["100.00", "90.00"])

    def test_unknown_neighbor_label_is_not_joined_to_partial_total(self):
        table = FakeTable([["资产总计", "100.00", "90.00"],
                           ["负债和所有者权益", "100.00", "90.00"],
                           ["其他说明", "", ""]], [50, 200, 365, 535])
        rows = bordered_field_rows(table, {"资产总计": "assets", "负债和所有者权益总计": "balance_total"}, 2)
        self.assertEqual([r[0] for r in rows], ["assets"])

    def test_multiple_amount_rectangles_in_one_column_are_rejected(self):
        table = FakeTable([["资产总计", "100.00", "", "90.00"],
                           ["货币资金", "10.00", "11.00", "20.00"]], [50, 200, 365, 370, 535])
        with self.assertRaises(ValueError):
            bordered_field_rows(table, {"资产总计": "assets", "货币资金": "cash"}, 2)

    def test_malformed_number_and_dash_cannot_become_numeric_zero(self):
        self.assertEqual(independent_cell("12,34.00")["status"], "UNPARSABLE_INDEPENDENT_CELL")
        self.assertIsNone(independent_cell("--")["reported_value"])
        self.assertEqual(independent_cell("0.00")["reported_value"], "0.00")

    def test_missing_date_cell_does_not_inherit_previous_date(self):
        table = FakeTable([["项目", "2019年9月30日", ""], ["资产总计", "100.00", "90.00"]], [50, 200, 365, 535])
        with self.assertRaises(ValueError): table_columns(table, "consolidated", 2, {"assets": ["资产总计"]})

    def test_merged_date_inheritance_requires_covering_rectangle(self):
        table = FakeTable([["资产", "2019年9月30日", None, "2018年12月31日", None],
                           [None, "合并数", "公司数", "合并数", "公司数"],
                           ["资产合计", "100.00", "90.00", "80.00", "70.00"]], [50, 180, 270, 360, 450, 540])
        with self.assertRaises(ValueError):
            table_columns(table, "combined", 4, {"assets": ["资产合计"]})
        table.rows[0].cells[1] = (180, 80, 360, 95)
        table.rows[0].cells[3] = (360, 80, 540, 95)
        columns = table_columns(table, "combined", 4, {"assets": ["资产合计"]})
        self.assertEqual([(c["scope"], c["period_end"]) for c in columns],
                         [("consolidated", "2019-09-30"), ("parent", "2019-09-30"),
                          ("consolidated", "2018-12-31"), ("parent", "2018-12-31")])


if __name__ == "__main__":
    unittest.main()
