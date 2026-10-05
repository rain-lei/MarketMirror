import unittest

from research.data_pipeline.build_issuer_regional_revenue_states import component_kind,merge_company


class RegionalRevenueStatesTest(unittest.TestCase):
    def inputs(self):
        parsed={'status':'NUMERIC_REGIONS_WITHOUT_COMPLETE_DENOMINATOR','unit':{'currency':'CNY_EXPLICIT','scale':1},
                'table_scope':'MAIN_BUSINESS_SCOPE_ONLY','issues':[], 'total':None,'reported_shares':None,
                'issuer_total_revenue_coverage_candidate':False,
                'rows':[{'region_label_as_printed':'武汉','region_label_normalized':'武汉','region_label_cell':{'text':'武汉'},
                         'current_revenue':{'reported_value':'30.00','status':'NUMERIC','source_cell':{'text':'30.00'}},
                         'current_revenue_yuan_at_reported_unit':'30.00'}]}
        source={'stock_code':'X','historical_short_name':'fixture','report_metadata':{'report_end_date':'2019-06-30'},
                'source_pdf_path':'fixture','source_pdf_sha256':'fixture','literal_reader_status':'PASS_LITERAL_PAGE_MAPS',
                'status':'REGIONAL_CELL_CANDIDATES_REQUIRE_INDEPENDENT_AUDIT',
                'tables':[{'frames':[{'pdf_page':1}],'source_reader_qc_required':False,'parsed':parsed}]}
        audit={'stock_code':'X','tables':[{'table_index':0,'status':'PASS_SOURCE_CELLS_AND_RECONCILIATION'}]}
        return source,audit

    def test_partial_revenue_is_preserved_without_invented_ratio(self):
        result=merge_company(*self.inputs());row=result['tables'][0]['rows'][0]
        self.assertEqual(row['verified_current_revenue_yuan'],'30.00')
        self.assertIsNone(row['verified_share_of_reported_table_total'])
        self.assertIsNone(row['finer_geography_exposure'])
        self.assertFalse(result['agent_signal_enabled'])

    def test_reader_warning_cannot_be_bypassed_by_numeric_cell_pass(self):
        source,audit=self.inputs();source['literal_reader_status']='READER_DIAGNOSTIC_REVIEW_REQUIRED'
        row=merge_company(source,audit)['tables'][0]['rows'][0]
        self.assertIsNone(row['verified_current_revenue_reported_units'])
        self.assertFalse(row['source_number_independently_verified'])

    def test_independent_cell_failure_cannot_keep_producer_values_verified(self):
        source,audit=self.inputs();audit['tables'][0]['status']='INDEPENDENT_CELL_REVIEW_REQUIRED'
        row=merge_company(source,audit)['tables'][0]['rows'][0]
        self.assertIsNone(row['verified_current_revenue_yuan'])
        self.assertEqual(row['reported_current_revenue'],'30.00')

    def test_unspecified_currency_is_not_silently_yuan(self):
        source,audit=self.inputs();source['tables'][0]['parsed']['unit']['currency']='CURRENCY_NOT_EXPLICIT_IN_UNIT_LABEL'
        row=merge_company(source,audit)['tables'][0]['rows'][0]
        self.assertEqual(row['verified_current_revenue_reported_units'],'30.00')
        self.assertIsNone(row['verified_current_revenue_yuan'])

    def test_missing_company_keeps_empty_source_and_unknown_exposure(self):
        source,audit=self.inputs();source.update(status='SOURCE_MISSING',source_pdf_path=None,source_pdf_sha256=None,report_metadata=None,tables=[]);audit['tables']=[]
        result=merge_company(source,audit)
        self.assertEqual(result['status'],'SOURCE_MISSING_NOT_ZERO')
        self.assertIsNone(result['finer_geography_exposure'])

    def test_other_income_is_not_a_geographic_region(self):
        self.assertEqual(component_kind('其他业务收入'),'UNALLOCATED_OTHER_REVENUE_NOT_A_REGION')
        self.assertEqual(component_kind('母公司合计'),'ENTITY_SCOPE_SUBTOTAL_OR_ELIMINATION')

    def test_duplicate_or_wrong_issuer_audit_association_is_rejected(self):
        source,audit=self.inputs();audit['stock_code']='OTHER'
        with self.assertRaises(ValueError):merge_company(source,audit)
        source,audit=self.inputs();audit['tables'].append(audit['tables'][0])
        with self.assertRaises(ValueError):merge_company(source,audit)


if __name__=='__main__':
    unittest.main()
