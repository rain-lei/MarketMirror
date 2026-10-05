import unittest

from research.data_pipeline.sectioned_policy_rates import parse_sectioned_bulletin


def table(label, tenor, amount, rate):
    return (f'<p>{label} 操作情况</p><table><tr><td>期限</td><td>操作量</td><td>操作利率</td></tr>'
            f'<tr><td>{tenor}</td><td>{amount}亿元</td><td>{rate}%</td></tr></table>')


def bulletin(parts, footer="二〇一九年七月二十三日", day="2019-07-23"):
    return (f'<html><head><title>公开市场业务交易公告 [2019]第142号</title></head><body>'
            f'<span id="shijian">{day} 09:46:10</span><div id="zoom">'
            '<p>开展定向中期借贷便利和中期借贷便利操作。今日不开展逆回购操作。</p>'
            + parts + '<p>中国人民银行公开市场业务操作室</p><p>' + footer + '</p></div></body></html>').encode('utf-8')


class SectionedRateTests(unittest.TestCase):
    def setUp(self):
        self.tmlf = table('TMLF', '1年（可展期2次，实际期限为3年）', 2977, 3.15)
        self.mlf = table('MLF', '1年', 2000, 3.30)

    def parse(self, parts):
        return parse_sectioned_bulletin(bulletin(parts), '2019-07-23', '公开市场业务交易公告 [2019]第142号')

    def test_distinct_table_headings_keep_rates_and_amounts_separate(self):
        result = self.parse(self.tmlf + self.mlf)
        self.assertEqual([(row['instrument'], row['rate_pct'], row['gross_amount_100m_yuan']) for row in result['operations']],
                         [('tmlf', 3.15, 2977.0), ('mlf', 3.30, 2000.0)])
        self.assertIsNone(result['net_liquidity_100m_yuan'])

    def test_renewal_qualifier_preserved_without_merging_with_mlf(self):
        operation = self.parse(self.tmlf + self.mlf)['operations'][0]
        self.assertEqual(operation['tenor_value'], 1)
        self.assertEqual(operation['renewal_count_stated'], 2)
        self.assertEqual(operation['actual_term_years_stated'], 3)
        self.assertEqual(operation['tenor_qualifier_text'], '1年（可展期2次，实际期限为3年）')

    def test_table_order_does_not_determine_the_tool(self):
        result = self.parse(self.mlf + self.tmlf)
        self.assertEqual([row['instrument'] for row in result['operations']], ['mlf', 'tmlf'])
        self.assertEqual(result['operations'][0]['rate_pct'], 3.3)

    def test_missing_adjacent_heading_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'preceding tool heading'):
            self.parse(self.tmlf.replace('<p>TMLF 操作情况</p>', '') + self.mlf)

    def test_duplicate_tool_tables_are_not_silently_combined(self):
        with self.assertRaisesRegex(ValueError, 'distinct MLF/TMLF'):
            self.parse(self.tmlf + self.tmlf)

    def test_inconsistent_renewal_actual_term_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'extension schedule'):
            self.parse(self.tmlf.replace('实际期限为3年', '实际期限为4年') + self.mlf)

    def test_renewable_mlf_not_assumed_to_be_tmlf(self):
        with self.assertRaisesRegex(ValueError, 'extension schedule'):
            self.parse(self.tmlf + table('MLF', '1年（可展期2次，实际期限为3年）', 2000, 3.3))

    def test_unknown_rate_is_not_filled_zero(self):
        with self.assertRaisesRegex(ValueError, 'evidence is incomplete'):
            self.parse(self.tmlf.replace('3.15%', '未知') + self.mlf)

    def test_signature_date_cannot_be_borrowed_from_page_clock(self):
        raw = bulletin(self.tmlf + self.mlf, footer='二〇一九年七月二十四日')
        with self.assertRaisesRegex(ValueError, 'signed date'):
            parse_sectioned_bulletin(raw, '2019-07-23', '公开市场业务交易公告 [2019]第142号')


if __name__ == '__main__':
    unittest.main()
