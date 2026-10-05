"""Run the unchanged resource producer/auditor in durable condition checkpoints."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]
EXECUTION_CONFIG = ROOT / 'research/configs/resource_unit_checkpoint_execution_2026_v1.json'
ORIGINAL_AUDITOR = ROOT / 'research/simulation/audit_temporal_resource_unit_study_v2.py'


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def json_value(value):
    """Compare archived JSON values while preserving native keys for final encoding."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def new_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        handle.write('\n')


def confined(path):
    path = Path(path).resolve()
    require(ROOT in path.parents, 'Execution artifact outside the repository')
    return path


def audit_function(source):
    """Keep the exact prefix and condition loop; change only selection and output."""
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'audit_seed')
    loop_index = next(i for i, n in enumerate(function.body) if isinstance(n, ast.For)
                      and ast.unparse(n.target) == '(ci, cell)' and ast.unparse(n.iter) == 'selected')
    prefix = function.body[:loop_index]
    guards = [n for n in prefix if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
              and ast.unparse(n.value).startswith('require(not target.exists(),')]
    require(len(guards) == 1, 'Original output-only guard changed')
    prefix = [n for n in prefix if n not in guards]
    loop = function.body[loop_index]
    suffix = function.body[loop_index + 1:]
    require(len(suffix) == 3 and ast.unparse(suffix[0]).startswith('require(dict(counts) == cfg[')
            and ast.unparse(suffix[1]) == 'bindings_ok(cfg)' and isinstance(suffix[2], ast.With),
            'Original audit aggregation boundary changed')
    original_loop_ast = ast.dump(loop, include_attributes=False)
    function.name = 'audit_single_condition'
    function.args.args.append(ast.arg(arg='cell_index'))
    function.args.defaults.append(ast.Constant(value=None))
    select = ast.parse("""
require(type(cell_index) is int and 0 <= cell_index < len(CELLS), 'Condition outside registered audit grid')
selected = [(cell_index, CELLS[cell_index])]
preflight_counts = dict(counts)
""").body
    returned = ast.parse("""
bindings_ok(cfg)
return {'checks': dict(counts), 'preflight_checks': preflight_counts,
        'source_qc': source_qc, 'condition_channel_coverage': coverage_by_cell, 'artifacts': bindings}
""").body
    function.body = prefix + select + [loop] + returned
    require(ast.dump(function.body[len(prefix) + len(select)], include_attributes=False) == original_loop_ast,
            'Scientific condition audit loop changed')
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    fingerprint = hashlib.sha256(ast.dump(loop, include_attributes=False).encode('utf-8')).hexdigest()
    return module, fingerprint


def load_execution():
    execution = read(EXECUTION_CONFIG)
    require(execution['schema_version'] == 'same-scientific-scope-condition-checkpoint-execution-v1',
            'Unexpected checkpoint execution version')
    require(execution['scientific_protocols_changed'] is False and execution['conditions_per_seed'] == 96,
            'Original full resource scope required')
    for relative, expected in execution['bindings'].items():
        require(sha(ROOT / relative) == expected, 'Checkpoint execution binding changed: ' + relative)
    from .temporal_resource_unit_study_v2 import CONFIG, load_study
    require(sha(CONFIG) == execution['original_protocol_sha256'], 'Original scientific protocol changed')
    cfg, *data = load_study()
    return execution, cfg, data


def segment_counts(checks, preflight):
    require(all(type(v) is int and v >= 0 for v in checks.values()), 'Invalid audit count')
    require(all(type(v) is int and v >= 0 for v in preflight.values()), 'Invalid preflight count')
    require(all(k in checks and checks[k] >= v for k, v in preflight.items()), 'Audit count fell below preflight')
    return {k: v - preflight.get(k, 0) for k, v in checks.items() if v - preflight.get(k, 0) != 0}


