from __future__ import annotations

import copy
import json
import unittest
from datetime import date, timedelta

from research.data_pipeline.fetch_extended_policy_rates import index_records, official_url
from research.data_pipeline.policy_calendar import build_calendar
from research.data_pipeline.policy_rate_panel import (
    diagnostic_rows, error_metrics, fit_diagnostic, fraction_rank,
    make_clocks, parse_rate_bulletin, policy_vectors,
)

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def bulletin(body: str, year="2020", footer="二〇二〇年二月三日", day="2020-02-03") -> bytes:
    return (f'<html><head><title>公开市场业务交易公告 [{year}]第18号</title></head>'
            f'<body><span id="shijian">{day} 09:30:00</span><div id="zoom">{body}'
            f'<p>中国人民银行公开市场业务操作室</p><p>{footer}</p></div></body></html>').encode("utf-8")


def tender(term="7天", amount="1200亿元", rate="2.55%") -> str:
    return f'<table><tr><td>{term}</td><td>{amount}</td><td>{rate}</td></tr></table>'


def rate_source(identity: str, stamp: str, instrument="reverse_repo", tenor=7, unit="天", delta=-5.0, market="mainland") -> dict:
    return {"source_id": identity, "title": identity, "available_at": stamp,
            "publication_timestamp": stamp, "signed_date": stamp[:10], "source": {"sha256": identity},
            "gross_reverse_repo_amount_100m_yuan": None, "operations": [{"instrument": instrument,
            "tenor_value": tenor, "tenor_unit": unit, "change_from_previous_observed_bps": delta,
            "rate_pct": 2.5, "market": market, "rate_kind": "tender_interest", "previous_observation": None}]}


class CrossYearSourceTests(unittest.TestCase):
    def test_2020_year_retained_and_table_rate_parsed(self):
        parsed = parse_rate_bulletin(bulletin("逆回购操作情况" + tender()), "2020-02-03", "公开市场业务交易公告 [2020]第18号")
        self.assertEqual(parsed["operations"][0]["rate_pct"], 2.55)
        self.assertEqual(parsed["publication_timestamp"], "2020-02-03T09:30:00+08:00")

    def test_bulletin_year_cannot_disagree_with_page(self):
        with self.assertRaisesRegex(ValueError, "title/year"):
            parse_rate_bulletin(bulletin("今日不开展逆回购操作。", year="2019"), "2020-02-03", "公开市场业务交易公告 [2019]第18号")

    def test_mlf_maturity_mention_does_not_create_mlf_operation(self):
        parsed = parse_rate_bulletin(bulletin("为对冲MLF到期，开展逆回购。逆回购操作情况" + tender()), "2020-02-03", "公开市场业务交易公告 [2020]第18号")
        self.assertEqual([row["instrument"] for row in parsed["operations"]], ["reverse_repo"])

    def test_tmlf_keeps_separate_rate_history_and_explicit_no_repo(self):
        parsed = parse_rate_bulletin(bulletin("定向中期借贷便利（TMLF）进行了续做。今日无逆回购操作。TMLF操作情况" + tender("1年", "2405亿元", "3.15%")), "2020-02-03", "公开市场业务交易公告 [2020]第18号")
        self.assertEqual(parsed["operations"][0]["instrument"], "tmlf")
        self.assertTrue(parsed["explicit_no_reverse_repo"])
        self.assertEqual(parsed["gross_reverse_repo_amount_100m_yuan"], 0.0)

    def test_mixed_mlf_tmlf_does_not_guess_table_scope(self):
        with self.assertRaisesRegex(ValueError, "section-aware"):
            parse_rate_bulletin(bulletin("MLF操作情况 TMLF操作情况" + tender("1年")), "2020-02-03", "公开市场业务交易公告 [2020]第18号")

    def test_bulletin_date_contradiction_rejected(self):
        with self.assertRaisesRegex(ValueError, "signed date"):
            parse_rate_bulletin(bulletin("今日不开展逆回购操作。", footer="二〇二〇年二月四日"), "2020-02-03", "公开市场业务交易公告 [2020]第18号")

    def test_malformed_rate_is_not_a_zero_rate(self):
        with self.assertRaisesRegex(ValueError, "omitted"):
            parse_rate_bulletin(bulletin("逆回购操作情况" + tender(rate="未知")), "2020-02-03", "公开市场业务交易公告 [2020]第18号")

    def test_cross_year_same_bulletin_number_has_distinct_identity(self):
        raw = ('<a href="/a">公开市场业务交易公告 [2019]第1号</a><span>2019-01-02</span>'
               '<a href="/b">公开市场业务交易公告 [2020]第1号</a><span>2020-01-02</span>')
        records = index_records(raw, "https://www.pbc.gov.cn/index.html", "2019-01-01", "2020-01-31")
        self.assertEqual({row["source_id"] for row in records}, {"pbc_2019_1", "pbc_2020_1"})

    def test_adjacent_date_cannot_be_borrowed_from_next_link(self):
        raw = ('<a href="/a">公开市场业务交易公告 [2019]第1号</a>'
               '<a href="/b">公开市场业务交易公告 [2020]第1号</a><span>2020-01-02</span>')
        with self.assertRaisesRegex(ValueError, "adjacent"):
            index_records(raw, "https://www.pbc.gov.cn/index.html", "2019-01-01", "2020-01-31")

    def test_publisher_lookalike_and_http_not_accepted(self):
        self.assertFalse(official_url("https://www.pbc.gov.cn.example.com/a"))
        self.assertFalse(official_url("http://www.pbc.gov.cn/a"))
        self.assertFalse(official_url("https://user:password@www.pbc.gov.cn/a"))


