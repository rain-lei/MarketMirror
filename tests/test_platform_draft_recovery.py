import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from design.server import PlatformStore, atomic_json, create_server
from tests.test_platform_analysis_binding import SOURCE, extracted


class DraftRecoveryHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PlatformStore(Path(self.tmp.name))
        self.analysis = self.store.save_analysis(SOURCE, extracted())
        self.server = create_server(self.store.data_dir, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.worker.join(timeout=10)
        self.server.server_close()

    def get(self, route):
        try:
            response = urlopen(self.base + route, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_saved_analysis_is_revalidated_and_read_without_model_calls_or_data_changes(self):
        files = {path: path.read_bytes() for path in self.store.data_dir.rglob('*.json')}
        with patch('design.server.analyze') as model:
            for _ in range(2):
                status, headers, raw = self.get('/api/platform/analyses/' + self.analysis['analysis_id'])
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(raw), self.analysis)
                self.assertEqual(headers['Cache-Control'], 'no-store')
        model.assert_not_called()
        self.assertEqual(self.store.list(), [])
        self.assertEqual({path: path.read_bytes() for path in self.store.data_dir.rglob('*.json')}, files)

    def test_absent_or_invalid_analysis_ids_do_not_return_a_substitute(self):
        self.assertEqual(self.get('/api/platform/analyses/' + 'f' * 32)[0], 404)
        for analysis_id in ('not-an-id', '../settings/strategies', 'a' * 32 + '/extra'):
            self.assertEqual(self.get('/api/platform/analyses/' + analysis_id)[0], 400)

    def test_changed_hash_quote_span_or_identity_is_rejected_while_other_analyses_remain_readable(self):
        healthy = self.store.save_analysis(SOURCE, extracted())
        path = self.store.data_dir / 'analyses' / (self.analysis['analysis_id'] + '.json')
        for field in ('hash', 'span', 'identity', 'source'):
            value = copy.deepcopy(self.analysis)
            if field == 'hash': value['source_sha256'] = '0' * 64
            elif field == 'span': value['facts'][0]['start'] += 1
            elif field == 'identity': value['analysis_id'] = '0' * 32
            else: value['source'] = value['source'].strip()
            atomic_json(path, value)
            before = path.read_bytes()
            self.assertEqual(self.get('/api/platform/analyses/' + self.analysis['analysis_id'])[0], 400)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(self.get('/api/platform/analyses/' + healthy['analysis_id'])[0], 200)

    def test_recovery_script_is_served_as_current_javascript(self):
        status, headers, raw = self.get('/draft-cache.js')
        self.assertEqual(status, 200)
        self.assertIn('text/javascript', headers['Content-Type'])
        self.assertEqual(raw, Path('design/draft-cache.js').read_bytes())


if __name__ == '__main__':
    unittest.main()
