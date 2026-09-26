import unittest
from datetime import date, timedelta

from research.data_pipeline.common import (
    canonical_headers,
    normalize_reply_status,
    normalize_stock_code,
    row_looks_like_header,
)
from research.data_pipeline.aggregate_qa_features import calendar_period, FeatureAccumulator
from research.baselines.event_study import DailyObservation, event_study, fit_market_model


class DataPipelineTest(unittest.TestCase):
    def test_header_mapping_and_duplicate_names(self):
        headers = canonical_headers(["Scode", "股票代码", "Qtm", "提问时间"])
        self.assertEqual(headers, ["stock_code", "stock_code_2", "question_time", "question_time_2"])
        self.assertEqual(canonical_headers(["来源"]), ["source"])

    def test_stock_code_is_six_digits(self):
        self.assertEqual(normalize_stock_code(1), "000001")
        self.assertEqual(normalize_stock_code("000001"), "000001")
        self.assertEqual(normalize_stock_code(""), None)

    def test_reply_status(self):
        self.assertEqual(normalize_reply_status("已回复"), "replied")
        self.assertEqual(normalize_reply_status("未回复"), "unreplied")

    def test_description_row_is_detected(self):
        header = ["From", "Scode", "Coname", "Qtm", "Qsubj", "Reply"]
        descriptions = ["数据来源", "股票代码", "公司简称", "提问时间", "提问内容", "回复内容"]
        data = ["深交所互动易", "000001", "平安银行", "2024-01-02", "问题", "回答"]
        self.assertTrue(row_looks_like_header(descriptions, header))
        self.assertFalse(row_looks_like_header(data, header))

    def test_period_and_keyword_features(self):
        self.assertEqual(calendar_period("2020-02-05 23:12:26"), "2020-Q1")
        acc = FeatureAccumulator()
        acc.add({
            "stock_code": 1,
            "company_name": "平安银行",
            "user_name": "u1",
            "user_type": "注册用户",
            "question_text": "请问疫情下公司的现金流和风险如何？",
            "reply_status": "已回复",
        })
        row = acc.as_row("2020-Q1")
        self.assertEqual(row["stock_code"], "000001")
        self.assertEqual(row["question_count"], 1)
        self.assertEqual(row["pandemic_count"], 1)
        self.assertEqual(row["liquidity_count"], 1)
        self.assertEqual(row["post_event_replied_count"], 1)

    def test_market_model_and_event_study(self):
        stock = [0.01 * i for i in range(40)]
        market = [0.005 * i for i in range(40)]
        alpha, beta = fit_market_model(stock, market)
        self.assertAlmostEqual(alpha, 0.0, places=8)
        self.assertAlmostEqual(beta, 2.0, places=8)
        observations = [
            DailyObservation(
                trade_date=date(2020, 1, 1) + timedelta(days=i),
                stock_return=stock[i],
                market_return=market[i],
            )
            for i in range(40)
        ]
        result = event_study(observations, "2020-02-05", estimation_window=20, pre_event_gap=2, window_after=3)
        self.assertEqual(result["estimation_observations"], 20)
        self.assertAlmostEqual(result["beta"], 2.0, places=8)


if __name__ == "__main__":
    unittest.main()
