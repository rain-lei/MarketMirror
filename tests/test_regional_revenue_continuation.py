import unittest

from research.data_pipeline.regional_revenue_tables_v2 import continuation_context_is_safe, parse_region_block, role_profile, unit_from_context
from tests.test_regional_revenue_tables import frame


class RegionalRevenueContinuationTest(unittest.TestCase):
    def test_missing_earlier_region_cannot_turn_into_zero_at_continuation(self):
        first=frame([["地区","营业收入"],["华北",None],["华南","100.00"]])
        profile=role_profile(first); unit=unit_from_context([{"text":"单位：人民币元"}])
        first_result=parse_region_block(first,profile,1,"FULL_REVENUE_COMPOSITION",unit)
        following=frame([["合计","100.00"]],2)
        result=parse_region_block(following,profile,0,"FULL_REVENUE_COMPOSITION",unit,first_result['rows'],None,first_result['issues'])
        self.assertIsNone(result['reported_shares'])
        self.assertIn('missing_or_unparsable_current_revenue',result['issues'])

    def test_unlabeled_earlier_number_issue_survives_continuation(self):
        first=frame([["地区","营业收入"],[None,"10.00"],["华南","100.00"]])
        profile=role_profile(first); unit=unit_from_context([{"text":"单位：人民币元"}])
        earlier=parse_region_block(first,profile,1,"FULL_REVENUE_COMPOSITION",unit)
        following=frame([["合计","100.00"]],2)
        result=parse_region_block(following,profile,0,"FULL_REVENUE_COMPOSITION",unit,earlier['rows'],None,earlier['issues'])
        self.assertIn('numeric_row_without_region_label',result['issues'])
        self.assertIsNone(result['reported_shares'])

    def context(self,text):
        return {'pdf_page':2,'bbox':[0,35,100,45],'text':text}

    def safe(self,context,unit=None):
        a={'pdf_page':1,'bbox':[0,700,300,760]};b={'pdf_page':2,'bbox':[0,70,300,200]}
        return continuation_context_is_safe(a,b,context,None,None,{'scale':1},unit or {'scale':1})

    def test_new_section_blocks_carry_even_with_same_column_widths(self):
        self.assertFalse(self.safe([self.context('三、非主营业务分析')]))
        self.assertFalse(self.safe([self.context('注：该项资产另行披露')]))

    def test_report_header_and_page_number_can_precede_continuation(self):
        self.assertTrue(self.safe([self.context('公司2019年半年度报告全文'),self.context('19')]))

    def test_unit_change_is_not_retroactively_applied_to_earlier_page(self):
        self.assertFalse(self.safe([],{'scale':10000}))


if __name__=='__main__':
    unittest.main()
