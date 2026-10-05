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
