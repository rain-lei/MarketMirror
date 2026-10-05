"""Official disclosure identity, availability and selection failures."""

import copy
import unittest
from datetime import datetime

from research.data_pipeline.issuer_disclosures import CHINA, identity_match, inventory_company, report_candidate


class IssuerDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"snapshot_at": "2020-01-22T23:59:59+08:00", "query_start_date": "2019-01-01",
                         "query_end_date": "2020-01-22", "attachment_host": "static.cninfo.com.cn",
                         "report_periods": {"2018_annual": {"title_token": "2018年年度报告", "report_end_date": "2018-12-31"},
                                            "2019_half": {"title_token": "2019年半年度报告", "report_end_date": "2019-06-30"},
                                            "2019_q3": {"title_token": "2019年第三季度报告", "report_end_date": "2019-09-30"}}}
        self.identity_rows = [{"code": "000001", "orgId": "gssz0000001", "category": "A股", "zwjc": "某银行"}]
        self.identity = identity_match(self.identity_rows, "000001")
        self.cohort = {"stock_code": "000001", "historical_short_name": "某银行", "industry_code": "J66",
                       "available_at_proxy": "2019-10-29T00:00:00+08:00"}

    def row(self, title="2019年第三季度报告全文", day="2019-10-22", identifier="1206997843"):
        return {"secCode": "000001", "orgId": "gssz0000001", "announcementId": identifier,
                "announcementTitle": title, "announcementTime": int(datetime.fromisoformat(day).replace(tzinfo=CHINA).timestamp()*1000),
                "adjunctUrl": f"finalpage/{day}/{identifier}.PDF"}

    def inventory(self, rows):
        return inventory_company(self.cohort, self.identity_rows,
                                 [{"totalAnnouncement": len(rows), "announcements": rows, "hasMore": False}], self.settings)

    def test_fund_with_same_code_is_not_stock_identity(self):
        fund = {"code": "000001", "orgId": "fund", "category": "基金"}
        self.assertEqual(identity_match([fund]+self.identity_rows,"000001"),self.identity)
        with self.assertRaises(ValueError): identity_match([fund],"000001")

    def test_duplicate_stock_identity_is_not_silently_chosen(self):
        with self.assertRaises(ValueError): identity_match(self.identity_rows*2,"000001")

    def test_date_only_is_available_next_midnight(self):
        actual=report_candidate(self.row(),self.identity,self.settings)
        self.assertEqual(actual["available_at_proxy"],"2019-10-23T00:00:00+08:00")
        self.assertEqual(actual["publication_precision_used"],"date")
        self.assertFalse(actual["financial_values_verified"])

    def test_snapshot_day_report_is_not_available(self):
        actual=report_candidate(self.row(day="2020-01-22"),self.identity,self.settings)
        self.assertFalse(actual["eligible"])
        self.assertEqual(actual["eligibility_reason"],"not_available_by_snapshot")

    def test_archive_date_must_agree_with_provider_date(self):
        row=self.row();row["adjunctUrl"]="finalpage/2019-10-23/1206997843.PDF"
        with self.assertRaises(ValueError):report_candidate(row,self.identity,self.settings)

    def test_cross_company_and_mismatched_attachment_are_rejected(self):
        for key,value in [("secCode","000002"),("orgId","other"),("adjunctUrl","finalpage/2019-10-22/999.PDF"),
                          ("adjunctUrl","https://attacker.invalid/file.PDF"),("announcementTime",True)]:
            row=self.row();row[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):report_candidate(row,self.identity,self.settings)

    def test_full_report_preferred_to_body_and_summary(self):
        rows=[self.row(identifier="1"),self.row(title="2019年第三季度报告正文",identifier="2"),
              self.row(title="2019年第三季度报告摘要",identifier="3")]
        selected=self.inventory(rows)["reports"]["2019_q3"]
        self.assertEqual(selected["selected"]["announcement_id"],"1")
        self.assertEqual(selected["status"],"AVAILABLE_METADATA_ONLY")

    def test_equal_day_competing_full_reports_are_ambiguous(self):
        report=self.inventory([self.row(identifier="1"),self.row(identifier="2")])["reports"]["2019_q3"]
        self.assertEqual(report["status"],"AMBIGUOUS_REPORT_METADATA")
        self.assertIsNone(report["selected"])

    def test_later_eligible_revision_is_retained_and_selected(self):
        rows=[self.row(identifier="1"),self.row(title="2019年第三季度报告全文（修订版）",day="2019-11-01",identifier="2")]
        report=self.inventory(rows)["reports"]["2019_q3"]["selected"]
        self.assertEqual(report["announcement_id"],"2")
        self.assertTrue(report["revision_marked_in_title"])

    def test_future_revision_does_not_replace_eligible_original(self):
        rows=[self.row(identifier="1"),self.row(title="2019年第三季度报告全文（更新后）",day="2020-02-01",identifier="2")]
        self.assertEqual(self.inventory(rows)["reports"]["2019_q3"]["selected"]["announcement_id"],"1")

    def test_exact_duplicate_rows_are_preserved_in_raw_count(self):
        row=self.row();result=self.inventory([row,copy.deepcopy(row)])
        self.assertEqual(result["raw_announcement_rows"],2)
        self.assertEqual(result["unique_announcement_rows"],1)

    def test_annual_future_year_and_ancillary_notice_are_not_reports(self):
        for title in ("2019年年度报告", "关于2019年第三季度报告的提示性公告", "2019年第三季度报告更正公告", "2019年半年度报告（英文版）"):
            with self.subTest(title=title):
                self.assertFalse(report_candidate(self.row(title=title),self.identity,self.settings)["eligible"])

    def test_incomplete_pagination_is_rejected(self):
        for page in ({"totalAnnouncement":2,"announcements":[self.row()],"hasMore":False},
                     {"totalAnnouncement":1,"announcements":[self.row()],"hasMore":True}):
            with self.subTest(page=page),self.assertRaises(ValueError):
                inventory_company(self.cohort,self.identity_rows,[page],self.settings)

    def test_empty_query_records_explicit_missing_periods(self):
        reports=self.inventory([])["reports"]
        self.assertEqual(set(item["status"] for item in reports.values()),{"MISSING_IN_CURRENT_CATALOG"})
        self.assertTrue(all(item["selected"] is None for item in reports.values()))


if __name__ == "__main__":
    unittest.main()
