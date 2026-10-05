import unittest

from research.data_pipeline.business_exposure_evidence import heading_kind, locate_evidence


TERMS = ["分地区", "分区域", "地区分部", "营业收入构成", "主营业务构成", "湖北", "武汉", "受限", "所有权或使用权"]


class BusinessExposureEvidenceTest(unittest.TestCase):
    def test_formal_headings_and_continuation_are_evidence_candidates(self):
        self.assertEqual(heading_kind("一、报告期内公司从事的主要业务"), "MAIN_BUSINESS_HEADING")
        self.assertEqual(heading_kind("（3）主营业务分地区情况"), "GEOGRAPHIC_REVENUE_OR_SEGMENT_HEADING")
        self.assertEqual(heading_kind("地区分部（续）"), "GEOGRAPHIC_REVENUE_OR_SEGMENT_HEADING")
        self.assertEqual(heading_kind("51、所有权或使用权受到限制的资产"), "ASSET_RESTRICTION_HEADING")

    def test_reference_sentences_and_contents_page_are_not_table_headings(self):
        for text in ("营业收支的地区分部情况详见财务报告", "主营业务分地区情况........16", "武汉地区存在销售业务"):
            with self.subTest(text=text):
                self.assertIsNone(heading_kind(text))

    def test_registered_address_and_subsidiary_mention_do_not_assign_exposure(self):
        result = locate_evidence(["注册地址：武汉\n湖北子公司\n主营业务构成情况\n单位：元\n分地区\n华中\n100.00"], TERMS)
        self.assertEqual(len(result["region_literal_contexts"]), 2)
        self.assertEqual(result["literal_term_pages"]["武汉"], [1])
        self.assertIsNone(result["quantified_exposure"])
        self.assertFalse(result["business_exposure_values_verified"])
        for row in result["region_literal_contexts"]:
            self.assertFalse(row["economic_relationship_verified"])
            self.assertIsNone(row["quantified_exposure"])
            self.assertEqual(row["shock_direction"], "unknown")

    def test_absence_is_unknown_and_not_zero_exposure(self):
        result = locate_evidence(["普通财务信息"], TERMS)
        self.assertEqual(result["review_queue_status"], "NO_LITERAL_EVIDENCE_CANDIDATE")
        self.assertIsNone(result["quantified_exposure"])
        self.assertEqual(result["literal_term_pages"]["湖北"], [])
        self.assertFalse(result["agent_signal_enabled"])

    def test_source_context_and_page_boundaries_are_preserved(self):
        result = locate_evidence(["单位：元\n分地区\n境内\n20.00", "分地区\n境外\n10.00"], TERMS)
        self.assertEqual(result["literal_term_pages"]["分地区"], [1, 2])
        first, second = result["heading_candidates"]
        self.assertEqual(first["pdf_page"], 1)
        self.assertEqual(first["line_number"], 2)
        self.assertEqual(first["context_lines"], ["单位：元", "分地区", "境内", "20.00"])
        self.assertNotIn("境外", first["context_lines"])
        self.assertIsNone(second["quantified_exposure"])
        self.assertFalse(second["value_column_verified"])

    def test_split_literal_and_bad_input_are_handled_explicitly(self):
        self.assertEqual(locate_evidence(["湖 北\n分 地 区"], TERMS)["literal_term_pages"]["湖北"], [1])
        for pages, terms in (([], TERMS), ([None], TERMS), (["page"], ["湖北", "湖北"])):
            with self.subTest(pages=pages, terms=terms):
                with self.assertRaisesRegex(ValueError, "unique fixed"):
                    locate_evidence(pages, terms)


if __name__ == "__main__":
    unittest.main()
