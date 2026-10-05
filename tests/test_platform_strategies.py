import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from design.engine import run_market
from design.server import PlatformStore, atomic_json, create_server
from design.strategy_config import CONFIG, defaults, parameters_digest, validate_parameters


def experiment(**overrides):
    return {'title': '策略参数验收', 'source': '这是一条虚构的测试消息，宣布增加长期流动性供给，具体实施进度仍待明确。',
            'type': '政策消息', 'published_at': '2026-10-05T09:00', 'signal': .6,
            'uncertainty': .2, 'duration': 6, 'sessions': 10, 'seed': 7, 'cash': 1000000, **overrides}


class StrategyConfigurationTest(unittest.TestCase):
    def test_rejects_incomplete_unknown_and_out_of_bounds_parameters(self):
        valid = defaults()
        invalid = [None, [], {}, {**valid, 'another_role': valid['aggressive']}]
        for field, value in [('text_sensitivity', True), ('text_sensitivity', '1'),
                             ('risk_budget', float('nan')), ('base_weight', float('inf')),
                             ('risk_budget', 0), ('text_sensitivity', 2.1),
                             ('base_weight', .56)]:
            row = copy.deepcopy(valid)
            row['conservative'][field] = value
            invalid.append(row)
        row = copy.deepcopy(valid)
        row['institutional']['max_weight'] = 1.0
        invalid.append(row)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_parameters(value)
        self.assertEqual(valid, validate_parameters(valid))

    def test_profile_changes_only_affect_new_experiments(self):
        before = CONFIG.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            store = PlatformStore(Path(tmp))
            first = store.create(experiment())
            original_result = store.run(first['id'])
            changed = defaults()
            changed['aggressive']['text_sensitivity'] = 0.
            saved = store.save_strategies(changed)
            reopened = PlatformStore(Path(tmp))
            second = reopened.create(experiment())
            self.assertEqual(reopened.strategies()['parameters'], changed)
            self.assertTrue(saved['updated_at'])
            self.assertEqual(second['strategy_parameters'], changed)
            self.assertEqual(first['strategy_parameters'], defaults())
            self.assertEqual(reopened.run(first['id'])['paths'], original_result['paths'])
            self.assertEqual(reopened.get(first['id'])['strategy_parameters'], defaults())
            explicit = reopened.create(experiment(strategy_parameters=defaults()))
            self.assertEqual(explicit['strategy_parameters'], defaults())
            self.assertEqual(explicit['strategy_parameters_origin'], 'explicit')
        self.assertEqual(before, CONFIG.read_bytes())

    def test_engine_receives_parameters_in_both_groups_and_changes_real_orders(self):
        original = run_market(experiment())
        changed = defaults()
        changed['aggressive']['text_sensitivity'] = 0.
        result = run_market(experiment(strategy_parameters=changed))
        self.assertEqual(result['strategy_parameters_sha256'], parameters_digest(changed))
        for path in result['paths'].values():
            strategies = [s for s in path['participant_specs'].values() if s['kind'] == 'strategy']
            self.assertEqual(len(strategies), 12)
            for spec in strategies:
                p = spec['parameters']
                for field, expected in changed[p['role']].items():
                    self.assertEqual(p[field], expected)
        # Text sensitivity must have no effect when the text channel is disabled.
        self.assertEqual(result['paths']['baseline']['trace'], original['paths']['baseline']['trace'])
        a = original['paths']['with_message']['trace']
        b = result['paths']['with_message']['trace']
        for index in range(4):
            self.assertEqual(a[index]['decisions'], b[index]['decisions'])
        self.assertNotEqual(a[4]['decisions'], b[4]['decisions'])
        self.assertNotEqual(a[4]['portfolio_auction']['asset_calls']['A']['orders'],
                            b[4]['portfolio_auction']['asset_calls']['A']['orders'])
        self.assertTrue(result['audit']['passed'])

    def test_changed_snapshot_or_mechanism_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PlatformStore(Path(tmp))
            record = store.create(experiment())
            path = Path(tmp) / record['id'] / 'experiment.json'
            changed = copy.deepcopy(record)
            changed['strategy_parameters']['aggressive']['base_weight'] = .4
            atomic_json(path, changed)
            with self.assertRaisesRegex(ValueError, '策略参数快照'):
                store.run(record['id'])
            changed = copy.deepcopy(record)
            changed['mechanism_config_sha256'] = '0' * 64
            atomic_json(path, changed)
            with self.assertRaisesRegex(ValueError, '基础撮合配置'):
                store.run(record['id'])
            self.assertIsNone(store.result(record['id']))


class StrategyHttpTest(unittest.TestCase):
    def test_save_reload_explicit_snapshot_run_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = create_server(Path(tmp), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def request(path, data=None):
                body = json.dumps(data).encode() if data is not None else None
                req = Request(base + path, data=body, headers={'Origin': base, 'Content-Type': 'application/json'})
                try:
                    response = urlopen(req, timeout=10)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, json.loads(response.read())
            try:
                status, profile = request('/api/platform/strategies')
                self.assertEqual(status, 200)
                changed = profile['parameters']
                changed['conservative']['risk_budget'] = .001
                status, saved = request('/api/platform/strategies', {'parameters': changed})
                self.assertEqual(status, 200)
                self.assertEqual(request('/api/platform/strategies')[1]['parameters'], changed)
                self.assertEqual(request('/api/platform/strategies', {'parameters': {}})[0], 400)
                self.assertEqual(request('/api/platform/strategies')[1]['parameters'], changed)
                status, record = request('/api/platform/experiments', experiment(strategy_parameters=changed))
                self.assertEqual(status, 201)
                path = '/api/platform/experiments/' + record['id']
                self.assertEqual(request(path + '/run', {})[0], 202)
                status, exported = request(path + '/export')
                e = exported['experiment']
                self.assertEqual(e['strategy_parameters'], changed)
                self.assertEqual(e['backendResult']['strategy_parameters'], changed)
                self.assertEqual(e['backendResult']['provenance']['strategy_parameters_sha256'], saved['strategy_parameters_sha256'])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
