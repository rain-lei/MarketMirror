"""Resume the frozen resource study using durable actual per-condition exits."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .resource_unit_checkpoint_execution import (
    ROOT, EXECUTION_CONFIG, load_execution, new_json, read, require, sha, confined, verify_job)


def now():
    return datetime.now(timezone.utc).isoformat()


def configure_imports():
    for path in read(EXECUTION_CONFIG)['fallback_import_paths']:
        if path not in sys.path:
            sys.path.append(path)


def command(module, arguments):
    bootstrap = ('import sys,runpy;sys.path.extend(' + repr(read(EXECUTION_CONFIG)['fallback_import_paths'])
                 + ');module=sys.argv.pop(1);runpy.run_module(module,run_name="__main__")')
    return [sys.executable, '-B', '-X', 'utf8', '-c', bootstrap, module, *arguments]


def inactive(owner):
    from ..data_pipeline.temporal_transport_sources import windows_process_identity
    actual = windows_process_identity(owner['pid'])
    return actual is None or not actual['live'] or actual['creation_ticks'] != owner['creation_ticks']


def receipt_row(path, value):
    return {**value, 'receipt_path': path.relative_to(ROOT).as_posix(), 'receipt_sha256': sha(path)}


class Runner:
    def __init__(self, execution, cfg):
        self.execution, self.cfg = execution, cfg
        self.folder = confined(ROOT / execution['jobs_directory'])
        self.folder.mkdir(parents=True, exist_ok=True)
        self.recipe_sha = sha(EXECUTION_CONFIG)

    def task_folder(self, task):
        name = task['operation'].replace('-', '_')
        if task.get('cell') is not None:
            name += f"_{task['cell']:03}"
        return self.folder / f"seed_{task['seed']}" / name

    def completed_or_missing(self, task, folder):
        recovery = None
        for attempt in sorted(folder.glob('attempt_*')):
            final, started = attempt / 'receipt.json', attempt / 'started.json'
            if final.exists():
                saved = read(final)
                job, _ = verify_job(final, self.execution, saved['operation'], task['seed'], task.get('cell'))
                require(saved['operation'] == task['operation'] or
                        (task['operation'] == 'produce-cell' and saved['operation'] == 'verify-produced-cell'),
                        'Checkpoint task operation changed')
                return receipt_row(final, job), None
            require(started.exists(), 'Interrupted attempt has no attributable process; inspect before resuming')
            old = read(started)
            require(old['execution_protocol_sha256'] == self.recipe_sha, 'Previous checkpoint recipe changed')
            require(inactive(old['owner']), 'Previous checkpoint worker is still live; keep waiting for its actual handle')
            observation = attempt / 'missing_exit_observation.json'
            if not observation.exists():
                values = {'status': 'CHECKPOINT_WORKER_MISSING_EXIT_UNOBSERVED',
                          'execution_protocol_sha256': self.recipe_sha,
                          'started_path': started.relative_to(ROOT).as_posix(), 'started_sha256': sha(started),
                          'observed_at_utc': now(), 'original_exit_code': None, 'old_started_record_preserved': True}
                if task['operation'] == 'produce-cell' and old['operation'] == 'produce-cell':
                    target = ROOT / self.execution['scientific_output_directory'] / f"seed_{task['seed']}/cell_{task['cell']}/checkpoint.json"
                    if target.exists():
                        values['sealed_checkpoint_sha256'] = sha(target)
                new_json(observation, values)
            require(read(observation)['started_sha256'] == sha(started), 'Missing worker evidence changed')
            if read(observation).get('sealed_checkpoint_sha256'):
                recovery = observation
        return None, recovery

    def launch(self, task):
        from ..data_pipeline.temporal_transport_sources import windows_process_identity
        folder = self.task_folder(task)
        folder.mkdir(parents=True, exist_ok=True)
        done, recovery = self.completed_or_missing(task, folder)
        if done is not None:
            return done
        attempt = folder / f"attempt_{len(list(folder.glob('attempt_*'))):03}"
        attempt.mkdir()
        result, log = attempt / 'result.json', attempt / 'log.txt'
        operation = 'verify-produced-cell' if recovery is not None else task['operation']
        args = ['--operation', operation, '--seed', str(task['seed']), '--result-file', str(result)]
        if task.get('cell') is not None:
            args += ['--cell', str(task['cell'])]
        if task.get('inventory') is not None:
            args += ['--inventory', str(task['inventory'])]
        if recovery is not None:
            args += ['--recovery-observation', str(recovery)]
        cmd = command('research.simulation.resource_unit_checkpoint_execution', args)
        stream = log.open('xb')
        child = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream,
                                 stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        owner = windows_process_identity(child.pid)
        require(owner is not None, 'Actual checkpoint process identity unavailable')
        started = {'status': 'RUNNING_CHECKPOINT_JOB_EXIT_UNOBSERVED', 'operation': operation,
                   'seed_index': task['seed'], 'cell_index': task.get('cell'), 'owner': owner,
                   'actual_exit_code': None, 'observed_process_exit_code': None,
                   'execution_protocol_sha256': self.recipe_sha,
                   'original_protocol_sha256': self.execution['original_protocol_sha256'],
                   'started_at_utc': now(), 'command': cmd,
                   'log_path': log.relative_to(ROOT).as_posix(), 'result_path': result.relative_to(ROOT).as_posix()}
        new_json(attempt / 'started.json', started)
        return {'child': child, 'stream': stream, 'attempt': attempt, 'started': started}

    def finish(self, running):
        code = running['child'].wait()
        running['stream'].close()
        attempt, started = running['attempt'], running['started']
        result, log = attempt / 'result.json', attempt / 'log.txt'
        passed = code == 0 and result.exists()
        saved = {**started, 'status': 'PASS_ACTUAL_CHECKPOINT_JOB' if passed else 'FAIL_ACTUAL_CHECKPOINT_JOB',
                 'actual_exit_code': code, 'observed_process_exit_code': code, 'finished_at_utc': now(),
                 'log_sha256': sha(log), 'result_sha256': sha(result) if result.exists() else None}
        receipt = attempt / 'receipt.json'
        new_json(receipt, saved)
        if passed:
            verify_job(receipt, self.execution, saved['operation'], saved['seed_index'], saved['cell_index'])
        print(f"Seed {saved['seed_index']} {saved['operation']} cell {saved['cell_index']}: actual exit {code}.", flush=True)
        return receipt_row(receipt, saved)

    def tasks(self, tasks, concurrency):
        queue, live, rows = iter(tasks), [], []
        exhausted, failures = False, []
        while not exhausted or live:
            while not exhausted and len(live) < concurrency:
                task = next(queue, None)
                if task is None:
                    exhausted = True
                    break
                job = self.launch(task)
                if 'child' in job:
                    live.append(job)
                else:
                    rows.append(job)
            finished = [j for j in live if j['child'].poll() is not None]
            for job in finished:
                row = self.finish(job)
                rows.append(row)
                live.remove(job)
                if row['status'] != 'PASS_ACTUAL_CHECKPOINT_JOB':
                    failures.append(row['receipt_path'])
                    exhausted = True
            if live and not finished:
                time.sleep(0.5)
        require(not failures, 'Actual failed checkpoint jobs preserved: ' + repr(failures))
        return sorted(rows, key=lambda row: -1 if row['cell_index'] is None else row['cell_index'])

    def inventory(self, seed, phase, rows):
        path = self.folder / f'seed_{seed}/{phase}_inventory.json'
        value = {'execution_protocol_sha256': self.recipe_sha, 'seed_index': seed,
                 'jobs': [{k: r[k] for k in ('cell_index', 'operation', 'receipt_path', 'receipt_sha256')} for r in rows]}
        if path.exists():
            require(read(path) == value, 'Preserved complete inventory differs')
        else:
            new_json(path, value)
        return path

    def claim(self):
        from ..data_pipeline.temporal_transport_sources import windows_process_identity
        for prior in sorted(self.folder.glob('owner_*.json')):
            require(inactive(read(prior)['owner']), 'The same resource checkpoint runner is still live')
        owner = self.folder / f"owner_{len(list(self.folder.glob('owner_*.json'))):03}.json"
        new_json(owner, {'status': 'RUNNING_CHECKPOINT_ORCHESTRATOR', 'owner': windows_process_identity(os.getpid()),
                         'execution_protocol_sha256': self.recipe_sha, 'started_at_utc': now()})
        return owner

    def external(self, phase, module, output):
        from ..data_pipeline.temporal_transport_sources import windows_process_identity
        folder = self.folder / phase
        folder.mkdir(exist_ok=True)
        receipt, log, start = folder / 'receipt.json', folder / 'log.txt', folder / 'started.json'
        if receipt.exists():
            saved = read(receipt)
            require(saved['actual_exit_code'] == 0 and sha(log) == saved['log_sha256']
                    and sha(output) == saved['output_sha256'], 'Preserved final actual execution differs')
            return receipt_row(receipt, saved)
        require(not start.exists() and not output.exists(), 'Inspect an interrupted final command before resuming')
        cmd = command(module, [])
        with log.open('xb') as stream:
            child = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream,
                                     stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            saved = {'phase': phase, 'owner': windows_process_identity(child.pid), 'command': cmd,
                     'execution_protocol_sha256': self.recipe_sha, 'actual_exit_code': None,
                     'started_at_utc': now(), 'log_path': log.relative_to(ROOT).as_posix(),
                     'output_path': output.relative_to(ROOT).as_posix()}
            new_json(start, saved)
            code = child.wait()
        saved.update(actual_exit_code=code, observed_process_exit_code=code, finished_at_utc=now(),
                     log_sha256=sha(log), output_sha256=sha(output) if output.exists() else None,
                     status='PASS_ACTUAL_ORIGINAL_FINAL_COMMAND' if code == 0 else 'FAIL_ACTUAL_ORIGINAL_FINAL_COMMAND')
        new_json(receipt, saved)
        require(code == 0 and output.exists(), 'Original full-scope final command failed')
        return receipt_row(receipt, saved)

    def run(self):
        from .temporal_resource_unit_study_v2 import OUTPUT
        owner = self.claim()
        for old in self.execution['interrupted_actual_processes']:
            require(sha(ROOT / old['receipt_path']) == old['receipt_sha256'] and inactive(old['owner']),
                    'Old interrupted process or unknown exit evidence changed')
        rows = []
        for old in self.execution['completed_seed_jobs']:
            path = ROOT / old['receipt_path']
            require(sha(path) == old['receipt_sha256'], 'Preserved actual seed job receipt changed')
            saved = read(path)
            require(type(saved['observed_process_exit_code']) is int and saved['observed_process_exit_code'] == 0
                    and sha(ROOT / saved['log_path']) == saved['log_sha256'], 'Preserved actual seed exit 0 required')
            rows.append(receipt_row(path, saved))
        for seed in (2, 3, 4):
            if seed in (3, 4):
                self.tasks([{'operation': 'prepare', 'seed': seed}], 1)
                reused = self.execution['interrupted_seed_observations'].get(str(seed), {}).get('sealed_condition_indices', [])
                production = self.tasks([{'operation': 'produce-cell', 'seed': seed, 'cell': cell}
                                         for cell in range(96) if cell not in reused], self.execution['producer_concurrency'])
                inv = self.inventory(seed, 'producer', production)
                complete = self.tasks([{'operation': 'complete-production', 'seed': seed, 'inventory': inv}], 1)[0]
                rows.append({**complete, 'phase': 'producer', 'execution_mode': 'FULL_SEED_FROM_ACTUAL_CONDITION_JOBS'})
            audits = self.tasks([{'operation': 'audit-cell', 'seed': seed, 'cell': cell} for cell in range(96)],
                                self.execution['audit_concurrency'])
            inv = self.inventory(seed, 'audit', audits)
            complete = self.tasks([{'operation': 'complete-audit', 'seed': seed, 'inventory': inv}], 1)[0]
            rows.append({**complete, 'phase': 'audit', 'execution_mode': 'FULL_SEED_FROM_ACTUAL_CONDITION_JOBS'})
        require(len(rows) == 10 and {(r['seed_index'], r['phase']) for r in rows}
                == {(i, p) for i in range(5) for p in ('producer', 'audit')}
                and all(r['observed_process_exit_code'] == 0 for r in rows), 'Ten full-scope actual seed jobs required')
        value = {'status': 'COMPLETE_FULL_RESOURCE_UNIT_CHILD_PROCESSES',
                 'protocol_sha256': self.execution['original_protocol_sha256'], 'jobs': rows,
                 'execution_protocol_sha256': self.recipe_sha,
                 'original_interrupted_exit_codes_unknown': True, 'scientific_protocols_changed': False,
                 'full_seed_completion_uses_actual_per_condition_receipts': True,
                 'max_concurrent_seed_pipelines': 1}
        aggregate = ROOT / self.cfg['jobs_receipt_path']
        if aggregate.exists():
            require(read(aggregate) == value, 'Preserved full resource jobs evidence differs')
        else:
            new_json(aggregate, value)
        finals = [self.external('finish', 'research.simulation.finish_temporal_resource_unit_study_v2', OUTPUT / 'results.json'),
                  self.external('final_audit', 'research.simulation.audit_temporal_resource_unit_summary_v2', OUTPUT / 'final_verification.json')]
        new_json(self.folder / 'completion.json', {'status': 'PASS_FULL_RESOURCE_STUDY_CHECKPOINTED_EXECUTION',
                 'execution_protocol_sha256': self.recipe_sha, 'jobs_receipt_sha256': sha(aggregate),
                 'owner_receipt_sha256': sha(owner), 'final_jobs': finals,
                 'original_unknown_exits_preserved': True, 'scientific_protocols_changed': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.parse_args()
    configure_imports()
    execution, cfg, _ = load_execution()
    Runner(execution, cfg).run()


if __name__ == '__main__':
    main()
