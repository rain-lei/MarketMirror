import unittest

from research.workbench.build import validate_public_payload


class WorkbenchPayloadTest(unittest.TestCase):
    def test_summary_only_payload_accepts_public_metrics_and_sources(self):
        validate_public_payload({"events": [{"car": -0.04, "stock_code": "000001"}],
                                 "evidence": [{"url": "https://example.org/notice"}]})

    def test_private_text_identifiers_and_host_paths_fail(self):
        for payload in ({"question_text": "private"},
                        {"nested": [{"raw_response": "private"}]},
                        {"path": r"D:\rain\private.xlsx"},
                        {"value": "wxid_private_identifier"}):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "private"):
                validate_public_payload(payload)


if __name__ == "__main__":
    unittest.main()
