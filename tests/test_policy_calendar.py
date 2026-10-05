"""Policy source semantics and visibility must survive timing counterfactuals."""

import copy
import unittest

from research.data_pipeline.monetary_operations import annotate_rate_changes
from research.data_pipeline.policy_calendar import build_calendar, parse_lpr, parse_rrr, parse_rrr_legal_text


LPR_TITLE = "2019年11月20日全国银行间同业拆借中心受权公布贷款市场报价利率（LPR）公告"
LPR_BODY = "中国人民银行授权全国银行间同业拆借中心公布，2019年11月20日贷款市场报价利率（LPR）为：1年期LPR为4.15%，5年期以上LPR为4.8%。以上LPR在下一次发布LPR之前有效。"
RRR_TITLE = "中国人民银行决定于2019年9月16日下调金融机构存款准备金率"
RRR_BODY = "中国人民银行决定于2019年9月16日全面下调金融机构存款准备金率0.5个百分点（不含财务公司、金融租赁公司和汽车金融公司）。再额外对仅在省级行政区域内经营的城市商业银行定向下调存款准备金率1个百分点，于10月15日和11月15日分两次实施到位，每次下调0.5个百分点。"


def lpr_html(body=LPR_BODY, clock="2019-11-20 09:30", extra=""):
    return (f"<title>{LPR_TITLE}_中国货币网</title><script>now='2026-10-01 12:00';</script>"
            f'<div class="title-heading">{LPR_TITLE}</div><div class="article-a-toolbar"><span>{clock}</span>{extra}</div>'
            f'<div id="ewebeditor_content"><div>{body}</div></div>').encode()


def rrr_html(body=RRR_BODY, attribution="中国人民银行网站"):
    return (f"<title>{RRR_TITLE}_中共广东省委金融委员会办公室</title>"
            f'<li class="c_time">发布时间：2019-09-06 23:07</li><span id="ly">{attribution}</span>'
            f'<div id="zoom"><p>{body}</p></div>').encode()


def rate_record(identity, day, rate, previous_kind="mlf", tenor=1, unit="年", market="mainland"):
    return {"source_id": identity, "kind": "omo", "title": identity, "signed_date": day,
            "publication_timestamp": day + "T09:46:00+08:00", "available_at": day + "T09:46:00+08:00",
            "source": {"sha256": identity}, "policy_measures": [],
            "gross_reverse_repo_amount_100m_yuan": None,
            "operations": [{"instrument": previous_kind, "tenor_value": tenor, "tenor_unit": unit,
                            "rate_pct": rate, "rate_kind": "tender_interest", "market": market}]}


def clocks():
    return [{"trade_date": "2019-11-19", "signal_cutoff_date": "2019-11-15"},
            {"trade_date": "2019-11-20", "signal_cutoff_date": "2019-11-18"},
            {"trade_date": "2019-11-21", "signal_cutoff_date": "2019-11-19"}]


class PolicySourceTests(unittest.TestCase):
    def test_lpr_two_distinct_tenors_and_minute_upper_bound(self):
        row = parse_lpr(lpr_html(), "2019-11-20", LPR_TITLE)
        self.assertEqual(row["available_at"], "2019-11-20T09:30:59+08:00")
        self.assertEqual([(o["tenor_value"], o["tenor_unit"], o["rate_pct"]) for o in row["operations"]], [(1, "年", 4.15), (5, "年以上", 4.8)])
        self.assertIsNone(row["gross_reverse_repo_amount_100m_yuan"])
        self.assertIsNone(row["net_liquidity_100m_yuan"])

    def test_lpr_body_date_cannot_replace_page_date(self):
        with self.assertRaisesRegex(ValueError, "two supported rates"):
            parse_lpr(lpr_html(body=LPR_BODY.replace("11月20日", "11月21日")), "2019-11-20", LPR_TITLE)

    def test_lpr_partial_tenor_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_lpr(lpr_html(body=LPR_BODY.replace("，5年期以上LPR为4.8%", "")), "2019-11-20", LPR_TITLE)

    def test_ambiguous_article_clock_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "one explicit"):
            parse_lpr(lpr_html(extra="<span>2019-11-20 10:30</span>"), "2019-11-20", LPR_TITLE)

    def test_unknown_precision_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_lpr(lpr_html(clock="2019-11-20"), "2019-11-20", LPR_TITLE)

    def test_rrr_announcement_and_three_phases_are_separate(self):
        row = parse_rrr(rrr_html(), "2019-09-06", RRR_TITLE)
        self.assertEqual(row["available_at"], "2019-09-06T23:07:59+08:00")
        self.assertEqual([m["effective_date"] for m in row["policy_measures"]], ["2019-09-16", "2019-10-15", "2019-11-15"])
        self.assertEqual([m["change_percentage_points"] for m in row["policy_measures"]], [-0.5] * 3)
        self.assertEqual(row["policy_measures"][1]["scope_key"], "province_only_city_commercial_banks")
        self.assertEqual(set(row["policy_measures"][0]["excluded_institutions"]), {"财务公司", "金融租赁公司", "汽车金融公司"})
        self.assertTrue(all(m["equity_direction"] is None and m["issuer_exposure"] == "unmapped" for m in row["policy_measures"]))

    def test_rrr_targeted_total_cannot_be_applied_per_phase(self):
        with self.assertRaisesRegex(ValueError, "targeted total"):
            parse_rrr(rrr_html(body=RRR_BODY.replace("每次下调0.5", "每次下调1")), "2019-09-06", RRR_TITLE)

    def test_rrr_source_attribution_is_required(self):
        with self.assertRaisesRegex(ValueError, "attributed"):
            parse_rrr(rrr_html(attribution="其他网站"), "2019-09-06", RRR_TITLE)

    def test_rrr_unknown_scope_cannot_become_all_banks(self):
        with self.assertRaisesRegex(ValueError, "scope"):
            parse_rrr(rrr_html(body=RRR_BODY.replace("仅在省级行政区域内经营的城市商业银行", "全部银行")), "2019-09-06", RRR_TITLE)

    def test_pdf_decimal_requires_explicit_visual_alias(self):
        pages = ["银发(2019)223号 下调金融机构存款准备金率的通知 自2019年9月16日起,下调金融机构人民币存款准备",
                 "‘·电金率O.5个百分点。财务公司、汽车金融公司、金融租赁公司存款准备金率保持不变。在该行政区域外没有设立分支机构的城市商业银行。自2019年10月15日起,下调仅在本省级行政区域内经营的城市商业银行人民币存款准备金率O.5个百分点。自2019年11月15日起,下调仅在本省级行政区域内经营的城市商业银行人民币存款准备金率0.5个百分点。", "第三页", "中国人民银行办公厅2019年9月10日印发"]
        with self.assertRaisesRegex(ValueError, "visual source review"):
            parse_rrr_legal_text(pages, {})
        row = parse_rrr_legal_text(pages, {"O.5": "0.5"})
        self.assertEqual(len(row["decimal_ocr_normalizations"]), 2)
        self.assertIsNone(row["publication_timestamp"])
        self.assertEqual(row["office_imprint_date"], "2019-09-10")


