import unittest

from research.simulation.scenario_shocks import build_scenario_shocks, validate_scenario_shocks


class ScenarioShockTest(unittest.TestCase):
    def test_build_is_deterministic_and_separates_common_and_issuer_paths(self):
        args = (['000001', '600000', '000002'], 4, 'common_and_specific', [1, 3], [1, -1], 0.3, 0.2, 'seed-v1')
        first = build_scenario_shocks(*args)
        second = build_scenario_shocks(*args)
        self.assertEqual(first, second)
        self.assertEqual(first['common'], [0.0, 0.3, 0.0, -0.3])
        self.assertEqual(set(first['asset_specific']), set(args[0]))
        for asset, values in first['asset_specific'].items():
            self.assertEqual(values[1], -values[3])
            self.assertEqual(values[0], 0.0)
            self.assertEqual(values[2], 0.0)
            self.assertTrue(all(abs(first['common'][i] + values[i]) <= 1 for i in range(4)))

    def test_rejects_incomplete_path_and_out_of_range_combined_shock(self):
        valid = {'scenario_id': 'x', 'common': [0.7], 'asset_specific': {'A': [0.4]}}
        with self.assertRaisesRegex(ValueError, 'between -1 and 1'):
            validate_scenario_shocks(['A'], 1, valid)
        with self.assertRaisesRegex(ValueError, 'complete session'):
            validate_scenario_shocks(['A'], 2, {'scenario_id': 'x', 'common': [0.0],
                                                  'asset_specific': {'A': [0.0]}})

    def test_pulse_schedule_rejects_future_or_duplicate_session(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            build_scenario_shocks(['A'], 2, 'x', [2], [1], 0.1, 0.0, 'seed')
        with self.assertRaisesRegex(ValueError, 'schedule'):
            build_scenario_shocks(['A'], 2, 'x', [0, 0], [1, -1], 0.1, 0.0, 'seed')


if __name__ == '__main__':
    unittest.main()
