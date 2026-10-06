import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from design.server import PlatformStore, create_server


def payload():
    return {'title': '可恢复的提交', 'source': '公司回复：项目仍在推进审批，尚未取得批复，具体投产时间尚不确定。',
            'type': '公司问答', 'published_at': '2026-10-05T09:00', 'signal': 0,
            'uncertainty': .2, 'duration': 6, 'sessions': 6, 'seed': 7, 'cash': 1_000_000}


class SubmissionStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.store = PlatformStore(self.directory)
        self.key = 'a' * 32

    def test_retry_after_restart_returns_completed_record_without_reset(self):
        record = self.store.create(payload(), self.key)
        result = self.store.run(record['id'])
        saved = self.store.get(record['id'])
        reopened = PlatformStore(self.directory)
        self.assertEqual(reopened.create(payload(), self.key), saved)
        self.assertEqual(reopened.result(self.key), result)
        self.assertEqual(len(reopened.list()), 1)
        self.assertEqual(saved['run_attempts'], 1)
        self.assertEqual(len(list((self.directory / self.key / 'runs').glob('*.json'))), 1)

    def test_reusing_key_for_different_configuration_never_overwrites(self):
        saved = self.store.create(payload(), self.key)
        for changes in ({'source': payload()['source'] + '补充'}, {'signal': .1}, {'seed': 11},
                        {'published_at': '2026-10-05T10:00'}, {'analysis_id': 'b' * 32}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '另一份配置'):
                self.store.create({**payload(), **changes}, self.key)
        self.assertEqual(self.store.get(self.key), saved)
        self.assertEqual(len(self.store.list()), 1)

    def test_default_profile_changes_do_not_change_a_saved_submission(self):
        saved = self.store.create(payload(), self.key)
        changed = copy.deepcopy(saved['strategy_parameters'])
        changed['aggressive']['text_sensitivity'] = 1.2
        self.store.save_strategies(changed)
        self.assertEqual(self.store.create(payload(), self.key), saved)
        self.assertEqual(saved['strategy_parameters']['aggressive']['text_sensitivity'], .9)

    def test_simultaneous_save_retries_create_exactly_one_record(self):
        barrier = threading.Barrier(8)
        original = self.store.strategies

        def wait_for_all():
            profile = original()
            barrier.wait(timeout=10)
            return profile

        with patch.object(self.store, 'strategies', side_effect=wait_for_all), ThreadPoolExecutor(max_workers=8) as pool:
            records = list(pool.map(lambda _: self.store.create(payload(), self.key), range(8)))
        self.assertTrue(all(record == records[0] for record in records))
        self.assertEqual(len(self.store.list()), 1)

    def test_invalid_keys_are_rejected_without_any_created_record(self):
        for key in ('', '../outside', 'A' * 32, 'a' * 31, [], 7):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.create(payload(), key)
        self.assertEqual(self.store.list(), [])


class SubmissionHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = create_server(Path(self.tmp.name), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()

    def create(self, value, key=None):
        headers = {'Content-Type': 'application/json'}
        if key is not None:
            headers['Idempotency-Key'] = key
        request = Request(self.base + '/api/platform/experiments',
                          data=json.dumps(value, ensure_ascii=False).encode('utf-8'), headers=headers)
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def test_header_retries_return_same_saved_record_and_reject_conflicts(self):
        key = 'c' * 32
        first = self.create(payload(), key)
        self.assertEqual(first['id'], key)
        self.assertEqual(self.create(payload(), key), first)
        with self.assertRaises(HTTPError) as failure:
            self.create({**payload(), 'seed': 11}, key)
        self.assertEqual(failure.exception.code, 400)
        with urlopen(self.base + '/api/platform/experiments', timeout=10) as response:
            self.assertEqual(len(json.load(response)), 1)

    def test_existing_clients_without_header_still_create_independent_records(self):
        first = self.create(payload())
        second = self.create(payload())
        self.assertNotEqual(first['id'], second['id'])


if __name__ == '__main__':
    unittest.main()
