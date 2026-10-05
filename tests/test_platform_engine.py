import unittest

from design.engine import run_market


class PlatformEngineTest(unittest.TestCase):
    def test_control_isolated_and_execution_reproducible(self):
        config = dict(cash=1_000_000, seed=7, sessions=6, duration=3, signal=.6, uncertainty=.2)
        first = run_market(config)
        self.assertEqual(first, run_market(config))
        opposite = run_market({**config, 'signal': -.6, 'uncertainty': .8})
        self.assertEqual(first['paths']['baseline'], opposite['paths']['baseline'])
        self.assertNotEqual(first['paths']['with_message'], opposite['paths']['with_message'])
        self.assertEqual(first['audit']['days_checked'], 12)
        self.assertTrue(first['audit']['passed'])
        for result in first['paths'].values():
            for day in result['trace']:
                for call in day['portfolio_auction']['asset_calls'].values():
                    for order in call['orders']:
                        self.assertLessEqual(order['filled_quantity'], order['accepted_quantity'])
                        self.assertEqual(order['filled_quantity'] % 100, 0)

    def test_neutral_message_is_distinct_from_uncertainty_and_expires(self):
        for seed in (1, 7, 19):
            config = dict(cash=1000000, seed=seed, sessions=9, duration=3,
                          signal=0, uncertainty=0)
            neutral = run_market(config)
            uncertain = run_market({**config, 'uncertainty': .2})
            baseline = neutral['paths']['baseline']['trace']
            self.assertEqual(neutral['paths']['baseline'], uncertain['paths']['baseline'])
            for control, message in zip(baseline, neutral['paths']['with_message']['trace']):
                self.assertEqual(control['decisions'], message['decisions'])
                self.assertEqual(control['portfolio_auction'], message['portfolio_auction'])
            message = uncertain['paths']['with_message']['trace']
            for step in range(4):
                self.assertEqual(baseline[step]['decisions'], message[step]['decisions'])
            parameters = {p['name']: p for p in uncertain['effective_agent_parameters']}
            for name, decision in message[4]['decisions'].items():
                difference = decision['beliefs']['A'] - baseline[4]['decisions'][name]['beliefs']['A']
                self.assertAlmostEqual(difference, -.2 * parameters[name.rsplit('_', 1)[0]]['uncertainty_aversion'])
            for step, day in enumerate(message):
                expected = .2 if 4 <= step < 7 else 0
                self.assertEqual(day['observations']['A']['text_uncertainty'], expected)
                for asset in ('B', 'C'):
                    self.assertEqual(day['observations'][asset]['text_uncertainty'], 0)
            # Expiry clears new information input; past trades may persist.
            self.assertTrue(uncertain['audit']['passed'])

    def test_message_has_no_effect_before_visibility(self):
        result = run_market(dict(cash=1000000, seed=7, sessions=4, duration=6,
                                 signal=.9, uncertainty=.7))
        for baseline, message in zip(result['paths']['baseline']['trace'],
                                     result['paths']['with_message']['trace'], strict=True):
            # Provenance correctly differs between enabled and disabled text;
            # decisions and actual settlement must be identical before visibility.
            self.assertEqual(baseline['decisions'], message['decisions'])
            self.assertEqual(baseline['portfolio_auction'], message['portfolio_auction'])


if __name__ == '__main__':
    unittest.main()
