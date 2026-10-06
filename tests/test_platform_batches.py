"""Real engine runs through the persisted asynchronous batch workflow."""
import json
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from design.batches import BatchInProgress, BatchManager
from design.server import PlatformStore, atomic_json, create_server, run_market


class BatchManagerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = PlatformStore(Path(temporary.name))
        self.manager = BatchManager(self.store)
        self.addCleanup(self.manager.close)

    def create(self, **overrides):
        return self.manager.create({'title': '批次核验', 'seeds': [7], 'sessions': 6, **overrides})

    def finish(self, batch_id):
        with self.manager.lock:
            worker = self.manager.workers.get(batch_id)
        if worker:
            worker.join(timeout=15)
            self.assertFalse(worker.is_alive(), 'batch did not finish')
        return self.manager.get(batch_id)

    def test_real_batch_freezes_saved_parameters_and_has_shared_baselines(self):
        record = self.create()
        parameters = deepcopy(record['strategy_parameters'])
        changed = deepcopy(parameters)
        changed['aggressive']['base_weight'] += .01
        self.store.save_strategies(changed)
        self.manager.start(record['id'])
        completed = self.finish(record['id'])
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(completed['progress']['completed'], 4)
        self.assertEqual(len({row['baseline_sha256'] for row in completed['records']}), 1)
        self.assertTrue(all(row['audit']['passed'] for row in completed['records']))
        for row in completed['records']:
            experiment = self.store.get(row['experiment_id'])
            self.assertEqual(experiment['strategy_parameters'], parameters)
            result = self.manager.result(record['id'], row['experiment_id'])
            self.assertEqual(result['provenance']['result_id'], row['result_id'])
            self.assertEqual(row['audit']['days_checked'], 12)
        neutral = completed['records'][0]
        self.assertTrue(all(value == 0 for value in neutral['role_return_difference_pp'].values()))
        with self.assertRaises(BatchInProgress):
            self.manager.start(record['id'])

    def test_one_failure_continues_and_retry_only_runs_incomplete_item(self):
        record = self.create()
        def fail_positive(config):
            if config['signal'] > 0:
                raise RuntimeError('private internal message')
            return run_market(config)
        with patch('design.server.run_market', side_effect=fail_positive):
            self.manager.start(record['id'])
            partial = self.finish(record['id'])
        self.assertEqual(partial['status'], 'partial')
        self.assertEqual([row['status'] for row in partial['records']],
                         ['completed', 'failed', 'completed', 'completed'])
        self.assertNotIn('private internal message', json.dumps(partial))
        failed = partial['records'][1]
        self.assertNotIn('role_return_difference_pp', failed)
        self.assertNotIn('filled', failed)
        positive = [row for row in partial['summary'] if row['scenario'] == 'positive']
        self.assertTrue(all(row['mean_pp'] is None and row['completed'] == 0 for row in positive))
        self.assertIn('未完成项', self.manager.report(record['id']))
        self.manager.start(record['id'])
        retried = self.finish(record['id'])
        self.assertEqual(retried['status'], 'completed')
        self.assertEqual([row['attempts'] for row in retried['records']], [1, 2, 1, 1])
        for before, after in zip(partial['records'], retried['records']):
            if before['status'] == 'completed':
                self.assertEqual(before, after)

    def test_running_batch_is_readable_and_duplicate_start_is_rejected(self):
        record = self.create(sessions=1)
        entered, release = threading.Event(), threading.Event()
        def held(config):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('test release timeout')
            return run_market(config)
        with patch('design.server.run_market', side_effect=held):
            try:
                response = self.manager.start(record['id'])
                self.assertEqual(response['status'], 'running')
                self.assertTrue(entered.wait(3))
                observed = self.manager.get(record['id'])
                self.assertEqual(observed['progress']['running'], 1)
                self.assertEqual(observed['progress']['pending'], 3)
                self.assertIn('运行中', self.manager.report(record['id']))
                self.assertEqual(self.manager.list()[0]['id'], record['id'])
                with self.assertRaises(BatchInProgress):
                    self.manager.start(record['id'])
            finally:
                release.set()
                self.finish(record['id'])

    def test_restart_marks_orphaned_job_interrupted_and_resumes_it(self):
        record = self.create(sessions=1)
        record.pop('progress'); record.pop('summary')
        record['status'] = 'running'
        record['records'][0].update(status='running', attempts=1)
        self.manager._save(record)
        recovered = BatchManager(self.store)
        self.addCleanup(recovered.close)
        recovered.recover_interrupted()
        saved = recovered.get(record['id'])
        self.assertEqual(saved['status'], 'interrupted')
        self.assertEqual(saved['records'][0]['status'], 'interrupted')
        recovered.start(record['id'])
        with recovered.lock:
            worker = recovered.workers.get(record['id'])
        if worker:
            worker.join(15)
            self.assertFalse(worker.is_alive())
        self.assertEqual(recovered.get(record['id'])['status'], 'completed')

    def test_archive_remains_fixed_after_single_experiment_rerun(self):
        record = self.create(sessions=1)
        self.manager.start(record['id'])
        completed = self.finish(record['id'])
        row = completed['records'][0]
        self.store.run(row['experiment_id'])
        self.assertNotEqual(self.store.get(row['experiment_id'])['result_id'], row['result_id'])
        archived = self.manager.result(record['id'], row['experiment_id'])
        self.assertEqual(archived['provenance']['result_id'], row['result_id'])
        archived['paths']['baseline']['summary']['role_wealth_multiple']['aggressive'] += .01
        atomic_json(self.store.data_dir / row['experiment_id'] / 'runs' / (row['result_id'] + '.json'), archived)
        with self.assertRaisesRegex(ValueError, '路径校验失败'):
            self.manager.result(record['id'], row['experiment_id'])

    def test_invalid_requests_leave_no_batch_files_and_ready_report_has_missing_values(self):
        for overrides in ({'seeds': [True]}, {'seeds': [1, 1]}, {'seeds': []},
                          {'sessions': 0}, {'duration': 4}, {'cash': float('nan')}, {'extra': 1}):
            with self.assertRaises(ValueError):
                self.create(**overrides)
        self.assertFalse(self.manager.directory.exists())
        record = self.create()
        report = self.manager.report(record['id'])
        self.assertIn('完成 0/4', report)
        self.assertIn('未完成 | — | — | — | —', report)


