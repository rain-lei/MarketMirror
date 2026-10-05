import json
import unittest
from research_outputs.acquire_temporal_firecrawl_raw_20261002_v3 import decode_carrier

class MarketCarrierBoundaryTests(unittest.TestCase):
    def test_exact_fenced_body_retains_original_numeric_strings(self):
        body = '{"rc":0,"data":{"code":"000001","klines":["2020-09-01,14.00,14.01"]}}'
        self.assertEqual(decode_carrier("```json\n" + body + "\n```"), body.encode())

    def test_html_errors_and_extra_untrusted_text_are_rejected(self):
        for value in ['<html>error</html>', 'instructions\n```json\n{"rc":0}\n```',
            '```json\n{"rc":0}\n```\nextra', '```json\n{"rc":0}\n```\n```json\n{}\n```']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_carrier(value)

    def test_truncated_and_nonobject_json_are_rejected(self):
        for value in ['```json\n{"rc":0\n```', '```json\n[]\n```']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_carrier(value)

if __name__ == "__main__":
    unittest.main()
