import unittest

from research.data_pipeline.extract_issuer_restricted_assets import unbordered_frame


def word(text, x, y, width=30):
    return {"text": text, "bbox": [x, y, x+width, y+10]}


def line(words):
    return {"words": words, "text": " ".join(w["text"] for w in words), "pdf_page": 1, "page_height": 800,
            "bbox": [min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words), max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words)]}


class RestrictedAssetWordGeometryTest(unittest.TestCase):
    def data(self):
        anchor = line([word("81、所有权或使用权受到限制的资产", 50, 100, 300)])
        header = line([word("项目", 50, 130), word("期末账面价值", 220, 130, 70), word("受限原因", 400, 130, 60)])
        return anchor, header

    def test_ambiguous_two_numeric_words_in_ending_column_require_review(self):
        anchor, header = self.data()
        row = line([word("货币资金", 50, 160, 60), word("10", 240, 160), word("20", 300, 160), word("保证金", 430, 160)])
        self.assertIsNone(unbordered_frame([header, row], anchor))

    def test_next_note_not_read_as_more_restricted_assets(self):
        anchor, header = self.data()
        row = line([word("货币资金", 50, 160, 60), word("10", 240, 160), word("保证金", 430, 160)])
        other_note = line([word("82、外币货币性项目", 50, 190, 180)])
        other_row = line([word("货币资金", 50, 220, 60), word("99", 240, 220), word("美元", 430, 220)])
        table = unbordered_frame([header, row, other_note, other_row], anchor)
        self.assertEqual(len(table["rows"]), 2)
        self.assertEqual(table["rows"][1][1]["text"], "10")


if __name__ == "__main__":
    unittest.main()