def verify_job(path, execution, operation, seed, cell=None):
    path = confined(path)
    job = read(path)
    require(job['status'] == 'PASS_ACTUAL_CHECKPOINT_JOB' and type(job['actual_exit_code']) is int
            and job['actual_exit_code'] == 0, 'Actual successful checkpoint exit required')
    require((job['operation'], job['seed_index'], job.get('cell_index')) == (operation, seed, cell),
            'Checkpoint job identity differs')
    require(job['execution_protocol_sha256'] == sha(EXECUTION_CONFIG), 'Execution recipe differs')
    require(sha(confined(ROOT / job['log_path'])) == job['log_sha256'], 'Checkpoint job log changed')
    result_path = confined(ROOT / job['result_path'])
    require(sha(result_path) == job['result_sha256'], 'Checkpoint result changed')
    result = read(result_path)
    require(result['status'] == 'FUNCTION_COMPLETE_PENDING_ACTUAL_PROCESS_EXIT', 'Complete function output required')
    require((result['operation'], result['seed_index'], result.get('cell_index')) == (operation, seed, cell),
            'Checkpoint function identity differs')
    require(result['original_protocol_sha256'] == execution['original_protocol_sha256']
            and result['execution_protocol_sha256'] == sha(EXECUTION_CONFIG), 'Function protocol differs')
    return job, result


def finish_payload(seed, checks, source_qc, coverage, artifacts, protocol_sha):
    return {'status': 'PASS_FULL_RESOURCE_UNIT_SEED_RAW_LEDGER_AND_STATISTICS', 'seed_id': seed,
            'protocol_sha256': protocol_sha, 'checks': checks, 'source_qc': source_qc,
            'condition_channel_coverage': coverage, 'artifacts': artifacts}


def prepared_inputs(seed_index, cfg, data):
    from .temporal_resource_unit_study_v2 import OUTPUT, CONFIG, seed_paths, checkpoint, read_gzip
    risk, factors, _, _ = data
    folder = OUTPUT / f'seed_{seed_index}'
    seed = cfg['seeds'][seed_index]
    core, background, paths = seed_paths(cfg, risk, factors, seed)
    checkpoint(folder / 'prepared', [seed['seed_id'], 'input_paths'], CONFIG)
    require(read_gzip(folder / 'prepared/issuer_paths.json.gz') == paths, 'Frozen prepared paths differ')
    return folder, seed, core, background, paths


def prepare_seed(seed_index, cfg, data, execution):
    from .temporal_resource_unit_study_v2 import OUTPUT, CONFIG, seed_paths, checkpoint, read_gzip, write_gzip, seal
    from ..data_pipeline.temporal_transport_sources import windows_process_identity
    import os
    risk, factors, _, _ = data
    seed = cfg['seeds'][seed_index]
    folder = OUTPUT / f'seed_{seed_index}'
    folder.mkdir(parents=True, exist_ok=True)
    claim = folder / 'producer_claim.json'
    if claim.exists():
        observed = execution['interrupted_seed_observations'].get(str(seed_index))
        if observed is not None:
            require(sha(claim) == observed['producer_claim_sha256'], 'Original interrupted producer claim changed')
        else:
            saved = read(claim)
            require(seed_index == 4 and saved['seed_id'] == seed['seed_id']
                    and saved['protocol_sha256'] == sha(CONFIG)
                    and saved['checkpoint_execution_protocol_sha256'] == sha(EXECUTION_CONFIG),
                    'Existing unregistered production claim')
        owner = read(claim)['owner']
        identity = windows_process_identity(owner['pid'])
        require(identity is None or not identity['live'] or identity['creation_ticks'] != owner['creation_ticks'],
                'Original resource preparation owner is still live')
    else:
        require(seed_index == 4, 'Only the originally unstarted seed may receive a fresh claim')
        with claim.open('xb') as handle:
            from .temporal_resource_unit_study_v2 import encoded
            handle.write(encoded({'status': 'RUNNING_CHECKPOINTED_EXECUTION',
                'owner': windows_process_identity(os.getpid()), 'seed_id': seed['seed_id'],
                'protocol_sha256': sha(CONFIG), 'checkpoint_execution_protocol_sha256': sha(EXECUTION_CONFIG)}))
    core, background, paths = seed_paths(cfg, risk, factors, seed)
    prepared = folder / 'prepared'
    if prepared.exists():
        checkpoint(prepared, [seed['seed_id'], 'input_paths'], CONFIG)
        require(read_gzip(prepared / 'issuer_paths.json.gz') == paths, 'Prepared inputs changed')
    else:
        stage = Path(tempfile.mkdtemp(prefix='checkpoint_prepare_', dir=folder))
        write_gzip(stage / 'issuer_paths.json.gz', paths)
        seal(stage, ['issuer_paths.json.gz'], [seed['seed_id'], 'input_paths'], CONFIG)
        require(stage.resolve().parent == folder.resolve() and prepared.resolve().parent == folder.resolve(),
                'Prepared resource stage left intended seed directory')
        stage.replace(prepared)
    return {'prepared_checkpoint_sha256': sha(prepared / 'checkpoint.json'),
            'producer_claim_sha256': sha(claim), 'original_protocol_sha256': sha(CONFIG)}


