"""Replay the frozen 60 probes and 120 source cases without changing their inputs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'research/configs/prototype_archive_replay_2026_v1.json'
LIBRARIES = ['C:/ProgramData/miniconda3/Lib/site-packages',
             'C:/Users/rain_/AppData/Roaming/Python/Python313/site-packages']


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def replace_receipt(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def scoped(path, root=ROOT):
    resolved = Path(path).resolve()
    require(resolved != root and root in resolved.parents, 'Path must stay within its declared output root')
    return resolved


def load_plan():
    cfg = read(CONFIG)
    require(cfg['schema_version'] == 'original-180-path-offline-replay-v1', 'Unexpected replay scope')
    require(cfg['new_model_calls'] == 0 and cfg['scientific_protocols_changed'] is False,
            'Replay must retain original scientific scope')
    require(cfg['expected_paths'] == {'probes': 60, 'source_cases': 120}, 'All registered paths required')
    require(cfg['expected_byte_identical_files'] == {'probes': 64, 'source_cases': 123}, 'All original artifacts required')
    for relative, expected in cfg['bindings'].items():
        require(sha(ROOT / relative) == expected, 'Frozen replay input changed: ' + relative)
    return cfg


def snapshot_inputs(output, cfg):
    snapshot = output / 'frozen_probe_inputs'
    snapshot.mkdir()
    copy_receipts = []
    for target, source in cfg['probe_snapshot_inputs'].items():
        destination = scoped(snapshot / target, snapshot)
        original = ROOT / source
        expected = cfg['bindings'][source]
        require(sha(original) == expected, 'Snapshot source changed')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as handle:
            handle.write(original.read_bytes())
        require(sha(destination) == expected, 'Copied historical input differs')
        copy_receipts.append({'target': target, 'source': source, 'sha256': expected})
    write_new(output / 'snapshot_manifest.json', {
        'status': 'EXACT_FROZEN_INPUT_BYTES_COPIED', 'files': copy_receipts,
        'historical_document_resolution': cfg['historical_document_resolution'],
        'current_workspace_document_replaced': False, 'scientific_code_modified': False})
    return snapshot


def check_snapshot(output, cfg):
    snapshot = output / 'frozen_probe_inputs'
    for target, source in cfg['probe_snapshot_inputs'].items():
        require(sha(snapshot / target) == cfg['bindings'][source], 'Copied replay input changed: ' + target)
    return snapshot


def run_command(output, label, command, cwd):
    log = output / (label + '.txt')
    receipt = output / (label + '.json')
    record = {'phase': label, 'command': command, 'working_directory': str(cwd),
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'actual_pid': None, 'actual_exit_code': None, 'status': 'STARTING'}
    write_new(receipt, record)
    with log.open('xb') as handle:
        child = subprocess.Popen(command, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT)
        record.update(actual_pid=child.pid, status='RUNNING_ACTUAL_REPLAY_COMMAND')
        replace_receipt(receipt, record)
        print(json.dumps({'phase': label, 'actual_pid': child.pid}), flush=True)
        code = child.wait()
    record.update(actual_exit_code=code, status='PASS_ACTUAL_REPLAY_COMMAND' if code == 0 else 'FAIL_ACTUAL_REPLAY_COMMAND',
                  finished_at_utc=datetime.now(timezone.utc).isoformat(), log_sha256=sha(log))
    replace_receipt(receipt, record)
    require(code == 0, 'Replay command failed: ' + label)
    return record


def import_auditor(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, 'Original auditor missing')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit_saved(output, study):
    cfg = load_plan()
    snapshot = check_snapshot(output, cfg)
    directory = output / study
    produced = read(output / (study + '_producer.json'))
    require(produced['actual_exit_code'] == 0, 'Actual replay producer success required')
    original_cfg = read(ROOT / cfg['studies'][study]['protocol'])
    manifest = read(directory / 'manifest.json')
    summary = read(directory / 'summary.json')
    require(manifest['protocol_sha256'] == sha(ROOT / cfg['studies'][study]['protocol']), 'Replayed protocol differs')
    require(summary['protocol_sha256'] == manifest['protocol_sha256'], 'Summary protocol differs')
    require(read(directory / 'frozen_plan.json') == original_cfg, 'Frozen replay plan differs')
    for relative, expected in manifest['artifacts'].items():
        require(sha(directory / relative) == expected, 'Saved replay artifact differs')

    paths, pairs, count = set(), [], Counter()
    if study == 'probes':
        auditor_path = snapshot / cfg['studies'][study]['auditor']
        auditor = import_auditor(auditor_path, 'original_probe_auditor')
        context = read(directory / 'historical_context_readiness.json')
        require(auditor.audit_sources(snapshot / original_cfg['context_catalog']) == context['source_audit'],
                'Original context archives or schema differ')
        require(context['historical_event_signal_applied'] is False, 'Historical context must remain disabled')
        for case in original_cfg['cases']:
            for seed in original_cfg['seeds']:
                runs = {}
                for mode in original_cfg['text_modes']:
                    name = f"{case['case_id']}_seed{seed}_text{int(mode)}.json.gz"
                    paths.add(name)
                    with gzip.open(directory / name, 'rt', encoding='utf-8') as handle:
                        run = json.load(handle)
                    require((run['case_id'], run['seed'], run['use_text']) == (case['case_id'], seed, mode),
                            'Probe condition identity differs')
                    auditor.audit_path(run, original_cfg, case)
                    runs[mode] = run
                    count.update(paths=1, portfolio_days=18, asset_calls=54,
                                 decision_explanations=216, asset_explanations=648,
                                 dense_asset_calls=3, closing_accounts=48)
                pairs.append(auditor.independent_pair(runs[False], runs[True]))
            print(json.dumps({'audited_probe_case': case['case_id'], 'paths': count['paths']}), flush=True)
        expected_pairs = 30
    else:
        auditor_path = ROOT / cfg['studies'][study]['auditor']
        auditor = import_auditor(auditor_path, 'original_source_case_auditor')
        cases = [json.loads(line) for line in (ROOT / original_cfg['prepared_case_directory'] / 'cases.jsonl').read_text(encoding='utf-8').splitlines()]
        reviews = [json.loads(line) for line in (ROOT / original_cfg['review_directory'] / 'case_reviews.jsonl').read_text(encoding='utf-8').splitlines()]
        require([c['case_id'] for c in cases] == original_cfg['case_ids'], 'Case coverage differs')
        require([r['case_id'] for r in reviews] == original_cfg['case_ids'], 'Review coverage differs')
        mapping = read(ROOT / original_cfg['mapping_path'])
        mechanism = read(ROOT / original_cfg['model_mechanism_config'])
        for index, (case, review) in enumerate(zip(cases, reviews)):
            facts = auditor.validate_fact_record(case, review['reviewed_record'])
            raw = read(ROOT / review['raw_case_file'])
            require(auditor.normalize_facts(case, raw['raw_response'])['facts'] == review['raw_facts'], 'Original model facts differ')
            for seed in original_cfg['seeds']:
                runs = {}
                for mode in original_cfg['modes']:
                    name = f'case{index}_seed{seed}_{mode}.json.gz'
                    paths.add(name)
                    with gzip.open(directory / name, 'rt', encoding='utf-8') as handle:
                        run = json.load(handle)
                    require((run['case_id'], run['seed'], run['mode']) == (case['case_id'], seed, mode),
                            'Source condition identity differs')
                    auditor.verify_run(run, case, facts, mapping, mechanism, original_cfg)
                    runs[mode] = run
                    count.update(paths=1, portfolio_days=18, asset_calls=54, decisions=216,
                                 asset_explanations=648, source_links=18, closing_accounts=48)
                for treatment, reference in (('keywords', 'no_text'), ('reviewed_llm', 'no_text'),
                                             ('reviewed_llm', 'keywords'), ('llm_asset_placebo', 'reviewed_llm')):
                    pairs.append(auditor.pair_direct(runs[reference], runs[treatment]))
            print(json.dumps({'audited_source_case': index + 1, 'paths': count['paths']}), flush=True)
        expected_pairs = 120

    require(paths == {p.name for p in directory.glob('*.json.gz')}, 'Full raw path coverage differs')
    require(pairs == summary['pairs'] and len(pairs) == expected_pairs, 'All independent paired decisions required')
    require(count['paths'] == cfg['expected_paths'][study], 'Registered path count differs')
    require(all(count[k] == v for k, v in manifest['counts'].items()), 'Registered audit counts differ')
    result = {'status': 'PASS_COMPLETE_REPLAY_INDEPENDENT_ORIGINAL_CLOCK_SETTLEMENT_TRACES_AND_PAIRS',
              'study': study, 'counts': dict(count), 'pairs': len(pairs),
              'actual_replay_producer_pid': produced['actual_pid'], 'actual_replay_producer_exit': 0,
              'original_auditor_sha256': sha(auditor_path), 'replay_protocol_sha256': sha(CONFIG),
              'original_scientific_protocol_sha256': manifest['protocol_sha256'],
              'new_model_calls': 0, 'goal_complete': False}
    write_new(directory / 'replay_independent_verification.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def replay(output):
    cfg = load_plan()
    require(not output.exists(), 'Use a new output directory; originals and partial replay artifacts are preserved')
    output.mkdir(parents=True)
    write_new(output / 'replay_plan.json', cfg)
    receipt = output / 'replay_receipt.json'
    record = {'status': 'RUNNING_ORIGINAL_180_PATH_OFFLINE_REPLAY',
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'replay_protocol_sha256': sha(CONFIG), 'actual_commands': [], 'comparisons': {},
              'new_model_calls': 0, 'scientific_protocols_changed': False, 'goal_complete': False}
    write_new(receipt, record)
    try:
        snapshot = snapshot_inputs(output, cfg)
        for study in ('probes', 'source_cases'):
            entry = cfg['studies'][study]
            module = entry['producer_module']
            bootstrap = f'import sys,runpy;sys.path.extend({LIBRARIES!r});runpy.run_module({module!r},run_name="__main__")'
            command = [sys.executable, '-B', '-X', 'utf8', '-c', bootstrap]
            if study == 'probes':
                command += ['--config', str(snapshot / entry['protocol'])]
            command += ['--output-dir', str(output / study)]
            job = run_command(output, study + '_producer', command, snapshot if study == 'probes' else ROOT)
            record['actual_commands'].append(job)
            replace_receipt(receipt, record)
            job = run_command(output, study + '_audit',
                              [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()),
                               '--output-dir', str(output), '--audit-study', study], ROOT)
            record['actual_commands'].append(job)
            original = ROOT / entry['original_output']
            original_manifest = read(original / 'manifest.json')
            names = sorted(set(original_manifest['artifacts']) | {'manifest.json'})
            require(len(names) == cfg['expected_byte_identical_files'][study], 'Original artifact inventory differs')
            for name in names:
                original_bytes = (original / name).read_bytes()
                replayed_bytes = (output / study / name).read_bytes()
                require(original_bytes == replayed_bytes, 'Full replay artifact bytes differ: ' + study + '/' + name)
            record['comparisons'][study] = {'paths': cfg['expected_paths'][study], 'byte_identical_files': names,
                                            'all_original_artifact_bytes_identical': True,
                                            'replay_independent_verification_sha256': sha(output / study / 'replay_independent_verification.json')}
            replace_receipt(receipt, record)
        load_plan()
        check_snapshot(output, cfg)
        record.update(status='PASS_ALL_180_ORIGINAL_PATHS_FULL_INDEPENDENT_AUDITS_AND_187_ARTIFACT_BYTES',
                      finished_at_utc=datetime.now(timezone.utc).isoformat(), total_byte_identical_files=187)
        replace_receipt(receipt, record)
        print(json.dumps({'status': record['status'], 'actual_commands': len(record['actual_commands']),
                          'byte_identical_files': 187, 'new_model_calls': 0}, ensure_ascii=False), flush=True)
    except Exception as error:
        record.update(status='FAIL_ORIGINAL_PATH_REPLAY_PRESERVED', error_type=type(error).__name__,
                      error_message=str(error), finished_at_utc=datetime.now(timezone.utc).isoformat())
        replace_receipt(receipt, record)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--audit-study', choices=('probes', 'source_cases'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = scoped(args.output_dir)
    if args.audit_study:
        audit_saved(output, args.audit_study)
    else:
        replay(output)


if __name__ == '__main__':
    main()
