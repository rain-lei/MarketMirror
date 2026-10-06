import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from design.scenario_batch import run_batch, summarize_scenarios
from design.server import PlatformStore


class ScenarioBatchTest(unittest.TestCase):
    def test_failure_keeps_completed_run_and_marks_failed_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'batch'
            original = PlatformStore.run
            count = 0
            def run(store, experiment_id):
                nonlocal count
                count += 1
                if count == 2:
                    raise RuntimeError('simulated')
                return original(store, experiment_id)
            with patch.object(PlatformStore, 'run', run), self.assertRaises(RuntimeError):
                run_batch(output, seeds=(7,), sessions=1)
            saved = json.loads((output / 'batch.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['status'], 'failed')
            self.assertEqual([r['status'] for r in saved['records']], ['completed', 'failed'])
            self.assertTrue(saved['records'][0]['audit']['passed'])
            self.assertNotIn('paths_sha256', saved['records'][1])
            before = (output / 'batch.json').read_bytes()
            with self.assertRaises(FileExistsError):
                run_batch(output)
            self.assertEqual((output / 'batch.json').read_bytes(), before)

    def test_invalid_batch_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'batch'
            for seeds, sessions in [((1, 1), 18), ((True,), 18), ((7,), 0), ((), 18)]:
                with self.assertRaises(ValueError):
                    run_batch(output, seeds=seeds, sessions=sessions)
                self.assertFalse(output.exists())


class ScenarioSummaryTest(unittest.TestCase):
    def test_failed_samples_are_missing_not_zero(self):
        def row(value):
            return {'scenario': 'positive', 'status': 'completed',
                    'role_return_difference_pp': dict.fromkeys(('aggressive', 'conservative', 'institutional'), value)}
        manifest = {'seeds': [1, 7, 19], 'scenarios': [('positive', .6, .2), ('neutral', 0, 0)],
                    'records': [row(-2), row(4), {'scenario': 'positive', 'status': 'failed'}]}
        summary = summarize_scenarios(manifest)
        self.assertEqual(summary[0]['mean_pp'], 1)
        self.assertEqual(summary[0]['min_pp'], -2)
        self.assertEqual(summary[0]['max_pp'], 4)
        self.assertEqual(summary[0]['completed'], 2)
        self.assertEqual(summary[0]['planned'], 3)
        self.assertIsNone(summary[3]['mean_pp'])