def produce_cell(seed_index, cell_index, cfg, data):
    from .temporal_resource_unit_study_v2 import CONFIG, CELLS, seal, checkpoint, bindings_ok
    from .run_temporal_resource_unit_study_v2 import execute_cell
    _, _, joined, mask = data
    folder, seed, core, background, paths = prepared_inputs(seed_index, cfg, data)
    cell = CELLS[cell_index]
    target = folder / f'cell_{cell_index}'
    require(not target.exists(), 'Preserve existing sealed condition; verify it before reusing')
    stage = Path(tempfile.mkdtemp(prefix=f'checkpoint_pending_{cell_index}_', dir=folder))
    execute_cell(cfg, joined, mask, seed, cell, paths, core, background, stage)
    seal(stage, ['ledger.jsonl.gz', 'condition.json'], [seed['seed_id'], cell['name']], CONFIG)
    require(stage.resolve().parent == folder.resolve() and target.resolve().parent == folder.resolve(),
            'Resource condition stage left intended seed directory')
    stage.replace(target)
    checkpoint(target, [seed['seed_id'], cell['name']], CONFIG)
    saved = read(target / 'condition.json')
    require(saved['checks'] == cfg['per_condition_producer_scope'], 'Full producer condition scope differs')
    bindings_ok(cfg)
    return {'condition_checkpoint_sha256': sha(target / 'checkpoint.json'), 'checks': saved['checks']}


def verify_produced_cell(seed_index, cell_index, cfg, recovery_path):
    """Verify a sealed result without assigning success to a lost producer."""
    from .temporal_resource_unit_study_v2 import OUTPUT, CONFIG, CELLS, checkpoint, bindings_ok
    from ..data_pipeline.temporal_transport_sources import windows_process_identity
    recovery = read(recovery_path)
    require(recovery['status'] == 'CHECKPOINT_WORKER_MISSING_EXIT_UNOBSERVED'
            and recovery['execution_protocol_sha256'] == sha(EXECUTION_CONFIG), 'Missing worker observation required')
    start_path = confined(ROOT / recovery['started_path'])
    require(sha(start_path) == recovery['started_sha256'], 'Missing worker start evidence changed')
    start = read(start_path)
    require((start['operation'], start['seed_index'], start.get('cell_index')) == ('produce-cell', seed_index, cell_index),
            'Missing producer condition differs')
    require(not (start_path.parent / 'receipt.json').exists(), 'Preserve an observed actual producer exit')
    owner = start['owner']
    identity = windows_process_identity(owner['pid'])
    require(identity is None or not identity['live'] or identity['creation_ticks'] != owner['creation_ticks'],
            'Original condition producer is still live')
    target = OUTPUT / f'seed_{seed_index}/cell_{cell_index}'
    checkpoint(target, [cfg['seeds'][seed_index]['seed_id'], CELLS[cell_index]['name']], CONFIG)
    require(sha(target / 'checkpoint.json') == recovery['sealed_checkpoint_sha256'], 'Recovered condition changed')
    saved = read(target / 'condition.json')
    require(saved['checks'] == cfg['per_condition_producer_scope'], 'Recovered producer scope incomplete')
    require([p['basket_index'] for p in saved['paths']] == list(range(41)), 'Recovered baskets incomplete')
    require(all(saved['variant'].get(k) == v for k, v in
                {'seed_id': cfg['seeds'][seed_index]['seed_id'], **CELLS[cell_index]}.items()),
            'Recovered condition identity differs')
    bindings_ok(cfg)
    return {'condition_checkpoint_sha256': sha(target / 'checkpoint.json'), 'checks': saved['checks'],
            'production_evidence': 'SEALED_RESULT_VERIFIED_ORIGINAL_PRODUCER_EXIT_UNKNOWN',
            'missing_original_exit_code': None, 'recovery_observation_path': recovery_path.relative_to(ROOT).as_posix(),
            'recovery_observation_sha256': sha(recovery_path)}


