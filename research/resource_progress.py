"""Read actual resource execution receipts without rerunning market simulations."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
import sys

from .simulation.resource_unit_checkpoint_execution import (
    ROOT, EXECUTION_CONFIG, read, sha, require, verify_job)


def process_state(owner, identity):
    actual = identity(owner['pid']) if identity is not None else None
    if identity is None:
        return {'status': 'NATIVE_PROCESS_OBSERVATION_UNAVAILABLE'}
    if actual is None:
        return {'status': 'PROCESS_MISSING_EXIT_UNOBSERVED', 'pid': owner['pid']}
    if actual['creation_ticks'] != owner['creation_ticks']:
        return {'status': 'RECORDED_PROCESS_MISSING_PID_REUSED', 'pid': owner['pid']}
    return {'status': 'LIVE' if actual['live'] else 'PROCESS_TERMINAL_EXIT_UNOBSERVED', **actual}


def actual_legacy_job(row, protocol_sha):
    receipt = ROOT / row['receipt_path']
    require(sha(receipt) == row['receipt_sha256'], 'Preserved actual seed receipt changed')
    job = read(receipt)
    require(type(job['observed_process_exit_code']) is int and job['observed_process_exit_code'] == 0
            and job['protocol_sha256'] == protocol_sha and sha(ROOT / job['log_path']) == job['log_sha256'],
            'Preserved successful seed process evidence differs')
    return job


def progress():
    execution = read(EXECUTION_CONFIG)
    recipe_sha = sha(EXECUTION_CONFIG)
    protocol = ROOT / 'research/configs/temporal_resource_unit_study_2022_v2.json'
    cfg = read(protocol)
    require(sha(protocol) == execution['original_protocol_sha256'], 'Original scientific protocol changed')
    for relative, expected in execution['bindings'].items():
        require(sha(ROOT / relative) == expected, 'Recovery execution binding changed: ' + relative)
    identity = None
    if os.name == 'nt':
        for path in execution['fallback_import_paths']:
            if path not in sys.path:
                sys.path.append(path)
        from .data_pipeline.temporal_transport_sources import windows_process_identity
        identity = windows_process_identity
    folder = ROOT / execution['jobs_directory']
    output = ROOT / execution['scientific_output_directory']
    counts, completed, failed, unobserved, owners = Counter(), {}, [], [], []
    successful_attempts = {}
    for row in execution['completed_seed_jobs']:
        job = actual_legacy_job(row, execution['original_protocol_sha256'])
        completed[job['seed_index'], job['phase']] = {'receipt_path': row['receipt_path'],
                                                   'receipt_sha256': row['receipt_sha256']}
    for path in sorted(folder.glob('seed_*/**/receipt.json')):
        job = read(path)
        require(job['execution_protocol_sha256'] == recipe_sha, 'Checkpoint process recipe changed')
        if type(job['actual_exit_code']) is not int or job['actual_exit_code'] != 0:
            failed.append({'receipt_path': path.relative_to(ROOT).as_posix(), 'actual_exit_code': job['actual_exit_code']})
            continue
        job, result = verify_job(path, execution, job['operation'], job['seed_index'], job['cell_index'])
        successful_attempts[path.parent.parent] = path
        seed, operation = job['seed_index'], job['operation']
        counts[seed, operation] += 1
        if operation in ('complete-production', 'complete-audit'):
            phase = 'producer' if operation == 'complete-production' else 'audit'
            artifact = output / f'seed_{seed}' / ('production_complete.json' if phase == 'producer' else 'independent.json')
            require(sha(artifact) == result['production_complete_sha256' if phase == 'producer' else 'audit_complete_sha256'],
                    'Full seed artifact changed after actual completion')
            completed[seed, phase] = {'receipt_path': path.relative_to(ROOT).as_posix(), 'receipt_sha256': sha(path)}
    for path in sorted(folder.glob('owner_*.json')):
        owner = read(path)
        require(owner['execution_protocol_sha256'] == recipe_sha, 'Resource owner recipe changed')
        owners.append({'owner_path': path.relative_to(ROOT).as_posix(), **process_state(owner['owner'], identity)})
    for path in sorted(folder.glob('seed_*/**/started.json')):
        if (path.parent / 'receipt.json').exists():
            continue
        job = read(path)
        require(job['execution_protocol_sha256'] == recipe_sha, 'Unfinished checkpoint recipe changed')
        native = process_state(job['owner'], identity)
        replacement = successful_attempts.get(path.parent.parent)
        observation = path.parent / 'missing_exit_observation.json'
        reconciled = False
        if replacement is not None and observation.exists() and native['status'] != 'LIVE':
            saved = read(observation)
            require(saved['status'] == 'CHECKPOINT_WORKER_MISSING_EXIT_UNOBSERVED'
                    and saved['execution_protocol_sha256'] == recipe_sha
                    and saved['started_sha256'] == sha(path) and saved['original_exit_code'] is None,
                    'Old unknown exit reconciliation evidence changed')
            reconciled = True
        unobserved.append({'operation': job['operation'], 'seed_index': job['seed_index'], 'cell_index': job['cell_index'],
                           'actual_exit_code': None, 'reconciled_by_verified_successful_attempt': reconciled,
                           'successful_replacement_receipt_path': replacement.relative_to(ROOT).as_posix() if reconciled else None,
                           **native})
    seeds = []
    for seed in range(5):
        seed_folder = output / f'seed_{seed}'
        for phase in ('producer', 'audit'):
            if (seed, phase) not in completed:
                continue
            artifact = read(seed_folder / ('production_complete.json' if phase == 'producer' else 'independent.json'))
            require(artifact['protocol_sha256'] == execution['original_protocol_sha256'], 'Full seed protocol differs')
            if phase == 'producer':
                require(artifact['status'] == 'COMPLETE_FULL_RESOURCE_UNIT_SEED' and len(artifact['checkpoints']) == 97,
                        'Full seed production scope differs')
            else:
                require(artifact['status'] == 'PASS_FULL_RESOURCE_UNIT_SEED_RAW_LEDGER_AND_STATISTICS'
                        and artifact['checks'] == cfg['per_seed_audit_scope'], 'Full seed independent scope differs')
        seeds.append({'seed_index': seed, 'sealed_conditions': len(list(seed_folder.glob('cell_*/checkpoint.json'))),
                      'required_conditions': 96, 'production_complete_with_actual_exit_zero': (seed, 'producer') in completed,
                      'audit_complete_with_actual_exit_zero': (seed, 'audit') in completed,
                      'actual_successful_new_condition_production_jobs': counts[seed, 'produce-cell'],
                      'actual_successful_recovered_condition_verification_jobs': counts[seed, 'verify-produced-cell'],
                      'actual_successful_condition_audit_jobs': counts[seed, 'audit-cell']})
    final_complete = False
    completion_path = folder / 'completion.json'
    if completion_path.exists():
        completion = read(completion_path)
        require(completion['status'] == 'PASS_FULL_RESOURCE_STUDY_CHECKPOINTED_EXECUTION'
                and completion['execution_protocol_sha256'] == recipe_sha and len(completed) == 10,
                'Complete resource execution evidence differs')
        require(sha(ROOT / cfg['jobs_receipt_path']) == completion['jobs_receipt_sha256'], 'Final seed jobs evidence changed')
        require(len(completion['final_jobs']) == 2, 'Both original full-scope final commands required')
        for job in completion['final_jobs']:
            require(type(job['actual_exit_code']) is int and job['actual_exit_code'] == 0
                    and sha(ROOT / job['receipt_path']) == job['receipt_sha256']
                    and sha(ROOT / job['log_path']) == job['log_sha256']
                    and sha(ROOT / job['output_path']) == job['output_sha256'], 'Final command actual exit or artifact changed')
        final = read(output / 'final_verification.json')
        require(final['unique_conditions'] == final['independently_rebuilt_statistical_conditions'] == final['resource_pairs'] == 720
                and final['strata'] == 36 and final['actual_successful_seed_jobs'] == 10
                and final['new_daily_ledger_records'] == 1141440 and final['new_account_audits'] == 944640
                and final['protocol_sha256'] == execution['original_protocol_sha256']
                and final['results_sha256'] == sha(output / 'results.json'), 'Full original final audit scope differs')
        require(not failed and all(j['reconciled_by_verified_successful_attempt'] for j in unobserved),
                'Failed or unreconciled checkpoint attempts still need attention')
        final_complete = True
    return {'status': 'COMPLETE_FULL_RESOURCE_EXECUTION_VERIFIED' if final_complete else 'RESOURCE_EXECUTION_INCOMPLETE',
            'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'original_protocol_sha256': sha(protocol),
            'execution_protocol_sha256': recipe_sha, 'execution_bindings_verified': len(execution['bindings']),
            'full_resource_scope': 720, 'complete_seed_phase_jobs_with_actual_exit_zero': len(completed),
            'seeds': seeds, 'owners': owners, 'unobserved_checkpoint_attempts': unobserved, 'failed_checkpoint_jobs': failed,
            'resource_execution_complete': final_complete,
            'original_raw_ledgers_reaudited_by_this_command': False, 'model_parameters_changed': False,
            'empirical_market_prediction_verified': False}


def main():
    print(json.dumps(progress(), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