class ClockAndMissingnessTests(unittest.TestCase):
    def setUp(self):
        self.days = ["2020-01-21", "2020-01-22", "2020-01-23", "2020-02-03", "2020-02-04", "2020-02-05"]
        self.clocks = make_clocks(self.days, "2020-01-23", "2020-02-05")
        self.channels = [("reverse_repo", 7, "天")]

    def test_holiday_gap_uses_exchange_sessions_not_calendar_subtraction(self):
        self.assertEqual(self.clocks[1], {"trade_date": "2020-02-03", "signal_cutoff_date": "2020-01-22", "execution_reference_date": "2020-01-23"})

    def test_duplicate_exchange_day_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicated"):
            make_clocks(self.days + [self.days[-1]], "2020-01-23", "2020-02-05")

    def test_unknown_first_observation_remains_null(self):
        sources = [rate_source("unknown", "2020-01-22T09:30:00+08:00", delta=None)]
        calendar = build_calendar(sources, self.clocks)
        panel = policy_vectors(sources, calendar, self.channels)
        self.assertEqual(panel[1]["change_bps_by_channel"], [None])
        self.assertEqual(panel[1]["unknown_predecessors"][0]["source_id"], "unknown")

    def test_first_inherited_rate_is_not_replayed_and_fresh_cut_is_once(self):
        sources = [rate_source("initial", "2020-01-20T09:30:00+08:00"), rate_source("cut", "2020-01-22T09:30:00+08:00")]
        calendar = build_calendar(sources, self.clocks)
        panel = policy_vectors(sources, calendar, self.channels)
        self.assertEqual([row["change_bps_by_channel"][0] for row in panel], [0.0, -5.0, 0.0, 0.0])

    def test_tmlf_and_offshore_changes_excluded_from_mainland_channels(self):
        sources = [rate_source("tmlf", "2020-01-22T09:30:00+08:00", instrument="tmlf", tenor=1, unit="年"),
                   rate_source("hk", "2020-01-22T09:31:00+08:00", market="offshore_hk")]
        panel = policy_vectors(sources, build_calendar(sources, self.clocks), self.channels)
        self.assertTrue(all(row["change_bps_by_channel"] == [0.0] for row in panel))

    def test_forged_future_source_is_rejected(self):
        source = rate_source("future", "2020-02-06T09:30:00+08:00")
        calendar = build_calendar([source], self.clocks)
        calendar[0]["newly_visible_source_ids"] = ["future"]
        with self.assertRaisesRegex(ValueError, "future"):
            policy_vectors([source], calendar, self.channels)

    def test_target_and_execution_price_poison_does_not_change_input_features(self):
        days = [(date(2019, 6, 1) + timedelta(days=i)).isoformat() for i in range(30)]
        closes = {day: 100 + i + (i % 3) * .1 for i, day in enumerate(days)}
        clocks = make_clocks(days, days[-1], days[-1])
        panel = [{"trade_date": days[-1], "change_bps_by_channel": [0.0]}]
        before = diagnostic_rows(days, closes, clocks, panel)[0]
        poisoned = dict(closes);poisoned[days[-1]] *= 100;poisoned[days[-2]] *= 50
        after = diagnostic_rows(days, poisoned, clocks, panel)[0]
        self.assertEqual(before["baseline_features"], after["baseline_features"])
        self.assertNotEqual(before["target_return"], after["target_return"])

    def test_nonlagged_clock_cannot_use_target_price(self):
        days = [(date(2019, 6, 1) + timedelta(days=i)).isoformat() for i in range(30)]
        clocks = make_clocks(days, days[-1], days[-1]);clocks[0]["signal_cutoff_date"] = days[-1]
        with self.assertRaisesRegex(ValueError, "prior two"):
            diagnostic_rows(days, {day: 100 + i for i, day in enumerate(days)}, clocks, [{"trade_date": days[-1], "change_bps_by_channel": [0.0]}])