def audit_cell(seed_index, cell_index, cfg, execution):
    from . import audit_temporal_resource_unit_study_v2 as original
    module, fingerprint = audit_function(ORIGINAL_AUDITOR.read_text(encoding='utf-8'))
    require(fingerprint == execution['exact_original_condition_loop_ast_sha256'], 'Original audit loop changed')
    namespace = dict(original.__dict__)
    exec(compile(module, str(ORIGINAL_AUDITOR), 'exec'), namespace)
    result = namespace['audit_single_condition'](seed_index, cell_index=cell_index)
    delta = segment_counts(result['checks'], result['preflight_checks'])
    for key, value in delta.items():
        require(key in cfg['per_seed_audit_scope'] and cfg['per_seed_audit_scope'][key] == value * 96,
                'Complete condition audit scope differs: ' + key)
    require(delta['conditions'] == 1 and delta['paths'] == 41 and delta['full_path_ledger_records'] == 2378,
            'All baskets and all 58 steps must be audited')
    return {**result, 'condition_checks': delta, 'exact_original_condition_loop_ast_sha256': fingerprint}


def job_inventory(path, operation, seed_index, execution):
    inventory = read(path)
    require(inventory['execution_protocol_sha256'] == sha(EXECUTION_CONFIG), 'Job inventory recipe differs')
    rows = inventory['jobs']
    require(len(rows) == 96 and {r['cell_index'] for r in rows} == set(range(96)), 'All 96 condition jobs required')
    validated = {}
    for row in rows:
        index = row['cell_index']
        job, result = verify_job(ROOT / row['receipt_path'], execution, operation, seed_index, index)
        require(sha(ROOT / row['receipt_path']) == row['receipt_sha256'], 'Job receipt changed')
        validated[index] = (job, result, row)
    return validated


