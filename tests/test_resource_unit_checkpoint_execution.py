"""Execution recovery must not weaken the registered scientific scope."""
import ast
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys

from research.simulation import resource_unit_checkpoint_execution as execution
from research.simulation import resource_unit_checkpoint_runner as runner


class ResourceCheckpointExecutionTests(unittest.TestCase):
    def test_all_scientific_statements_in_condition_loop_are_preserved(self):
        source = execution.ORIGINAL_AUDITOR.read_text(encoding='utf-8')
        original = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'audit_seed')
        module, _ = execution.audit_function(source)
        old_loop = next(n for n in original.body if isinstance(n, ast.For))
        new_loop = next(n for n in module.body[0].body if isinstance(n, ast.For))
        self.assertEqual(ast.dump(old_loop, include_attributes=False), ast.dump(new_loop, include_attributes=False))
        old_prefix = original.body[:original.body.index(old_loop)]
        old_prefix = [n for n in old_prefix if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                      and ast.unparse(n.value).startswith('require(not target.exists(),'))]
        self.assertEqual([ast.dump(n, include_attributes=False) for n in old_prefix],
                         [ast.dump(n, include_attributes=False) for n in module.body[0].body[:len(old_prefix)]])

    def test_changing_original_audit_boundary_is_rejected(self):
        source = execution.ORIGINAL_AUDITOR.read_text(encoding='utf-8')
        source = source.replace('    bindings_ok(cfg)\n    with target.open', '    pass\n    with target.open')
        with self.assertRaisesRegex(ValueError, 'aggregation boundary'):
            execution.audit_function(source)

    def test_source_preflight_counted_once_not_once_per_condition(self):
        cfg = execution.read(execution.ROOT / 'research/configs/temporal_resource_unit_study_2022_v2.json')
        base = {k: v for k, v in cfg['per_seed_audit_scope'].items()
                if k.startswith('independently_') and k != 'independently_reconstructed_initial_resource_accounts'}
        base.update(suspended_asset_calls=0, unknown_target_asset_calls_preserved=0)
        delta = {k: v // 96 for k, v in cfg['per_seed_audit_scope'].items() if k not in base}
        single = Counter(base)
        single.update(delta)
        actual_delta = execution.segment_counts(dict(single), base)
        total = Counter(base)
        for _ in range(96):
            total.update(actual_delta)
        self.assertEqual(dict(total), cfg['per_seed_audit_scope'])
        self.assertNotEqual({k: v * 96 for k, v in single.items()}, cfg['per_seed_audit_scope'])

    def test_invalid_or_decreasing_scope_counts_are_rejected(self):
        for checks, preflight in [({'paths': -1}, {}), ({'paths': True}, {}),
                                  ({'paths': 40}, {'paths': 41}), ({'paths': 41}, {'paths': 0.5})]:
            with self.subTest(checks=checks, preflight=preflight), self.assertRaises(ValueError):
                execution.segment_counts(checks, preflight)

    def test_archived_numeric_source_keys_compare_without_changing_native_encoding(self):
        raw = {'distribution': {8: 3, 10: 1, 20: 43}}
        archived = json.loads(json.dumps(raw))
        self.assertNotEqual(raw, archived)
        self.assertEqual(execution.json_value(raw), archived)
        self.assertEqual(list(raw['distribution']), [8, 10, 20])
        self.assertNotEqual(json.dumps(raw, sort_keys=True), json.dumps(archived, sort_keys=True))

    def make_job(self, folder):
        root = Path(folder)
        recipe, log, result, receipt = (root / name for name in ('recipe.json', 'log.txt', 'result.json', 'receipt.json'))
        recipe.write_text('{}', encoding='utf-8')
        log.write_text('all 41 baskets checked', encoding='utf-8')
        value = {'status': 'FUNCTION_COMPLETE_PENDING_ACTUAL_PROCESS_EXIT', 'operation': 'audit-cell',
                 'seed_index': 2, 'cell_index': 7, 'execution_protocol_sha256': execution.sha(recipe),
                 'original_protocol_sha256': 'original'}
        result.write_text(json.dumps(value), encoding='utf-8')
        job = {**value, 'status': 'PASS_ACTUAL_CHECKPOINT_JOB', 'actual_exit_code': 0,
               'log_path': 'log.txt', 'log_sha256': execution.sha(log),
               'result_path': 'result.json', 'result_sha256': execution.sha(result)}
        receipt.write_text(json.dumps(job), encoding='utf-8')
        return root, recipe, log, result, receipt, job

    def test_unknown_failed_boolean_exits_and_changed_artifacts_cannot_pass(self):
        for invalid_exit in (None, 1, True):
            with tempfile.TemporaryDirectory() as folder:
                root, recipe, _, _, receipt, job = self.make_job(folder)
                job['actual_exit_code'] = invalid_exit
                receipt.write_text(json.dumps(job), encoding='utf-8')
                with patch.object(execution, 'ROOT', root), patch.object(execution, 'EXECUTION_CONFIG', recipe):
                    with self.assertRaisesRegex(ValueError, 'Actual successful'):
                        execution.verify_job(receipt, {'original_protocol_sha256': 'original'}, 'audit-cell', 2, 7)
        for changed in ('log', 'result'):
            with tempfile.TemporaryDirectory() as folder:
                root, recipe, log, result, receipt, _ = self.make_job(folder)
                (log if changed == 'log' else result).write_text('changed', encoding='utf-8')
                with patch.object(execution, 'ROOT', root), patch.object(execution, 'EXECUTION_CONFIG', recipe):
                    with self.assertRaisesRegex(ValueError, 'changed'):
                        execution.verify_job(receipt, {'original_protocol_sha256': 'original'}, 'audit-cell', 2, 7)

    def test_missing_duplicate_or_wrong_condition_inventories_are_rejected(self):
        for indices in (list(range(95)), list(range(95)) + [94], list(range(95)) + [96]):
            with tempfile.TemporaryDirectory() as folder:
                root, recipe, _, _, _, _ = self.make_job(folder)
                inventory = root / 'inventory.json'
                inventory.write_text(json.dumps({'execution_protocol_sha256': execution.sha(recipe),
                                     'jobs': [{'cell_index': i} for i in indices]}), encoding='utf-8')
                with patch.object(execution, 'ROOT', root), patch.object(execution, 'EXECUTION_CONFIG', recipe):
                    with self.assertRaisesRegex(ValueError, 'All 96'):
                        execution.job_inventory(inventory, 'audit-cell', 2, {})

    def test_live_worker_cannot_be_restarted_from_a_running_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            attempt = Path(folder) / 'attempt_000'
            attempt.mkdir()
            start = attempt / 'started.json'
            execution.new_json(start, {'execution_protocol_sha256': 'recipe', 'owner': {'pid': 123, 'creation_ticks': 4},
                                     'operation': 'audit-cell', 'actual_exit_code': None})
            value = object.__new__(runner.Runner)
            value.recipe_sha, value.execution = 'recipe', {}
            before = start.read_bytes()
            with patch.object(runner, 'inactive', return_value=False), self.assertRaisesRegex(ValueError, 'still live'):
                value.completed_or_missing({'operation': 'audit-cell', 'seed': 2, 'cell': 0}, Path(folder))
            self.assertEqual(start.read_bytes(), before)
            self.assertFalse((attempt / 'missing_exit_observation.json').exists())

    def test_missing_worker_preserves_unknown_exit_and_immutable_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            attempt = root / 'attempt_000'
            attempt.mkdir()
            start = attempt / 'started.json'
            execution.new_json(start, {'execution_protocol_sha256': 'recipe', 'owner': {'pid': 123, 'creation_ticks': 4},
                                     'operation': 'audit-cell', 'actual_exit_code': None})
            value = object.__new__(runner.Runner)
            value.recipe_sha, value.execution = 'recipe', {}
            before = start.read_bytes()
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'inactive', return_value=True):
                task = {'operation': 'audit-cell', 'seed': 2, 'cell': 0}
                self.assertEqual(value.completed_or_missing(task, root), (None, None))
                observation = attempt / 'missing_exit_observation.json'
                observed = observation.read_bytes()
                self.assertIsNone(execution.read(observation)['original_exit_code'])
                self.assertEqual(value.completed_or_missing(task, root), (None, None))
                self.assertEqual(observation.read_bytes(), observed)
            self.assertEqual(start.read_bytes(), before)
            self.assertFalse((attempt / 'receipt.json').exists())

    def test_actual_failure_stops_new_jobs_and_drains_existing_children(self):
        class ActualChildren:
            tasks = runner.Runner.tasks

            def __init__(self):
                self.launched, self.exits = [], {}

            def launch(self, task):
                cell = task['cell']
                self.launched.append(cell)
                script = f'import time,sys;time.sleep({0.05 if cell == 0 else 0.2});sys.exit({1 if cell == 0 else 0})'
                child = subprocess.Popen([sys.executable, '-B', '-c', script], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                return {'child': child, 'cell': cell}

            def finish(self, job):
                code = job['child'].wait()
                self.exits[job['cell']] = code
                return {'status': 'PASS_ACTUAL_CHECKPOINT_JOB' if code == 0 else 'FAIL_ACTUAL_CHECKPOINT_JOB',
                        'receipt_path': str(job['cell']), 'cell_index': job['cell']}

        value = ActualChildren()
        with self.assertRaisesRegex(ValueError, 'failed checkpoint'):
            value.tasks([{'cell': i} for i in range(4)], 2)
        self.assertEqual(value.launched, [0, 1])
        self.assertEqual(value.exits, {0: 1, 1: 0})


if __name__ == '__main__':
    unittest.main()
