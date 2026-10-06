import copy
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from research.baselines.event_study import DailyObservation
from research.semantic.source_case_facts import canonical, digest, normalize_facts
from research.simulation.single_observed_case import (
    REPLAY_CONFIG, audit, calculate, joined_conditions, read, select_steps, verify_observed_rows,
)
from research.simulation.source_case_decision_link import load_mapping


class SingleObservedCaseTest(unittest.TestCase):
    def setUp(self):
        self.config = read(REPLAY_CONFIG)
        self.config['start_date'] = '2020-01-26'
        self.config['end_date'] = '2020-03-15'
        self.rows = [DailyObservation(date(2020, 1, 1)+timedelta(days=i),
                    (i % 9-4)*0.002, (i % 7-3)*0.003) for i in range(75)]
        self.case = {'case_id': 'qa_test', 'stock_code': '300294',
            'source_kind': 'company_reply_workbook',
            'available_at': '2020-02-05T23:59:59.999999+08:00',
            'segments': [{'source': 'question', 'text': '收购已完成吗？'},
                         {'source': 'reply', 'text': '原料调拨审批具有不确定性。'}]}
        self.case['case_text_sha256'] = digest(canonical(self.case['segments']))
        self.record = normalize_facts(self.case, canonical({'facts': [{
            'kind': 'approval_uncertainty', 'status': 'uncertain',
            'claim': '原料调拨审批具有不确定性。',
            'evidence': [{'source': 'reply', 'quote': '原料调拨审批具有不确定性。'}],
        }]}).decode())
        self.plan = {'case': self.case, 'steps': select_steps(self.rows, self.case, self.config),
            'sessions': 30, 'information_duration_steps': 6, 'mapping': load_mapping(),
            'agents': self.config['agents'], 'fee_rate': 0.001, 'initial_price_index': 100.0,
            'market_dataset_id': 'synthetic_test_fixture_only'}
        for step in self.plan['steps']:
            step['execution_available'] = True

    def test_source_enters_only_after_end_of_day_and_expires_after_six_steps(self):
        joined = joined_conditions(self.plan, self.record)
        llm = joined['reviewed_llm']
        self.assertEqual(llm[4]['signal_cutoff_date'], '2020-02-05')
        self.assertEqual(llm[4]['trade_date'], '2020-02-07')
        self.assertTrue(all(not s['source_visible'] for s in llm[:4]))
        self.assertEqual(sum(s['source_active'] for s in llm), 6)
        self.assertTrue(all(s['text_signal'] == s['text_uncertainty'] == 0
                            for s in llm[:4]+llm[10:]))
        self.assertTrue(all(s['text_signal'] == s['text_uncertainty'] == 0
                            for s in joined['no_text']))

    def test_future_return_cannot_change_same_day_decision(self):
        original = calculate(self.plan, self.record)
        changed = copy.deepcopy(self.plan)
        changed['steps'][6]['observed_return'] = -0.3
        perturbed = calculate(changed, self.record)
        for mode in original['paths']:
            a, b = original['paths'][mode]['trace'], perturbed['paths'][mode]['trace']
            self.assertEqual(a[:6], b[:6])
            self.assertEqual({n: r['decision'] for n, r in a[6]['agents'].items()},
                             {n: r['decision'] for n, r in b[6]['agents'].items()})
            self.assertNotEqual(a[6]['price_index_after'], b[6]['price_index_after'])

    def test_suspension_blocks_all_fills_and_cash_ledger_rebuilds(self):
        self.plan['steps'][4]['execution_available'] = False
        result = calculate(self.plan, self.record)
        for path in result['paths'].values():
            self.assertTrue(all(r['filled_shares'] == r['fees_paid'] == 0
                                for r in path['trace'][4]['agents'].values()))
        self.assertEqual(audit(self.plan, result)['agent_decisions'], 270)
        self.assertEqual(result, calculate(self.plan, self.record))

    def test_independent_audit_rejects_missing_days_early_text_or_wrong_wealth(self):
        result = calculate(self.plan, self.record)
        for mutation in ('missing_day', 'early_text', 'wrong_wealth', 'extended_source'):
            changed = copy.deepcopy(result)
            trace = changed['paths']['reviewed_llm']['trace']
            if mutation == 'missing_day':
                trace.pop()
            elif mutation == 'early_text':
                trace[0]['text_uncertainty'] = 0.6
            elif mutation == 'wrong_wealth':
                trace[4]['agents']['aggressive']['closing_wealth'] += 1
            else:
                trace[10]['source_active'] = True
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                audit(self.plan, changed)

    def test_imported_returns_are_reconciled_against_provider_raw_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            stock, benchmark = Path(tmp)/'stock.csv', Path(tmp)/'benchmark.csv'
            stock.write_text('date,pctChg\n2020-01-02,2.5\n', encoding='utf-8')
            benchmark.write_text('date,close\n2020-01-01,100\n2020-01-02,101\n', encoding='utf-8')
            rows = [DailyObservation(date(2020, 1, 2), 0.025, 0.01)]
            self.assertEqual(verify_observed_rows(rows, stock, benchmark)['stock_return_rows'], 1)
            with self.assertRaisesRegex(ValueError, '不一致'):
                verify_observed_rows([DailyObservation(date(2020, 1, 2), 0.25, 0.01)], stock, benchmark)

    def test_incomplete_real_window_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '不足'):
            select_steps(self.rows[:45], self.case, self.config)


if __name__ == '__main__':
    unittest.main()