@unittest.skipUnless(HAS_NUMPY, "NumPy required; bundled research Python provides it")
class TrainingOnlyFitTests(unittest.TestCase):
    def fixture(self):
        result = []
        for index in range(26):
            x = (index + 1) / 40
            features = [1.0, x, x * x, x * x * x]
            policy = [0.0] * 5
            if index in (2, 4, 6, 8, 10):
                policy[(index - 2) // 2] = -5.0
            if index == 21:
                policy[0] = -10.0
            target = .02 + .01 * x - .002 * x * x + .001 * x * x * x + .0001 * sum(policy)
            result.append({"trade_date": (date(2019, 9, 1) + timedelta(days=index)).isoformat(),
                           "baseline_features": features, "policy_features": policy, "target_return": target,
                           "target_absolute_return": abs(target), "paired_exclusion_reason": None})
        return result

    def test_future_outcomes_cannot_change_training_coefficients(self):
        rows = self.fixture()
        before = fit_diagnostic(rows, rows[19]["trade_date"], rows[20]["trade_date"])
        poisoned = copy.deepcopy(rows)
        for row in poisoned[20:]:
            row["target_return"] += 1000;row["target_absolute_return"] += 1000
        after = fit_diagnostic(poisoned, rows[19]["trade_date"], rows[20]["trade_date"])
        for target in before["targets"]:
            for name in before["targets"][target]["models"]:
                self.assertEqual(before["targets"][target]["models"][name]["coefficients"], after["targets"][target]["models"][name]["coefficients"])
        self.assertNotEqual(before["targets"]["target_return"]["models"]["market_state"]["metrics"], after["targets"]["target_return"]["models"]["market_state"]["metrics"])

    def test_rank_deficiency_produces_serializable_no_fit_result(self):
        rows = self.fixture()
        for row in rows:
            row["policy_features"] = [0.0] * 5
        result = fit_diagnostic(rows, rows[19]["trade_date"], rows[20]["trade_date"])
        self.assertFalse(result["coefficients_fitted"])
        self.assertEqual(result["targets"], {})
        json.dumps(result, allow_nan=False)

    def test_unknown_policy_row_excluded_from_both_models(self):
        rows = self.fixture();rows[19]["policy_features"][0] = None
        rows[19]["paired_exclusion_reason"] = "unknown_new_policy_predecessor"
        rows[19]["target_return"] = 1000;rows[19]["target_absolute_return"] = 1000
        result = fit_diagnostic(rows, rows[19]["trade_date"], rows[20]["trade_date"])
        self.assertEqual(result["training_rows"], 19)
        self.assertEqual(result["training_designs"]["market_state"]["training_rows"], result["training_designs"]["market_state_plus_rates"]["training_rows"])
        self.assertEqual(result["excluded_rows"][0]["trade_date"], rows[19]["trade_date"])

    def test_overlapping_training_and_evaluation_rejected(self):
        rows = self.fixture()
        with self.assertRaisesRegex(ValueError, "overlap"):
            fit_diagnostic(rows, rows[20]["trade_date"], rows[20]["trade_date"])


class MetricContractTests(unittest.TestCase):
    def test_negative_absolute_predictions_are_reported_without_clipping(self):
        values = error_metrics([.01, .02], [-.01, .03], .015, True)
        self.assertEqual(values["absolute_return_predictions_below_zero"], 1)
        self.assertAlmostEqual(values["mae"], .015)

    def test_rank_exposes_duplicated_lpr_and_unobserved_tool_columns(self):
        self.assertEqual(fraction_rank([[1, 0, 0, 0], [1, -5, -5, 0], [1, 0, 0, 0]]), 2)

    def test_nonfinite_prediction_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            error_metrics([.01], [float("nan")], .01, False)


if __name__ == "__main__":
    unittest.main()
