import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from design.server import PlatformStore, atomic_json, create_server
from design.text_analysis import PROMPT, source_digest, validate_facts

SOURCE = ' \n公司公告：新建生产线仍在办理审批手续，尚未取得正式批复，投产时间存在不确定性。\n '
QUOTE = '尚未取得正式批复'


def extracted(source=SOURCE):
    raw = json.dumps({'facts': [{'claim': '新生产线审批尚未完成', 'status': 'uncertain', 'quote': QUOTE}]}, ensure_ascii=False)
    return {'model': 'DeepSeek-V4-Flash-0731-W8A8', 'source_sha256': source_digest(source),
            'raw_response': raw, 'facts': validate_facts(source, raw),
            'prompt_sha256': source_digest(PROMPT), 'evidence_verified': True, 'semantic_truth_verified': False}


def payload():
    return {'title': '带原文依据的实验', 'source': SOURCE, 'type': '公司问答',
            'published_at': '2026-10-05T09:00', 'signal': -.3, 'uncertainty': .7,
            'duration': 3, 'sessions': 6, 'seed': 7, 'cash': 1000000}


class AnalysisBindingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PlatformStore(Path(self.tmp.name))
        self.analysis = self.store.save_analysis(SOURCE, extracted())

    def test_binding_survives_restart_and_analysis_file_change(self):
        record = self.store.create({**payload(), 'analysis_id': self.analysis['analysis_id']})
        self.assertEqual(record['source'], SOURCE)
        self.assertEqual(record['source_sha256'], self.analysis['source_sha256'])
        saved = copy.deepcopy(record['text_analysis'])
        path = self.store.data_dir / 'analyses' / (self.analysis['analysis_id'] + '.json')
        atomic_json(path, {'replaced_later': True})
        reopened = PlatformStore(self.store.data_dir)
        self.assertEqual(reopened.get(record['id'])['text_analysis'], saved)
        result = reopened.run(record['id'])
        self.assertEqual(result['provenance']['analysis_id'], self.analysis['analysis_id'])
        self.assertEqual(result['provenance']['analysis_sha256'], record['analysis_sha256'])
        self.assertEqual(result['provenance']['scenario_variables_origin'], 'manual')
        self.assertEqual(result['provenance']['text_analysis_role'], 'reference_only')
        self.assertEqual(reopened.get(record['id'])['signal'], payload()['signal'])

    def test_changed_source_and_missing_analysis_fail_before_experiment_creation(self):
        cases = [
            {**payload(), 'source': SOURCE.strip(), 'analysis_id': self.analysis['analysis_id']},
            {**payload(), 'source': SOURCE.replace('尚未', '已经'), 'analysis_id': self.analysis['analysis_id']},
            {**payload(), 'analysis_id': 'f' * 32},
            {**payload(), 'analysis_id': '../analyses/test'},
            {**payload(), 'analysis_id': []},
        ]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.store.create(item)
        self.assertEqual(self.store.list(), [])

    def test_tampered_quote_offsets_or_hash_are_rejected(self):
        path = self.store.data_dir / 'analyses' / (self.analysis['analysis_id'] + '.json')
        for field in ('offset', 'hash'):
            record = copy.deepcopy(self.analysis)
            if field == 'offset':
                record['facts'][0]['start'] += 1
            else:
                record['source_sha256'] = '0' * 64
            atomic_json(path, record)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.create({**payload(), 'analysis_id': self.analysis['analysis_id']})

    def test_model_evidence_does_not_change_manual_simulation(self):
        linked = self.store.create({**payload(), 'analysis_id': self.analysis['analysis_id']})
        manual = self.store.create(payload())
        self.assertEqual(self.store.run(linked['id'])['paths'], self.store.run(manual['id'])['paths'])

    def test_modified_experiment_snapshot_cannot_run(self):
        record = self.store.create({**payload(), 'analysis_id': self.analysis['analysis_id']})
        record['text_analysis']['model'] = 'a-different-model'
        atomic_json(self.store.data_dir / record['id'] / 'experiment.json', record)
        with self.assertRaisesRegex(ValueError, '快照已改变'):
            self.store.run(record['id'])
        self.assertIsNone(self.store.result(record['id']))

    def test_empty_analysis_remains_a_record(self):
        analysis = self.store.save_analysis(SOURCE, {**extracted(), 'raw_response': '{"facts":[]}', 'facts': []})
        record = self.store.create({**payload(), 'analysis_id': analysis['analysis_id']})
        self.assertEqual(record['text_analysis']['facts'], [])
        self.assertEqual(record['analysis_id'], analysis['analysis_id'])


class AnalysisHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = create_server(Path(self.tmp.name), port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def request(self, path, data=None):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
        request = Request(self.base + path, data=raw, headers={'Content-Type': 'application/json', 'Origin': self.base})
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read().decode('utf-8'))

    def test_extract_create_run_reload_and_reject_stale_binding(self):
        with patch('design.server.analyze', return_value=extracted()) as model:
            status, analysis = self.request('/api/platform/analyze', {'source': SOURCE})
        model.assert_called_once_with(SOURCE)
        self.assertEqual(status, 200)
        self.assertEqual(analysis['source'], SOURCE)
        self.assertEqual(analysis['facts'][0]['quote'], QUOTE)
        status, record = self.request('/api/platform/experiments', {**payload(), 'analysis_id': analysis['analysis_id']})
        self.assertEqual(status, 201)
        endpoint = '/api/platform/experiments/' + record['id']
        status, result = self.request(endpoint + '/run', {})
        self.assertEqual(status, 202)
        self.assertTrue(result['audit']['passed'])
        self.assertEqual(result['provenance']['analysis_id'], analysis['analysis_id'])
        status, restored = self.request(endpoint)
        self.assertEqual(restored['text_analysis'], record['text_analysis'])
        with urlopen(self.base + endpoint + '/export', timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Content-Disposition'], f'attachment; filename="marketmirror-{record["id"]}.json"')
            exported = json.loads(response.read().decode('utf-8'))
        self.assertEqual(exported['mode'], 'synthetic_market')
        self.assertEqual(exported['experiment']['text_analysis'], record['text_analysis'])
        self.assertEqual(exported['experiment']['backendResult'], result)
        self.assertEqual(exported['experiment']['source'], SOURCE)
        self.assertIsNone(exported['fixture'])
        status, error = self.request('/api/platform/experiments', {**payload(), 'source': SOURCE + '补充', 'analysis_id': analysis['analysis_id']})
        self.assertEqual(status, 400)
        self.assertIn('原文不一致', error['error'])

    def test_model_error_never_creates_a_usable_analysis(self):
        with patch('design.server.analyze', side_effect=RuntimeError('provider unavailable')):
            status, error = self.request('/api/platform/analyze', {'source': SOURCE})
        self.assertEqual(status, 502)
        self.assertFalse((Path(self.tmp.name) / 'analyses').exists())

    def test_export_missing_experiment_is_not_a_download(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + '/api/platform/experiments/' + '0' * 32 + '/export', timeout=10)
        self.assertEqual(caught.exception.code, 404)
        self.assertIsNone(caught.exception.headers['Content-Disposition'])
        caught.exception.close()


if __name__ == '__main__':
    unittest.main()