def complete_audit(seed_index, cfg, data, execution, inventory):
    from .temporal_resource_unit_study_v2 import OUTPUT, CONFIG, CELLS, checkpoint, encoded, bindings_ok
    from .audit_temporal_transport_inputs import audit_source_inputs
    validated = job_inventory(inventory, 'audit-cell', seed_index, execution)
    first = validated[0][1]
    # The source auditor uses integer distribution keys. JSON archives turn them
    # into strings; reconstruct the native result so final bytes retain the
    # original whole-seed auditor's encoding and key order.
    risk, factors, joined, mask = data
    source_qc = audit_source_inputs(cfg, {'risk': risk, 'features': factors, 'joined': joined}, mask)
    require(json_value(source_qc) == first['source_qc'], 'Fresh independent source audit differs from condition archives')
    base = first['preflight_checks']
    counts, coverage, artifacts = Counter(base), {}, {}
    folder = OUTPUT / f'seed_{seed_index}'
    seed = cfg['seeds'][seed_index]
    for index in range(96):
        _, result, _ = validated[index]
        require(result['preflight_checks'] == base and result['source_qc'] == first['source_qc'],
                'Independent source or prepared-input preflight differs across conditions')
        require(result['exact_original_condition_loop_ast_sha256'] == execution['exact_original_condition_loop_ast_sha256'],
                'Independent scientific loop differs')
        cell = CELLS[index]
        checkpoint(folder / f'cell_{index}', [seed['seed_id'], cell['name']], CONFIG)
        # Preserve the original auditor's platform-specific artifact keys exactly.
        expected_path = str((folder / f'cell_{index}/checkpoint.json').relative_to(ROOT))
        require(result['artifacts'] == {expected_path: sha(ROOT / expected_path)}, 'Audited sealed condition changed')
        require(set(result['condition_channel_coverage']) == {cell['name']}, 'Audit coverage identity differs')
        counts.update(result['condition_checks'])
        coverage.update(result['condition_channel_coverage'])
        artifacts.update(result['artifacts'])
    require(dict(counts) == cfg['per_seed_audit_scope'], 'Full original seed audit scope incomplete')
    bindings_ok(cfg)
    payload = finish_payload(seed['seed_id'], dict(counts), source_qc, coverage, artifacts, sha(CONFIG))
    target = folder / 'independent.json'
    if target.exists():
        require(target.read_bytes() == encoded(payload), 'Preserved complete audit differs from rebuilt full result')
    else:
        with target.open('xb') as handle:
            handle.write(encoded(payload))
    return {'audit_complete_sha256': sha(target), 'checks': dict(counts),
            'all_condition_actual_exits_verified': 96, 'inventory_sha256': sha(inventory),
            'scientific_result_format_unchanged': True}