class BatchHTTPTest(unittest.TestCase):
    def test_http_create_start_read_result_and_download_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = create_server(Path(tmp), 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def request(path, payload=None):
                body = json.dumps(payload).encode() if payload is not None else None
                with urlopen(Request(base + path, data=body, headers={'Content-Type': 'application/json'}), timeout=10) as response:
                    return response.status, response.headers, response.read()
            try:
                status, _, raw = request('/api/platform/batches', {'title': 'HTTP 批次', 'seeds': [19], 'sessions': 1})
                self.assertEqual(status, 201)
                record = json.loads(raw)
                path = '/api/platform/batches/' + record['id']
                self.assertEqual(request(path + '/run', {})[0], 202)
                # server_close waits on the actual worker handle without stopping HTTP.
                for worker in threading.enumerate():
                    if worker.name == 'marketmirror-batch-' + record['id'][:8]:
                        worker.join(15)
                        self.assertFalse(worker.is_alive())
                saved = json.loads(request(path)[2])
                self.assertEqual(saved['progress']['completed'], 4)
                self.assertEqual(len(json.loads(request('/api/platform/batches')[2])), 1)
                exp_id = saved['records'][0]['experiment_id']
                result = json.loads(request(path + '/results/' + exp_id)[2])
                self.assertEqual(result['provenance']['result_id'], saved['records'][0]['result_id'])
                _, headers, report = request(path + '/report')
                self.assertIn('attachment', headers['Content-Disposition'])
                self.assertIn('完成 4/4', report.decode())
                self.assertEqual(request('/batch-view.js')[0], 200)
                with self.assertRaises(HTTPError) as caught:
                    request('/api/platform/batches/' + '0' * 32 + '/run', {})
                self.assertEqual(caught.exception.code, 404)
            finally:
                server.shutdown(); server.server_close(); thread.join(5)
