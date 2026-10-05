"""Printed note references cannot hide cash rows or become cash amounts."""

import unittest

from research.data_pipeline.half_year_balance_context_v2 import extract_company, separate_note_references
from research.data_pipeline.financial_balance_sheet import geometry_rows
from tests.test_financial_balance_sheet import row, sheet


def note_row(label, reference, current="10.00", previous="20.00", y=120):
    words = [(50, y, 160, y + 10, label), (210, y, 230, y + 10, reference),
             (265, y, 320, y + 10, current), (445, y, 500, y + 10, previous)]
    return geometry_rows(words, 1, 850)[0]


def with_note_header():
    rows = sheet(current="2019年6月30日")
    header = [(50, 100, 160, 110, "项目"), (210, 100, 230, 110, "附注"),
              (265, 100, 320, 110, "2019年6月30日"), (445, 100, 500, 110, "2018年12月31日")]
    rows[3] = geometry_rows(header, 1, 850)[0]
    return rows


class NoteColumnTests(unittest.TestCase):
    def test_chinese_note_reference_does_not_hide_cash(self):
        rows = with_note_header()
        rows[4] = note_row("货币资金", "七、1")
        value = extract_company(rows, "2019-06-30")
        self.assertEqual(value["status"], "UNIQUE_RECONCILED_CANDIDATE")
        self.assertEqual(value["candidates"][0]["fields"]["money_funds"]["cells"][0]["reported_value"], "10.00")
        self.assertEqual(value["financial_note_references_retained_separately"][0]["note_word"]["text"], "七、1")

    def test_integer_note_reference_is_not_an_unassigned_financial_value(self):
        rows = with_note_header()
        rows[4] = note_row("货币资金", "1")
        value = extract_company(rows, "2019-06-30")["candidates"][0]
        self.assertEqual(value["fields"]["money_funds"]["cells"][0]["status"], "NUMERIC")

    def test_no_printed_note_header_does_not_authorize_removal(self):
        rows = sheet(current="2019年6月30日")
        rows[4] = note_row("货币资金", "七、1")
        self.assertEqual(separate_note_references(rows)[1], [])

    def test_real_zero_inside_financial_column_is_preserved(self):
        rows = with_note_header()
        rows[4] = note_row("货币资金", "七、1", current="0.00")
        self.assertEqual(extract_company(rows, "2019-06-30")["candidates"][0]
                         ["fields"]["money_funds"]["cells"][0]["reported_value"], "0.00")

    def test_parent_or_profit_section_stops_note_interval(self):
        rows = with_note_header() + [row("合并利润表", y=230), note_row("货币资金", "七、1", y=250)]
        _, removed = separate_note_references(rows)
        self.assertEqual(removed, [])

    def test_unknown_label_is_not_reconstructed_by_deleting_numbers(self):
        rows = with_note_header() + [note_row("不明字段", "1", y=230)]
        self.assertEqual(separate_note_references(rows)[1], [])


if __name__ == "__main__":
    unittest.main()
