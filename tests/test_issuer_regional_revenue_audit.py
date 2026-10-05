import unittest
from decimal import Decimal

from research.data_pipeline.audit_issuer_regional_revenue import match_cell, number, verify_reconciliation, verify_roles


class RegionalRevenueIndependentAuditTest(unittest.TestCase):
    def evidence(self,text,box=(0,0,100,20)):
        return {'text':text,'bbox':list(box),'pdf_page':1}

    def test_independent_number_keeps_source_sign_and_zero(self):
        self.assertEqual(number('(1,234.00)'),Decimal('-1234.00'))
        self.assertEqual(number('0.00'),Decimal('0.00'))
        self.assertIsNone(number('—'))
        self.assertIsNone(number('32.65%'))

    def test_same_number_elsewhere_not_a_border_cell_match(self):
        with self.assertRaises(ValueError):
            match_cell([self.evidence('100.00',(200,0,300,20))],self.evidence('100.00'))

    def test_wrong_text_at_correct_geometry_is_not_accepted(self):
        with self.assertRaisesRegex(ValueError,'text differs'):
            match_cell([self.evidence('300.00')],self.evidence('100.00'))

    def test_ambiguous_independent_borders_require_qc(self):
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            match_cell([self.evidence('100.00'),self.evidence('100.00',(1,0,101,20))],self.evidence('100.00'))

    def test_comparative_amount_role_is_rejected(self):
        a=self.evidence('上年同期');b=self.evidence('金额',(0,20,100,40))
        profile={'current_money_column':0,'header_rows':[[a],[b]],'basis':'explicit_current_period_amount_header_not_comparison'}
        with self.assertRaisesRegex(ValueError,'period amount role'):
            verify_roles(profile,{1:[a,b]})

    def data(self):
        return {'rows':[{'region_label_normalized':'中南','printed_current_share':None},{'region_label_normalized':'境外','printed_current_share':None}],
                'issues':[],'reported_shares':[{'region_label_as_printed':'中南','share_of_reported_table_total':'0.4'},
                                             {'region_label_as_printed':'境外','share_of_reported_table_total':'0.6'}]}

    def test_missing_region_is_not_zero_in_closed_table(self):
        with self.assertRaisesRegex(ValueError,'do not close'):
            verify_reconciliation(self.data(),[None,Decimal(100)],Decimal(100))

    def test_closed_total_does_not_override_parent_hierarchy(self):
        data=self.data();data['rows'][0]['region_label_normalized']='母公司合计'
        with self.assertRaisesRegex(ValueError,'hierarchical'):
            verify_reconciliation(data,[Decimal(40),Decimal(60)],Decimal(100))

    def test_source_ratios_are_independently_recomputed(self):
        self.assertEqual(verify_reconciliation(self.data(),[Decimal(40),Decimal(60)],Decimal(100)),2)
        data=self.data();data['reported_shares'][0]['share_of_reported_table_total']='0.75'
        with self.assertRaisesRegex(ValueError,'ratio differs'):
            verify_reconciliation(data,[Decimal(40),Decimal(60)],Decimal(100))


if __name__=='__main__':
    unittest.main()
