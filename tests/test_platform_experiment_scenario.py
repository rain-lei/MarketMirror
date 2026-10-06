import copy
import math
import tempfile
import unittest
from pathlib import Path

from design.engine import run_market
from design.experiment_scenario import DEFAULT_ASSUMPTIONS, assumptions_digest
from design.server import PlatformStore, analysis_digest, atomic_json, validate_experiment
from design.strategy_config import defaults, load_model
from design.strategy_preview import preview_decisions


class ExperimentScenarioTest(unittest.TestCase):
    def config(self):
        return dict(cash=1000000, seed=7, sessions=8, duration=3, signal=.6, uncertainty=.2)

    def payload(self):
        return {**self.config(), 'title': '预览情景实验', 'type': '政策消息',
                'published_at': '2026-10-07T09:00',
                'source': '人工假设情景：消息带来正向信息，但实施范围和落地时间仍存在不确定性。'}

    def reference(self):
        return {'parameters': defaults(), 'mechanism_config_sha256': load_model()[1],
                'scenario': dict(signal=.6, uncertainty=.2, market=-.2, volatility=.03,
                                 scope='public', history='positive')}

    def test_legacy_submission_shape_and_default_paths_remain_unchanged(self):
        payload = self.payload()
        legacy = validate_experiment(payload)
        self.assertEqual(set(legacy), set(payload) | {'analysis_id', 'strategy_parameters'})
        default = validate_experiment({**payload, 'market_assumptions': DEFAULT_ASSUMPTIONS})
        self.assertEqual(legacy, {k: v for k, v in default.items() if k != 'market_assumptions'})
        self.assertEqual(run_market(self.config())['paths'], run_market({**self.config(), 'market_assumptions': DEFAULT_ASSUMPTIONS})['paths'])
        self.assertNotEqual(analysis_digest(legacy), analysis_digest(default))

    def test_public_text_exposure_respects_visibility_and_expiry_on_all_assets(self):
        result = run_market({**self.config(), 'market_assumptions': {**DEFAULT_ASSUMPTIONS, 'scope': 'public'}})
        self.assertTrue(result['audit']['passed'])
        self.assertEqual(result['assumptions']['exposed_asset'], 'A/B/C')
        trace = result['paths']['with_message']['trace']
        for index, day in enumerate(trace):
            for asset in ('A', 'B', 'C'):
                self.assertEqual(day['observations'][asset]['text_signal'], .6 if 4 <= index < 7 else 0.)
                self.assertEqual(day['observations'][asset]['text_uncertainty'], .2 if 4 <= index < 7 else 0.)
            if index < 4:
                self.assertEqual(day['decisions'], result['paths']['baseline']['trace'][index]['decisions'])
        issuer_only = run_market(self.config())['paths']['with_message']['trace'][4]
        self.assertEqual(issuer_only['observations']['B']['text_signal'], 0.)

    def test_market_offset_changes_baseline_decisions_and_is_shared_between_conditions(self):
        assumptions = dict(scope='public', market=-.25, volatility=.02)
        result = run_market({**self.config(), 'market_assumptions': assumptions})
        plain = run_market(self.config())
        self.assertNotEqual(result['paths']['baseline']['trace'][0]['decisions'], plain['paths']['baseline']['trace'][0]['decisions'])
        for name, path in result['paths'].items():
            for day in path['trace']:
                for asset in ('A', 'B', 'C'):
                    observation = day['observations'][asset]
                    self.assertEqual(observation['scenario_shock'], -.25)
                    for account, decision in day['decisions'].items():
                        spec = path['participant_specs'][account]
                        p, profile = spec['parameters'], spec['profile']
                        market = max(-1., min(1., observation['market_signal'] * profile['momentum_loading'] + profile['market_bias'] - .25))
                        text = p['text_sensitivity'] * observation['text_signal'] - p['uncertainty_aversion'] * observation['text_uncertainty'] if name == 'with_message' else 0.
                        self.assertAlmostEqual(decision['beliefs'][asset], p['market_sensitivity'] * market + text)
        for step in range(4):
            self.assertEqual(result['paths']['baseline']['trace'][step]['portfolio_auction'], result['paths']['with_message']['trace'][step]['portfolio_auction'])

    def test_higher_volatility_floor_restricts_actual_target_risk(self):
        low = run_market({**self.config(), 'market_assumptions': DEFAULT_ASSUMPTIONS})
        high = run_market({**self.config(), 'market_assumptions': dict(scope='A', market=0., volatility=.3)})
        for path in high['paths'].values():
            for day in path['trace']:
                for asset in ('A', 'B', 'C'):
                    self.assertGreaterEqual(day['covariance'][asset][asset], .3 ** 2)
        low_day, high_day = low['paths']['with_message']['trace'][0], high['paths']['with_message']['trace'][0]
        specs = high['paths']['with_message']['participant_specs']
        for account, spec in specs.items():
            if spec.get('parameters', {}).get('role') == 'conservative':
                self.assertLess(sum(high_day['decisions'][account]['desired_weights'].values()), sum(low_day['decisions'][account]['desired_weights'].values()))
        self.assertTrue(high['assumptions']['volatility_is_floor'])
        self.assertEqual(high['market_assumptions_sha256'], assumptions_digest(high['market_assumptions']))

    def test_no_text_control_is_independent_of_signal_uncertainty_and_message_scope(self):
        a = {**self.config(), 'market_assumptions': dict(scope='A', market=.1, volatility=.03)}
        b = {**a, 'signal': -.7, 'uncertainty': .9, 'market_assumptions': {**a['market_assumptions'], 'scope': 'public'}}
        self.assertEqual(run_market(a)['paths']['baseline'], run_market(b)['paths']['baseline'])

    def test_saved_preview_is_reconstructed_exported_and_kept_as_reference_only(self):
        reference = self.reference()
        payload = {**self.payload(), 'strategy_parameters': reference['parameters'],
                   'market_assumptions': dict(scope='public', market=-.2, volatility=.03),
                   'preview_reference': reference}
        # Later draft edits remain distinct from the source preview snapshot.
        payload['signal'] = -.4
        with tempfile.TemporaryDirectory() as tmp:
            store = PlatformStore(Path(tmp))
            record = store.create(payload, 'a' * 32)
            snapshot = preview_decisions({k: reference[k] for k in ('parameters', 'scenario')})
            self.assertEqual(record['decision_preview'], snapshot)
            self.assertEqual(store.create(copy.deepcopy(payload), record['id']), record)
            result = store.run(record['id'])
            export = store.export(record['id'])['experiment']
            self.assertEqual(export['preview_reference'], reference)
            self.assertEqual(export['decision_preview'], snapshot)
            self.assertEqual(result['provenance']['decision_preview_sha256'], analysis_digest(snapshot))
            self.assertEqual(result['provenance']['decision_preview_role'], 'reference_only')
            self.assertFalse(result['provenance']['preview_memory_applied'])
            self.assertEqual(result['assumptions']['memory_initial_state'], {'sign': 0, 'streak': 0})
            self.assertEqual(snapshot['state_before']['memory'], {'sign': 1, 'streak': 2})
            self.assertEqual(export['signal'], -.4)

    def test_invalid_or_partial_assumptions_and_preview_references_fail_explicitly(self):
        for invalid in (None, {}, [], {**DEFAULT_ASSUMPTIONS, 'extra': 1},
                        {**DEFAULT_ASSUMPTIONS, 'market': True}, {**DEFAULT_ASSUMPTIONS, 'market': math.nan},
                        {**DEFAULT_ASSUMPTIONS, 'volatility': 0}, {**DEFAULT_ASSUMPTIONS, 'volatility': .51},
                        {**DEFAULT_ASSUMPTIONS, 'scope': 'D'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_experiment({**self.payload(), 'market_assumptions': invalid})
        ref = self.reference()
        for bad in (None, {}, {**ref, 'mechanism_config_sha256': '0' * 64},
                    {**ref, 'scenario': {**ref['scenario'], 'history': 'future'}}):
            with self.subTest(reference=bad), self.assertRaises(ValueError):
                validate_experiment({**self.payload(), 'market_assumptions': DEFAULT_ASSUMPTIONS, 'preview_reference': bad})
        with self.assertRaises(ValueError):
            validate_experiment({**self.payload(), 'preview_reference': ref})

    def test_changed_assumptions_preview_snapshot_or_reference_cannot_be_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PlatformStore(Path(tmp))
            for field in ('market_assumptions', 'preview_reference', 'decision_preview'):
                record = store.create({**self.payload(), 'market_assumptions': DEFAULT_ASSUMPTIONS,
                                       'preview_reference': self.reference()})
                if field == 'market_assumptions': record[field]['market'] = .7
                elif field == 'preview_reference': record[field]['scenario']['history'] = 'negative'
                else: record[field]['roles']['aggressive']['with_message']['total_target_weight'] = 0
                atomic_json(Path(tmp) / record['id'] / 'experiment.json', record)
                with self.subTest(field=field), self.assertRaises(ValueError):
                    store.run(record['id'])
                self.assertEqual(store.get(record['id'])['run_status'], 'failed')
                self.assertIsNone(store.result(record['id']))


if __name__ == '__main__':
    unittest.main()
