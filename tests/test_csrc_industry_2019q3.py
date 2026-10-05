import copy
import unittest

from research.data_pipeline.csrc_industry_2019q3 import HEADER, decode_table, map_cohort, validate_records
from research.data_pipeline.fetch_csrc_industry_2019q3 import publication_and_attachment


def table_fixture():
    # Labels span rows, then change at a real border; wrapped text must survive.
    values = [list(HEADER), ["制造业(C)", "19", "皮革、毛皮、羽毛及其制\n品和制鞋业", "000001", "测试甲"],
              [None, None, None, "000002", "测试乙"], [None, "20", "木材加工", "600003", "测试丙"]]
    cells = [[(i * 10, 0, i * 10 + 10, 10) for i in range(5)],
             [(0, 10, 10, 40), (10, 10, 20, 30), (20, 10, 30, 30), (30, 10, 40, 20), (40, 10, 50, 20)],
             [None, None, None, (30, 20, 40, 30), (40, 20, 50, 30)],
             [None, (10, 30, 20, 40), (20, 30, 30, 40), (30, 30, 40, 40), (40, 30, 50, 40)]]
    return values, cells


class CsrcIndustryTest(unittest.TestCase):
    def test_geometry_resolves_merged_labels_and_keeps_leading_zeros_and_wrapping(self):
        values, cells = table_fixture()
        records = decode_table(9, values, cells)
        self.assertEqual([row["industry_code"] for row in records], ["C19", "C19", "C20"])
        self.assertEqual(records[1]["security_code"], "000002")
        self.assertEqual(records[1]["division_name"], "皮革、毛皮、羽毛及其制品和制鞋业")
        self.assertIn("\n", records[1]["source_text"][2])
        self.assertEqual(records[1]["source_location"]["column_bboxes"][2], [20, 10, 30, 30])
        self.assertEqual(records[1]["source_location"]["pdf_page"], 9)
        validate_records(records)

    def test_missing_page_start_or_partial_cell_coverage_cannot_forward_fill(self):
        values, cells = table_fixture()
        values[1][0] = None
        cells[1][0] = None
        with self.assertRaisesRegex(ValueError, "coverage"):
            decode_table(2, values, cells)
        values, cells = table_fixture()
        cells[1][2] = (20, 10, 30, 25)
        with self.assertRaisesRegex(ValueError, "coverage"):
            decode_table(1, values, cells)

    def test_incomplete_source_text_remains_null_and_never_becomes_zero_or_signal(self):
        values, cells = table_fixture()
        values[1][0] = "居民服务、修理和"
        records = decode_table(86, values, cells)
        validate_records(records)
        self.assertTrue(all(row["industry_code"] is None for row in records))
        self.assertTrue(all(row["division_name"] is None for row in records))
        mapped = map_cohort(records, ["000001", "999999"], "2019-10-29T00:00:00+08:00", "2019-10-30T00:00:00+08:00")
        self.assertEqual([row["status"] for row in mapped], ["source_label_incomplete", "missing_official_security_row"])
        self.assertTrue(all(row["industry_code"] is None and row["impact_magnitude"] is None and not row["agent_signal_enabled"] for row in mapped))

    def test_duplicate_security_and_conflicting_division_are_rejected(self):
        records = decode_table(1, *table_fixture())
        with self.assertRaisesRegex(ValueError, "duplicate security"):
            validate_records(records + [copy.deepcopy(records[0])])
        conflict = copy.deepcopy(records)
        conflict[1]["division_name"] = "另一行业"
        with self.assertRaisesRegex(ValueError, "division mapping"):
            validate_records(conflict)

    def test_future_publication_and_unzoned_dates_cannot_enter_cohort(self):
        records = decode_table(1, *table_fixture())
        with self.assertRaisesRegex(ValueError, "not available"):
            map_cohort(records, ["000001"], "2020-01-10T00:00:00+08:00", "2019-10-30T00:00:00+08:00")
        with self.assertRaisesRegex(ValueError, "time zones"):
            map_cohort(records, ["000001"], "2019-10-29", "2019-10-30")
        with self.assertRaisesRegex(ValueError, "six-digit"):
            map_cohort(records, [1], "2019-10-29T00:00:00+08:00", "2019-10-30T00:00:00+08:00")

    def test_both_verified_and_incomplete_labels_cannot_enable_economic_signals(self):
        for incomplete in (False, True):
            values, cells = table_fixture()
            if incomplete:
                values[1][0] = "居民服务、修理和"
            records = decode_table(1, values, cells)
            records[0]["agent_signal_enabled"] = True
            with self.assertRaisesRegex(ValueError, "economic impact"):
                validate_records(records)

    def test_official_release_identity_is_not_replaced_by_pdf_migration_timestamp(self):
        html = '2019年3季度上市公司行业分类结果 日期：2019-10-28 <a href="/csrc/c100103/c1451997/1451997/files/1616066678064_24727.pdf">附件</a>'
        date, attachment = publication_and_attachment(html)
        self.assertEqual(date, "2019-10-28")
        self.assertTrue(attachment.endswith("1616066678064_24727.pdf"))
        for altered in (html.replace("2019-10-28", "2020-01-10"), html.replace("/csrc/c100103/c1451997/1451997/files/", "https://example.com/")):
            with self.assertRaises(ValueError):
                publication_and_attachment(altered)


if __name__ == "__main__":
    unittest.main()
