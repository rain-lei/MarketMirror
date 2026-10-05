import json
import unittest

from design.text_analysis import PROMPT, PROMPT_VERSION, validate_facts


class TextAnalysisTest(unittest.TestCase):
    def test_reviewed_prompt_is_production_contract(self):
        self.assertEqual(PROMPT_VERSION, 'v3')
        self.assertIn('重复短句必须向前或向后扩展', PROMPT)
        self.assertIn('未知量都不能填零', PROMPT)

    def test_quote_offsets_are_exact(self):
        source = '公司回复：项目尚未获得审批，最终结果存在不确定性。'
        facts = validate_facts(source, json.dumps({'facts': [{
            'claim': '项目审批未完成', 'status': 'uncertain', 'quote': '项目尚未获得审批'}]}))
        row = facts[0]
        self.assertEqual(source[row['start']:row['end']], row['quote'])

    def test_fabricated_and_ambiguous_quotes_fail(self):
        for source, quote in [('尚未获批', '已经获批'), ('未获批，未获批', '未获批')]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_facts(source, json.dumps({'facts': [{
                    'claim': 'test', 'status': 'confirmed', 'quote': quote}]}))

    def test_empty_is_valid_but_wrong_schema_is_not(self):
        self.assertEqual(validate_facts('source', '{"facts":[]}'), [])
        with self.assertRaises(ValueError):
            validate_facts('source', '{"signal":0.9}')


if __name__ == '__main__':
    unittest.main()
