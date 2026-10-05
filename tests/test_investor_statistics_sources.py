"""Source and clock failures that must not become calibrated agent parameters."""

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from research.data_pipeline.investor_statistics_sources import (
    YEARBOOK_DECIMAL_GLYPH, parse_szse_survey, parse_yearbook_holdings,
    parse_yearbook_industry_holdings, publication_availability,
    reported_decimal, reported_interval,
)


HOLDINGS = """年末各类投资者持股情况
Share Hold of Investors by 2019
持股市值 (亿元) 持股账户数 (万户)
自然人投资者 10 10.00 90 90.00
其中: 10 万元以下 2 2.00 30 30.00
一般法人 60 60.00 1 1.00
沪股通 5 5.00 0.00 0.00
专业机构 25 25.00 9 9.00
其中: 投资基金 15 15.00 5 5.00
"""
INDUSTRY = """年末分行业持股信息
Hold Distribution by 2019
A 农业 10.00 10.00 30.00 30.00 60.00 60.00
B 采矿业 20.00 20.00 40.00 40.00 40.00 40.00
注: 持股市值单位为亿元。
"""


class InvestorStatisticsSourcesTests(unittest.TestCase):
    def test_private_glyph_requires_visual_review(self):
        with self.assertRaises(ValueError):
            reported_decimal("２０" + YEARBOOK_DECIMAL_GLYPH + " ５９")
        self.assertEqual(reported_decimal("２０" + YEARBOOK_DECIMAL_GLYPH + " ５９", glyph_visually_verified=True), Decimal("20.59"))

    def test_does_not_accept_float_bool_missing_or_foreign_glyph(self):
        for value in [20.59, True, None, "20\ue00059", "NaN", "-1", "1,000"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                reported_decimal(value)

    def test_reported_zero_has_a_nonzero_precision_interval(self):
        self.assertEqual(reported_interval("0.00"), (Decimal("0"), Decimal("0.01")))

    def test_holding_totals_and_subgroups_keep_different_roles(self):
        table = parse_yearbook_holdings(HOLDINGS, year=2019, glyph_visually_verified=False)
        self.assertEqual(sum(r["kind"] == "total" for r in table["rows"]), 4)
        self.assertEqual(sum(r["kind"] == "subgroup" for r in table["rows"]), 2)
        self.assertFalse(table["agent_role_mapping_identified"])
        self.assertEqual(table["rows"][3]["category"], "northbound_channel")
        self.assertFalse(table["rows"][3]["exact_zero_accounts_certified"])
        self.assertFalse(table["rows"][0]["cash_value_observed"])
        self.assertFalse(table["rows"][0]["tradable_inventory_observed"])

    def test_wrong_year_cannot_be_inferred_from_edition(self):
        with self.assertRaises(ValueError):
            parse_yearbook_holdings(HOLDINGS, year=2020, glyph_visually_verified=False)

    def test_unreported_units_rejected(self):
        with self.assertRaises(ValueError):
            parse_yearbook_holdings(HOLDINGS.replace("亿元", "元"), year=2019, glyph_visually_verified=False)

    def test_missing_northbound_category_rejected(self):
        with self.assertRaises(ValueError):
            parse_yearbook_holdings(HOLDINGS.replace("沪股通 5 5.00 0.00 0.00\n", ""), year=2019, glyph_visually_verified=False)

    def test_account_percentage_and_value_percentage_cannot_swap(self):
        wrong = HOLDINGS.replace("自然人投资者 10 10.00 90 90.00", "自然人投资者 10 90.00 90 10.00")
        with self.assertRaises(ValueError):
            parse_yearbook_holdings(wrong, year=2019, glyph_visually_verified=False)

    def test_industry_denominator_is_not_the_all_market_denominator(self):
        table = parse_yearbook_industry_holdings(INDUSTRY, year=2019, glyph_visually_verified=False)
        self.assertEqual(len(table["rows"]), 2)
        self.assertFalse(table["northbound_category_present"])
        self.assertFalse(table["all_market_denominator_equivalent"])

    def test_industry_percentages_must_agree_with_reported_amounts(self):
        with self.assertRaises(ValueError):
            parse_yearbook_industry_holdings(INDUSTRY.replace("10.00 10.00", "10.00 90.00"), year=2019, glyph_visually_verified=False)

    def test_duplicate_industry_rows_rejected(self):
        with self.assertRaises(ValueError):
            parse_yearbook_industry_holdings(INDUSTRY.replace("B 采矿业", "A 采矿业"), year=2019, glyph_visually_verified=False)

    def test_insufficient_precision_does_not_become_zero_or_divide_by_zero(self):
        tiny = INDUSTRY.replace("10.00 10.00 30.00 30.00 60.00 60.00", "0.01 100.00 0.00 0.00 0.00 0.00")
        with self.assertRaises(ValueError):
            parse_yearbook_industry_holdings(tiny, year=2019, glyph_visually_verified=False)

    def test_undated_yearbook_is_ineligible_even_after_observation_year(self):
        outcome = publication_availability({"publisher_date": None}, datetime(2022, 1, 1, tzinfo=timezone.utc), mode="declared_date_proxy")
        self.assertFalse(outcome["eligible"])
        self.assertEqual(outcome["reason"], "unknown_first_publication_time")

    def test_survey_published_2021_cannot_enter_2020_or_2021q1(self):
        for cutoff in [datetime(2020, 1, 20, tzinfo=timezone.utc), datetime(2021, 3, 31, tzinfo=timezone.utc)]:
            outcome = publication_availability({"publisher_date": "2021-05-17"}, cutoff, mode="declared_date_proxy")
            self.assertFalse(outcome["eligible"])
            self.assertEqual(outcome["reason"], "future_publisher_date")

    def test_same_day_requires_a_real_intraday_timestamp(self):
        outcome = publication_availability({"publisher_date": "2021-05-17"}, datetime(2021, 5, 17, 2, tzinfo=timezone.utc), mode="declared_date_proxy")
        self.assertEqual(outcome["reason"], "unknown_intraday_publication_time")

    def test_date_proxy_never_claims_point_in_time_certification(self):
        clock = {"publisher_date": "2021-05-17", "original_public_version_verified": False}
        cutoff = datetime(2022, 1, 1, tzinfo=timezone.utc)
        self.assertTrue(publication_availability(clock, cutoff, mode="declared_date_proxy")["eligible"])
        self.assertFalse(publication_availability(clock, cutoff, mode="declared_date_proxy")["point_in_time_certified"])
        self.assertFalse(publication_availability(clock, cutoff)["eligible"])

    def test_naive_cutoff_and_naive_publication_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            publication_availability({"publisher_date": "2021-05-17"}, datetime(2022, 1, 1))
        with self.assertRaises(ValueError):
            publication_availability({"first_publication_timestamp": "2021-05-17T09:00:00"}, datetime(2022, 1, 1, tzinfo=timezone.utc))

    def test_future_exact_timestamp_and_version_evidence_gate(self):
        clock = {"publisher_date": "2021-05-17", "first_publication_timestamp": "2021-05-17T10:00:00+08:00"}
        self.assertFalse(publication_availability(clock, datetime(2021, 5, 17, 1, tzinfo=timezone.utc))["eligible"])
        after = datetime(2022, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(publication_availability(clock, after)["reason"], "original_public_version_not_verified")
        clock["original_public_version_verified"] = True
        self.assertEqual(publication_availability(clock, after)["reason"], "missing_publication_evidence_bindings")
        clock["publication_evidence_bindings"] = {"historical_original": "e" * 64}
        self.assertFalse(publication_availability(clock, after)["point_in_time_certified"])
        self.assertTrue(publication_availability(clock, after, verified_publication_bindings={"historical_original": "e" * 64})["point_in_time_certified"])

    def test_asserted_or_changed_evidence_digest_never_certifies_publication(self):
        clock = {"publisher_date": "2021-05-17", "first_publication_timestamp": "2021-05-17T10:00:00+08:00",
                 "original_public_version_verified": True, "publication_evidence_bindings": {"source": "f" * 64}}
        after = datetime(2022, 1, 1, tzinfo=timezone.utc)
        self.assertFalse(publication_availability(clock, after, verified_publication_bindings={"source": "e" * 64})["eligible"])
        clock["publication_evidence_bindings"] = {"source": "not-a-digest"}
        self.assertEqual(publication_availability(clock, after)["reason"], "invalid_publication_evidence_bindings")

    def test_mirrored_survey_or_missing_body_cannot_be_accepted(self):
        with self.assertRaises(ValueError):
            parse_szse_survey({"metadata": {"statusCode": 200, "sourceURL": "https://example.com/survey", "title": "深交所发布2020年个人投资者状况调查报告"}})


if __name__ == "__main__":
    unittest.main()
