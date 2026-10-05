import unittest

from research.semantic.keyword_baseline import predict_item
from research.simulation.semantic_signal_join import join_steps
from research.simulation.wuhan_portfolio_keyword import reply_attention_row


class WuhanPortfolioKeywordTest(unittest.TestCase):
    def test_only_visible_reply_hit_enters_directionless_attention_channel(self):
        base = {"item_id": "item-1", "source_text_sha256": "source-1",
                "stock_code": "000001", "stage": "reply",
                "available_at": "2020-01-20T00:00:00+08:00"}
        question_only = {**base, "segments": [
            {"source": "question", "text": "请问疫情有什么影响？"},
            {"source": "reply", "text": "您好，谢谢关注。"}]}
        self.assertIsNone(reply_attention_row(question_only, predict_item(question_only), 0.25))
        reply_hit = {**base, "segments": [
            {"source": "question", "text": "请问进展？"},
            {"source": "reply", "text": "疫情影响尚不确定。"}]}
        row = reply_attention_row(reply_hit, predict_item(reply_hit), 0.25)
        self.assertEqual(row["text_signal"], 0.0)
        self.assertEqual(row["uncertainty"], 0.25)
        steps = [
            {"trade_date": "2020-01-20", "signal_cutoff_date": "2020-01-19"},
            {"trade_date": "2020-01-22", "signal_cutoff_date": "2020-01-21"},
        ]
        joined = join_steps(steps, "000001", [row])
        self.assertEqual([step["text_uncertainty"] for step in joined], [0.0, 0.25])
        self.assertEqual([step["text_signal"] for step in joined], [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