class PolicyTimingTests(unittest.TestCase):
    def test_inherited_state_is_not_an_initial_shock(self):
        records = [rate_record("earlier", "2019-10-16", 3.30), rate_record("cut", "2019-11-05", 3.25)]
        annotate_rate_changes(records)
        row = build_calendar(records, clocks())[0]
        self.assertEqual(row["baseline_source_ids"], ["earlier", "cut"])
        self.assertEqual(row["new_observed_rate_changes"], [])
        self.assertEqual(row["latest_observed_instrument_rates"]["mlf:1年"]["rate_pct"], 3.25)

    def test_new_change_is_not_reapplied_on_following_days(self):
        records = [rate_record("earlier", "2019-10-25", 2.55, "reverse_repo", 7, "天"),
                   rate_record("cut", "2019-11-18", 2.50, "reverse_repo", 7, "天")]
        annotate_rate_changes(records)
        rows = build_calendar(records, clocks())
        self.assertEqual([len(row["new_observed_rate_changes"]) for row in rows], [0, 1, 0])
        self.assertEqual(rows[1]["new_observed_rate_changes"][0]["change_from_previous_observed_bps"], -5.0)

    def test_no_predecessor_is_not_a_zero_change_event(self):
        records = [rate_record("first14", "2019-11-18", 2.65, "reverse_repo", 14, "天")]
        annotate_rate_changes(records)
        rows = build_calendar(records, clocks())
        self.assertIsNone(rows[1]["latest_observed_instrument_rates"]["reverse_repo:14天"]["change_from_previous_observed_bps"])
        self.assertEqual(rows[1]["new_observed_rate_changes"], [])
        self.assertEqual(rows[1]["newly_visible_source_ids"], ["first14"])

    def test_future_record_cannot_change_an_earlier_context(self):
        records = [rate_record("prior", "2019-10-16", 3.3)]
        annotate_rate_changes(records)
        reference = build_calendar(records, clocks())
        poisoned = copy.deepcopy(records) + [rate_record("future", "2019-12-20", 999.0)]
        annotate_rate_changes(poisoned)
        self.assertEqual(reference, build_calendar(poisoned, clocks()))

    def test_rrr_implementation_is_anticipated_not_new_information(self):
        record = parse_rrr(rrr_html(), "2019-09-06", RRR_TITLE)
        record.update({"source_id": "rrr", "source": {"sha256": "rrr"}})
        rows = build_calendar([record], [{"trade_date": "2019-11-18", "signal_cutoff_date": "2019-11-14"},
                                         {"trade_date": "2019-11-19", "signal_cutoff_date": "2019-11-15"},
                                         {"trade_date": "2019-11-20", "signal_cutoff_date": "2019-11-18"}])
        self.assertEqual(rows[0]["known_rrr_schedule"][2]["status"], "scheduled_after_cutoff")
        self.assertEqual(rows[1]["newly_effective_known_rrr_measure_ids"], ["rrr:targeted_phase_2"])
        self.assertEqual(rows[2]["newly_effective_known_rrr_measure_ids"], [])
        self.assertTrue(all(row["newly_visible_source_ids"] == [] and row["new_observed_rate_changes"] == [] for row in rows))

    def test_future_rrr_announcement_has_no_earlier_schedule(self):
        record = parse_rrr(rrr_html(), "2019-09-06", RRR_TITLE)
        record.update({"source_id": "rrr", "source": {"sha256": "rrr"}, "available_at": "2019-12-20T09:30:59+08:00"})
        self.assertTrue(all(row["known_rrr_schedule"] == [] for row in build_calendar([record], clocks())))

    def test_duplicate_source_and_unordered_calendar_are_rejected(self):
        record = rate_record("prior", "2019-10-16", 3.3)
        annotate_rate_changes([record])
        with self.assertRaisesRegex(ValueError, "duplicated"):
            build_calendar([record, record], clocks())
        with self.assertRaisesRegex(ValueError, "unordered"):
            build_calendar([record], list(reversed(clocks())))


if __name__ == "__main__":
    unittest.main()
