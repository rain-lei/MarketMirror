import copy
import unittest

from research.data_pipeline.monetary_operations import annotate_rate_changes, lagged_context, parse_bulletin

TITLE = "公开市场业务交易公告 [2019]第215号"


def page(body, timestamp="2019-11-05 09:46:16", footer="二〇一九年十一月五日"):
    return (f'<html><title>{TITLE}</title><meta name="createDate" content="2025-09-22">'
            f'<div>2026年10月1日</div><span id="shijian">{timestamp}</span><div id="zoom">'
            f'{body}<p>中国人民银行公开市场业务操作室</p><p>{footer}</p></div></html>').encode("utf-8")


def table(cells):
    return "<table><tr>" + "".join("<td>" + value + "</td>" for value in cells) + "</tr></table>"


def attach_source(record, value="a"):
    record["source"] = {"sha256": value * 64}
    return record


class MonetaryOperationsTest(unittest.TestCase):
    def test_historical_body_clock_overrides_current_navigation_and_migration_date(self):
        row = parse_bulletin(page("今日不开展逆回购操作。"), "2019-11-05", TITLE)
        self.assertEqual(row["publication_timestamp"], "2019-11-05T09:46:16+08:00")
        self.assertFalse(row["use_policy"]["agent_signal_enabled"])

    def test_explicit_no_reverse_repo_is_zero_but_unobserved_net_liquidity_is_null(self):
        row = parse_bulletin(page("今日不开展逆回购操作。"), "2019-11-05", TITLE)
        self.assertEqual(row["gross_reverse_repo_amount_100m_yuan"], 0.0)
        self.assertIsNone(row["net_liquidity_100m_yuan"])
        self.assertEqual(row["operations"], [])

    def test_mlf_and_repo_absence_do_not_create_a_repo_rate(self):
        body = "开展中期借贷便利（MLF）。今日不开展逆回购操作。" + table(["1年", "4000亿元", "3.25%"])
        row = parse_bulletin(page(body), "2019-11-05", TITLE)
        self.assertEqual(len(row["operations"]), 1)
        self.assertEqual((row["operations"][0]["instrument"], row["operations"][0]["rate_pct"]), ("mlf", 3.25))

    def test_reverse_repo_tender_quantity_and_rate_are_not_net_injection(self):
        row = parse_bulletin(page("逆回购操作情况" + table(["7天", "1800亿元", "2.50%"])), "2019-11-05", TITLE)
        self.assertEqual(row["gross_reverse_repo_amount_100m_yuan"], 1800.0)
        self.assertIsNone(row["net_liquidity_100m_yuan"])

    def test_hong_kong_bill_yield_does_not_become_mainland_liquidity(self):
        body = "通过香港金融管理局发行央行票据。" + table(["2019年第十期央行票据（香港）", "200亿元", "3个月（91天）", "2.90%"])
        row = parse_bulletin(page(body), "2019-11-05", TITLE)
        operation = row["operations"][0]
        self.assertEqual((operation["instrument"], operation["rate_kind"], operation["market"]), ("hk_central_bank_bill", "bill_yield", "offshore_hk"))
        self.assertEqual(operation["gross_amount_100m_yuan"], 200)
        self.assertIsNone(row["gross_reverse_repo_amount_100m_yuan"])

    def test_cbs_fee_is_distinct_from_policy_tender_interest(self):
        body = "开展央行票据互换（CBS），操作量为60亿元，期限为3个月，费率为0.10%。"
        row = parse_bulletin(page(body), "2019-11-05", TITLE)
        self.assertEqual(row["operations"][0]["rate_kind"], "fee")
        self.assertIsNone(row["gross_reverse_repo_amount_100m_yuan"])

    def test_unknown_or_incomplete_announced_operation_is_rejected(self):
        for body in ["流动性新闻，没有操作声明。", "逆回购操作情况" + table(["7天", "1800亿元", "未知"]),
                     "中期借贷便利（MLF）" + table(["1年", "未知", "3.25%"])]:
            with self.assertRaises(ValueError):
                parse_bulletin(page(body), "2019-11-05", TITLE)

    def test_contradictory_repo_absence_is_rejected(self):
        body = "不开展逆回购操作。逆回购操作情况" + table(["7天", "1800亿元", "2.50%"])
        with self.assertRaisesRegex(ValueError, "contradictory"):
            parse_bulletin(page(body), "2019-11-05", TITLE)

    def test_missing_day_time_or_signature_date_cannot_invent_a_publication_clock(self):
        for raw in [page("不开展逆回购操作。", timestamp="2019-11-05"),
                    page("不开展逆回购操作。", footer="二〇一九年十一月六日"),
                    page("不开展逆回购操作。").replace(b'id="shijian"', b'id="not-the-clock"')]:
            with self.assertRaises(ValueError):
                parse_bulletin(raw, "2019-11-05", TITLE)

    def test_observed_rate_changes_require_a_previous_same_instrument_and_tenor(self):
        earlier = attach_source(parse_bulletin(page("逆回购操作情况" + table(["7天", "100亿元", "2.55%"])), "2019-11-05", TITLE))
        later = attach_source(parse_bulletin(page("逆回购操作情况" + table(["7天", "1800亿元", "2.50%"]), "2019-11-18 09:46:10", "二〇一九年十一月十八日"), "2019-11-18", TITLE), "b")
        other = copy.deepcopy(later)
        other["operations"][0]["tenor_value"] = 14
        annotate_rate_changes([earlier, later, other])
        self.assertIsNone(earlier["operations"][0]["change_from_previous_observed_bps"])
        self.assertEqual(later["operations"][0]["change_from_previous_observed_bps"], -5)
        self.assertIsNone(other["operations"][0]["change_from_previous_observed_bps"])

    def test_future_policy_is_censored_even_when_future_rate_and_amount_are_poisoned(self):
        record = attach_source(parse_bulletin(page("逆回购操作情况" + table(["7天", "1800亿元", "2.50%"])), "2019-11-05", TITLE))
        annotate_rate_changes([record])
        calendar = [{"trade_date": "2019-11-06", "signal_cutoff_date": "2019-11-04"},
                    {"trade_date": "2019-11-07", "signal_cutoff_date": "2019-11-05"}]
        actual = lagged_context([record], calendar)
        poison = copy.deepcopy(record)
        poison["operations"][0].update(rate_pct=99, gross_amount_100m_yuan=999999)
        self.assertEqual(actual[0], lagged_context([poison], calendar)[0])
        self.assertEqual(actual[0]["latest_observed_instrument_rates"], {})
        self.assertIsNone(actual[0]["cutoff_date_gross_reverse_repo_100m_yuan"])
        self.assertEqual(actual[1]["latest_observed_instrument_rates"]["reverse_repo:7天"]["rate_pct"], 2.5)


if __name__ == "__main__":
    unittest.main()