def complete_production(seed_index, cfg, data, execution, inventory):
    from .temporal_resource_unit_study_v2 import CONFIG, CELLS, checkpoint, encoded, bindings_ok
    folder, seed, _, _, _ = prepared_inputs(seed_index, cfg, data)
    inv = read(inventory)
    require(inv['execution_protocol_sha256'] == sha(EXECUTION_CONFIG), 'Producer inventory recipe differs')
    reused = execution['interrupted_seed_observations'].get(str(seed_index), {}).get('sealed_condition_indices', [])
    new_indices = [i for i in range(96) if i not in reused]
    require([r['cell_index'] for r in inv['jobs']] == new_indices, 'Complete newly produced condition scope differs')
    recovered = []
    for row in inv['jobs']:
        index = row['cell_index']
        operation = row.get('operation', 'produce-cell')
        require(operation in ('produce-cell', 'verify-produced-cell'), 'Unexpected production evidence operation')
        job, result = verify_job(ROOT / row['receipt_path'], execution, operation, seed_index, index)
        require(sha(ROOT / row['receipt_path']) == row['receipt_sha256'], 'Producer job receipt changed')
        require(result['condition_checkpoint_sha256'] == sha(folder / f'cell_{index}/checkpoint.json'),
                'Produced condition checkpoint changed')
        if operation == 'verify-produced-cell':
            require(result['production_evidence'] == 'SEALED_RESULT_VERIFIED_ORIGINAL_PRODUCER_EXIT_UNKNOWN'
                    and result['missing_original_exit_code'] is None, 'Lost original producer exit must remain unknown')
            recovered.append(index)
    checkpoints = {'prepared/checkpoint.json': sha(folder / 'prepared/checkpoint.json')}
    for index, cell in enumerate(CELLS):
        target = folder / f'cell_{index}'
        checkpoint(target, [seed['seed_id'], cell['name']], CONFIG)
        saved = read(target / 'condition.json')
        require(saved['checks'] == cfg['per_condition_producer_scope'] and
                [p['basket_index'] for p in saved['paths']] == list(range(41)), 'Full condition production scope differs')
        require(all(saved['variant'].get(k) == v for k, v in {'seed_id': seed['seed_id'], **cell}.items()),
                'Produced condition identity differs')
        checkpoints[f'cell_{index}/checkpoint.json'] = sha(target / 'checkpoint.json')
        if index in reused:
            require(checkpoints[f'cell_{index}/checkpoint.json'] ==
                    execution['interrupted_seed_observations'][str(seed_index)]['sealed_checkpoint_sha256'][str(index)],
                    'Previously sealed interrupted condition changed')
    bindings_ok(cfg)
    payload = {'status': 'COMPLETE_FULL_RESOURCE_UNIT_SEED', 'seed_id': seed['seed_id'],
               'protocol_sha256': sha(CONFIG), 'checkpoints': checkpoints}
    target = folder / 'production_complete.json'
    if target.exists():
        require(target.read_bytes() == encoded(payload), 'Preserved complete production differs')
    else:
        with target.open('xb') as handle:
            handle.write(encoded(payload))
    function_path = folder / 'producer_function_complete.json'
    function = {'status': 'FULL_FUNCTION_SCOPE_COMPLETE_EXIT_NOT_YET_OBSERVED',
                'claim_sha256': sha(folder / 'producer_claim.json'), 'production_complete_sha256': sha(target),
                'execution_protocol_sha256': sha(EXECUTION_CONFIG), 'reused_verified_conditions': len(reused),
                'new_condition_indices': new_indices, 'condition_verification_jobs_with_actual_exit_zero': len(new_indices),
                'recovered_condition_indices_with_original_exit_unknown': recovered}
    if function_path.exists():
        require(read(function_path) == function, 'Preserved producer function completion differs')
    else:
        with function_path.open('xb') as handle:
            handle.write(encoded(function))
    return {'production_complete_sha256': sha(target), 'producer_function_complete_sha256': sha(function_path),
            'conditions': 96, 'reused_verified_conditions': len(reused),
            'new_production_jobs_with_actual_exit_zero': len(new_indices) - len(recovered),
            'recovered_sealed_conditions_with_actual_verification_exit_zero': len(recovered),
            'inventory_sha256': sha(inventory)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', choices=['prepare', 'produce-cell', 'verify-produced-cell', 'audit-cell', 'complete-production', 'complete-audit'], required=True)
    parser.add_argument('--seed', type=int, required=True, choices=range(5))
    parser.add_argument('--cell', type=int, choices=range(96))
    parser.add_argument('--result-file', type=Path, required=True)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--recovery-observation', type=Path)
    args = parser.parse_args()
    target = confined(args.result_file)
    require(not target.exists(), 'Keep existing checkpoint function results; use a new result path')
    execution, cfg, data = load_execution()
    if args.operation == 'prepare':
        values = prepare_seed(args.seed, cfg, data, execution)
    elif args.operation == 'produce-cell':
        require(args.cell is not None, 'Condition index required')
        values = produce_cell(args.seed, args.cell, cfg, data)
    elif args.operation == 'audit-cell':
        require(args.cell is not None, 'Condition index required')
        values = audit_cell(args.seed, args.cell, cfg, execution)
    elif args.operation == 'verify-produced-cell':
        require(args.cell is not None and args.recovery_observation is not None, 'Missing producer observation required')
        values = verify_produced_cell(args.seed, args.cell, cfg, confined(args.recovery_observation))
    elif args.operation == 'complete-production':
        require(args.inventory is not None, 'Production job inventory required')
        values = complete_production(args.seed, cfg, data, execution, confined(args.inventory))
    else:
        require(args.inventory is not None, 'Audit job inventory required')
        values = complete_audit(args.seed, cfg, data, execution, confined(args.inventory))
    payload = {'status': 'FUNCTION_COMPLETE_PENDING_ACTUAL_PROCESS_EXIT', 'operation': args.operation,
               'seed_index': args.seed, 'cell_index': args.cell, 'original_protocol_sha256': execution['original_protocol_sha256'],
               'execution_protocol_sha256': sha(EXECUTION_CONFIG), **values}
    new_json(target, payload)
    print(json.dumps({'operation': args.operation, 'seed': args.seed, 'cell': args.cell,
                      'status': payload['status']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
