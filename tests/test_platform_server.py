import json
import tempfile
import unittest
from threading import Thread
from urllib.request import urlopen
from unittest.mock import patch
from pathlib import Path

from design.server import PlatformStore, RunInProgress, atomic_json, create_server, preview_run, validate_experiment


class PlatformStoreTest(unittest.TestCase):
    def test_exclusive_server_and_restart_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            store = PlatformStore(directory)
            record = store.create(self.payload())
            path = directory / record['id'] / 'experiment.json'
            first = create_server(directory, 0)
            try:
                atomic_json(path, {**record, 'run_status': 'running', 'run_attempts': 1})
                with self.assertRaises(RuntimeError):
                    create_server(directory, 0)
                self.assertEqual(store.get(record['id'])['run_status'], 'running')
            finally:
                first.server_close()
            restarted = create_server(directory, 0)
            try:
                recovered = store.get(record['id'])
                self.assertEqual(recovered['run_status'], 'interrupted')
                self.assertEqual(recovered['run_attempts'], 1)
                self.assertEqual(recovered['strategy_parameters_sha256'], record['strategy_parameters_sha256'])
                self.assertEqual(recovered['run_error']['type'], 'ServiceInterrupted')
                self.assertTrue(store.run(record['id'])['audit']['passed'])
                self.assertEqual(store.get(record['id'])['run_attempts'], 2)
            finally:
                restarted.server_close()

    def payload(self):
        return {"title": "测试实验", "source": "这是一段足够长的政策消息，用于验证本地平台输入和保存流程。", "type": "政策消息",
                "published_at": "2026-10-05T09:00", "signal": .6, "uncertainty": .2, "duration": 6,
                "sessions": 18, "seed": 7, "cash": 1_000_000}

    def test_startup_recovery_skips_corrupt_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            directory.joinpath('bad-record').mkdir()
            directory.joinpath('bad-record', 'experiment.json').write_text('{not-json', encoding='utf-8')
            store = PlatformStore(directory)
            store.recover_interrupted()
            self.assertEqual(store.list()[0]['run_status'], 'corrupt')

    def test_non_object_records_do_not_prevent_real_server_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for index, value in enumerate((None, [], 7, {}, {'created_at': 42})):
                atomic_json(directory / str(index) / 'experiment.json', value)
            server = create_server(directory, 0)
            try:
                rows = PlatformStore(directory).list()
                self.assertEqual(len(rows), 5)
                self.assertTrue(all(row['run_status'] == 'corrupt' for row in rows))
            finally:
                server.server_close()

    def test_list_exposes_corrupt_record_tombstone(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            directory.joinpath('bad-record').mkdir()
            directory.joinpath('bad-record', 'experiment.json').write_text('{not-json', encoding='utf-8')
            rows = PlatformStore(directory).list()
            self.assertEqual(rows[0]['id'], 'bad-record')
            self.assertEqual(rows[0]['run_status'], 'corrupt')
            self.assertIn('损坏', rows[0]['title'])

    def test_validate_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PlatformStore(Path(tmp)); record = store.create(self.payload())
            self.assertEqual(store.list()[0]["title"], "测试实验")
            result = store.run(record["id"])
            self.assertEqual(result["mode"], "synthetic_market")
            self.assertTrue(result['audit']['passed'])
            self.assertEqual(len(result['paths']['with_message']['summary']['role_wealth_multiple']), 3)
            self.assertEqual(store.get(record["id"])["run_status"], "completed")

    def test_validation_rejects_private_or_unknown_fields(self):
        with self.assertRaises(ValueError): validate_experiment({**self.payload(), "unknown": 1})
        with self.assertRaises(ValueError): validate_experiment({**self.payload(), "signal": 3})
        with self.assertRaises(ValueError): validate_experiment({**self.payload(), "source": "太短"})

    def test_failed_run_is_recorded_and_retry_preserves_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PlatformStore(Path(tmp))
            record = store.create(self.payload())
            def fail(_record):
                self.assertEqual(store.get(record['id'])['run_status'], 'running')
                with self.assertRaises(RunInProgress):
                    store.run(record['id'])
                self.assertEqual(store.get(record['id'])['run_attempts'], 1)
                self.assertTrue(store.strategies()['parameters'])
                raise RuntimeError('internal detail should not be persisted')
            with patch('design.server.run_market', side_effect=fail), self.assertRaises(RuntimeError):
                store.run(record['id'])
            failed = store.get(record['id'])
            self.assertEqual(failed['run_status'], 'failed')
            self.assertEqual(failed['run_attempts'], 1)
            self.assertNotIn('internal detail', json.dumps(failed))
            self.assertIsNone(store.result(record['id']))
            result = store.run(record['id'])
            retried = store.get(record['id'])
            self.assertEqual(retried['run_attempts'], 2)
            self.assertEqual(retried['run_status'], 'completed')
            self.assertIsNone(retried['run_error'])
            self.assertEqual(retried['strategy_parameters_sha256'], record['strategy_parameters_sha256'])
            with patch('design.server.run_market', side_effect=RuntimeError('failed')), self.assertRaises(RuntimeError):
                store.run(record['id'])
            self.assertEqual(store.get(record['id'])['run_status'], 'failed')
            self.assertEqual(store.result(record['id']), result)

    def test_failed_publication_keeps_previous_result_and_export_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PlatformStore(Path(tmp))
            record = store.create(self.payload())
            first_result = {'mode': 'synthetic_market', 'attempt': 1}
            with patch('design.server.run_market', return_value=first_result):
                previous = store.run(record['id'])
            previous_id = store.get(record['id'])['result_id']

            def fail_commit(path, value):
                if path.name == 'experiment.json' and value.get('run_status') == 'completed':
                    raise OSError('simulated publication failure')
                atomic_json(path, value)

            with patch('design.server.run_market', return_value={'mode': 'synthetic_market', 'attempt': 2}), \
                    patch('design.server.atomic_json', side_effect=fail_commit), self.assertRaises(OSError):
                store.run(record['id'])
            exported = store.export(record['id'])['experiment']
            self.assertEqual(exported['run_status'], 'failed')
            self.assertEqual(exported['result_id'], previous_id)
            self.assertEqual(exported['backendResult'], previous)
            self.assertEqual(PlatformStore(Path(tmp)).result(record['id']), previous)
            with patch('design.server.run_market', return_value={'mode': 'synthetic_market', 'attempt': 3}):
                store.run(record['id'])
            self.assertEqual(store.export(record['id'])['experiment']['backendResult']['attempt'], 3)

    def test_legacy_result_remains_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PlatformStore(Path(tmp))
            record = store.create(self.payload())
            legacy = {'mode': 'synthetic_market', 'legacy': True}
            atomic_json(Path(tmp) / record['id'] / 'result.json', legacy)
            self.assertEqual(store.export(record['id'])['experiment']['backendResult'], legacy)

    def test_preview_is_explicitly_not_scientific(self):
        result = preview_run(validate_experiment(self.payload()))
        self.assertEqual(result["mode"], "platform_preview")
        self.assertTrue(result["limitations"])
        self.assertEqual([r["name"] for r in result["roles"]], ["激进型", "保守型", "机构型"])

    def test_invalid_parameters_are_not_silently_coerced(self):
        invalid = {"seed": [7.8, True, "7", None], "sessions": [18.5, False],
                   "duration": [6.5], "signal": [float("nan"), float("inf"), True],
                   "cash": [float("inf"), None], "type": [[]],
                   "published_at": ["notTaDate", "2026-02-30T09:00"]}
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_experiment({**self.payload(), field: value})

    def test_history_survives_store_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PlatformStore(Path(tmp))
            record = store.create(self.payload())
            result = store.run(record["id"])
            reopened = PlatformStore(Path(tmp))
            self.assertEqual(reopened.get(record["id"])["seed"], 7)
            self.assertEqual(reopened.result(record["id"]), result)
            self.assertEqual(reopened.list()[0]["run_status"], "completed")

    def test_comparison_export_is_a_read_only_download_of_both_result_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            store = PlatformStore(directory)
            left = store.create(self.payload(), "a" * 32)
            right_payload = {**self.payload(), "title": "比较实验"}
            right = store.create(right_payload, "b" * 32)
            store.run(left["id"])
            store.run(right["id"])
            server = create_server(directory, 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/platform/comparisons/{left['id']}/{right['id']}/export"
                with urlopen(url) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertIn("marketmirror-compare-aaaaaaaa-bbbbbbbb-raw.json", response.headers["Content-Disposition"])
                self.assertEqual(payload["schema_version"], "platform-comparison-raw-v1")
                self.assertEqual(payload["left"]["id"], left["id"])
                self.assertEqual(payload["right"]["id"], right["id"])
                self.assertEqual(payload["left"]["result_id"], store.get(left["id"])["result_id"])
                self.assertEqual(payload["right"]["result_id"], store.get(right["id"])["result_id"])
                with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
                    self.assertIn('src="portfolio-metrics.js"', response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/portfolio-metrics.js") as response:
                    self.assertIn("text/javascript", response.headers["Content-Type"])
                    self.assertIn("buildOverview", response.read().decode("utf-8"))
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


if __name__ == "__main__": unittest.main()
