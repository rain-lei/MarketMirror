import unittest
from copy import deepcopy

from research.data_pipeline.regional_revenue_tables import decimal_text, money_cell, parse_region_block, role_profile, scope_heading, unit_from_context


def frame(values, page=1):
    width = len(values[0])
    return {"pdf_page": page, "column_count": width, "column_edges": [100*i for i in range(width+1)],
            "rows": [[None if text is None else {"text": text, "pdf_page": page, "bbox": [100*j, 20*i, 100*(j+1), 20*(i+1)]} for j, text in enumerate(row)] for i, row in enumerate(values)]}


class RegionalRevenueTablesTest(unittest.TestCase):
    def unit(self):
        return unit_from_context([{"text": "单位：人民币元", "pdf_page": 1, "bbox": [0,0,100,10]}])

    def full(self):
        return frame([["", "本报告期", None, "上年同期", None], ["", "金额", "占营业收入比重", "金额", "占营业收入比重"],
                      ["营业收入合计", "100.00", "100%", "200.00", "100%"], ["分地区", None,None,None,None],
                      ["中南", "40.00", "40.00%", "150.00", "75.00%"], ["境外", "60.00", "60.00%", "50.00", "25.00%"]])

    def test_current_period_not_larger_comparative_selected(self):
        source=self.full(); profile=role_profile(source)
        result=parse_region_block(source,profile,4,"FULL_REVENUE_COMPOSITION",self.unit())
        self.assertEqual(profile["current_money_column"],1)
        self.assertEqual(result["rows"][0]["current_revenue"]["reported_value"],"40.00")
        self.assertEqual(result["reported_shares"][0]["share_of_reported_table_total"],"0.4")
        self.assertIsNone(result["rows"][0]["finer_geography_share"])
        self.assertFalse(result["agent_signal_enabled"])

    def test_explicit_zero_diff_is_reconciled_at_source_precision(self):
        source=self.full(); result=parse_region_block(source,role_profile(source),4,"FULL_REVENUE_COMPOSITION",self.unit())
        self.assertEqual(result["regional_sum_minus_explicit_total"],"0.00")
        self.assertTrue(result["issuer_total_revenue_coverage_candidate"])

    def test_cost_and_yoy_headers_not_treated_as_current_revenue(self):
        source=frame([["分地区","营业收入","营业成本","营业收入比上年同期增减"],["境内","30.00","29.00","100%"]])
        profile=role_profile(source); result=parse_region_block(source,profile,1,"MAIN_BUSINESS_SCOPE_ONLY",self.unit())
        self.assertEqual(result["rows"][0]["current_revenue"]["reported_value"],"30.00")
        self.assertIsNone(result["reported_shares"])

    def test_partial_table_not_renormalized(self):
        source=frame([["地区","营业收入"],["华北","70.00"]])
        result=parse_region_block(source,role_profile(source),1,"MAIN_BUSINESS_SCOPE_ONLY",self.unit())
        self.assertIsNone(result["total"])
        self.assertIsNone(result["reported_shares"])
        self.assertFalse(result["issuer_total_revenue_coverage_candidate"])

    def test_missing_and_dash_do_not_become_zero(self):
        self.assertIsNone(money_cell(None)["reported_value"])
        self.assertIsNone(money_cell({"text":"—"})["reported_value"])
        self.assertEqual(money_cell({"text":"0.00"})["reported_value"],"0.00")
        source=self.full(); source["rows"][4][1]["text"]=""
        result=parse_region_block(source,role_profile(source),4,"FULL_REVENUE_COMPOSITION",self.unit())
        self.assertIsNone(result["reported_shares"])
        self.assertIn("missing_or_unparsable_current_revenue",result["issues"])

    def test_unit_and_same_cell_wrapping_are_preserved(self):
        self.assertEqual(decimal_text("2,658,783,198.\n32"),"2658783198.32")
        self.assertIsNone(decimal_text("1,23.00"))
        source=self.full(); unit=unit_from_context([{"text":"单位：万元 币种：人民币"}])
        result=parse_region_block(source,role_profile(source),4,"FULL_REVENUE_COMPOSITION",unit)
        self.assertEqual(result["rows"][0]["current_revenue_yuan_at_reported_unit"],"400000.00")
        unknown=parse_region_block(source,role_profile(source),4,"FULL_REVENUE_COMPOSITION",unit_from_context([]))
        self.assertIsNone(unknown["reported_shares"])

    def test_hierarchical_parent_subtotal_blocks_coverage(self):
        source=self.full(); source["rows"][4][0]["text"]="母公司合计"
        result=parse_region_block(source,role_profile(source),4,"FULL_REVENUE_COMPOSITION",self.unit())
        self.assertIn("hierarchical_or_mixed_company_scope",result["issues"])
        self.assertIsNone(result["reported_shares"])

    def test_comparative_only_header_is_not_guessed(self):
        source=self.full(); source["rows"][0][1]["text"]="上年同期"
        self.assertIsNone(role_profile(source))

    def test_continuation_requires_matching_physical_columns(self):
        source=self.full(); profile=role_profile(source)
        continuation=frame([["华北","70.00","70%","90.00","45%"]],2)
        self.assertEqual(role_profile(continuation,profile),profile)
        altered=deepcopy(continuation); altered["column_edges"][2]+=10
        self.assertIsNone(role_profile(altered,profile))

    def test_wrong_printed_percent_blocks_closed_table(self):
        source=self.full(); source["rows"][4][2]["text"]="75.00%"
        result=parse_region_block(source,role_profile(source),4,"FULL_REVENUE_COMPOSITION",self.unit())
        self.assertIsNone(result["reported_shares"])
        self.assertIn("printed_share_not_reconciled_to_current_total",result["issues"])

    def test_material_subset_not_upgraded_to_full_scope(self):
        self.assertEqual(scope_heading("（2）占公司营业收入或营业利润10%以上的行业、产品或地区情况"),"MATERIAL_SEGMENTS_ONLY")
        self.assertEqual(scope_heading("（1）营业收入构成："),"FULL_REVENUE_COMPOSITION")
        self.assertIsNone(scope_heading("说明：营业收入构成详见下文"))


if __name__ == "__main__":
    unittest.main()
