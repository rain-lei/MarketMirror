"""Read the frozen six-source study without modifying or rerunning it."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import threading

from research.simulation.agents import AgentParameters
from research.simulation.agent_decision_trace import audit_explanations
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_market import initial_portfolio
from research.simulation.run_source_case_decisions import load_study, pair
from research.simulation.source_case_decision_link import case_inputs


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path(__file__).with_name('source-case-registry.json')
MODES = ('no_text', 'keywords', 'reviewed_llm', 'llm_asset_placebo')
SEEDS = (7, 11, 23, 47, 89)


class CaseArchiveIntegrityError(ValueError):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SourceCaseLibrary:
    def __init__(self, registry=REGISTRY, root=ROOT):
        self.registry, self.root = Path(registry), Path(root).resolve()
        self.cache, self.lock = OrderedDict(), threading.RLock()

    def _path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise CaseArchiveIntegrityError('案例路径超出登记目录')
        return path

    def _context(self):
        try:
            return self._read_context()
        except FileNotFoundError:
            raise
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise CaseArchiveIntegrityError('案例来源或冻结版本核验失败') from exc

    def _read_context(self):
        registry = json.loads(self.registry.read_text(encoding='utf-8'))
        if registry['schema'] != 'platform-real-source-case-registry-v1':
            raise CaseArchiveIntegrityError('案例登记版本不匹配')
        protocol_raw = self._path(registry['study_config']).read_bytes()
        directory = self._path(registry['archive_directory'])
        manifest_raw = (directory / 'manifest.json').read_bytes()
        if (sha(protocol_raw) != registry['study_config_sha256']
                or sha(manifest_raw) != registry['archive_manifest_sha256']):
            raise CaseArchiveIntegrityError('案例协议或归档清单已变化')
        config, cases, records, mapping, mechanism = load_study()
        manifest = json.loads(manifest_raw)
        expected = {k: v for k, v in config['expected_counts'].items() if k != 'pairs'}
        names = {f'case{i}_seed{s}_{m}.json.gz' for i in range(6) for s in SEEDS for m in MODES}
        if (config != json.loads(protocol_raw) or manifest['protocol_sha256'] != sha(protocol_raw)
                or manifest['version'] != config['schema_version']
                or manifest['bindings'] != config['bindings'] or manifest['counts'] != expected
                or set(manifest['artifacts']) != names | {'summary.json', 'frozen_plan.json'}
                or set(registry['labels']) != set(config['case_ids'])):
            raise CaseArchiveIntegrityError('案例归档范围与冻结协议不一致')
        if any(not (directory / name).is_file() for name in names):
            raise FileNotFoundError('案例路径归档不完整')
        for name in ('summary.json', 'frozen_plan.json'):
            raw = (directory / name).read_bytes()
            if sha(raw) != manifest['artifacts'][name]:
                raise CaseArchiveIntegrityError('案例摘要或冻结计划已变化')
            value = json.loads(raw)
            if name == 'frozen_plan.json' and value != config:
                raise CaseArchiveIntegrityError('冻结计划与源协议不一致')
            if name == 'summary.json' and (value['counts'] != expected
                    or value['status'] != 'COMPLETE_ALL_SIX_SOURCE_CASES_120_PATHS_AND_FULL_DECISION_LINKS'
                    or value['version'] != config['schema_version']
                    or value['protocol_sha256'] != sha(protocol_raw)
                    or value['historical_returns_or_prices_applied'] is not False
                    or value['independent_semantic_validation'] is not False):
                raise CaseArchiveIntegrityError('案例结果范围或解释边界已变化')
        notes = [json.loads(line) for line in self._path(config['review_directory']).joinpath('case_reviews.jsonl').read_text(encoding='utf-8').splitlines()]
        if len(notes) != len(cases) or len(cases) != 6:
            raise CaseArchiveIntegrityError('案例来源与复核数量不一致')
        for case, note in zip(cases, notes):
            record = records[case['case_id']]
            if (note['case_id'] != case['case_id'] or note['case_text_sha256'] != case['case_text_sha256']
                    or note['reviewed_record'] != record or note['independent_gold'] is not False
                    or note['ready_for_registered_development_case'] is not True
                    or not isinstance(note['review_basis'], str) or not note['review_basis']
                    or not isinstance(note['raw_facts'], list)
                    or len(note['raw_facts']) != len(record['facts'])
                    or len(note['checks']) != len(record['facts'])
                    or [check['fact_index'] for check in note['checks']] != list(range(len(record['facts'])))
                    or any(not isinstance(check['changes'], dict) or not isinstance(check['notes'], str)
                           for check in note['checks'])):
                raise CaseArchiveIntegrityError('案例原始提取与复核依据不完整')
        return registry, config, cases, records, mapping, mechanism, manifest, directory, notes

    @staticmethod
    def _metadata(case, note, label):
        return {key: case.get(key) for key in ('case_id', 'title', 'source_kind', 'stock_code',
                    'source_url', 'available_at', 'visibility_precision', 'case_text_sha256')} | {
            'label': label, 'facts': len(note['reviewed_record']['facts']),
            'corrected_facts': sum(bool(check['changes']) for check in note['checks']),
            'data_kind': 'source_text_with_synthetic_market'}

    def catalog(self):
        try:
            registry, config, cases, _, _, _, _, _, notes = self._context()
        except FileNotFoundError:
            return {'available': False, 'cases': [], 'message': '这台电脑尚无完整案例归档。单次与批量合成实验仍可使用。'}
        return {'available': True, 'cases': [self._metadata(case, note, registry['labels'][case['case_id']])
                    for case, note in zip(cases, notes)], 'modes': list(MODES), 'seeds': list(SEEDS),
                'path_count': config['expected_counts']['paths'], 'sessions': 18,
                'paths_validated_on_open': True, 'historical_returns_or_prices_applied': False,
                'independent_semantic_validation': False}

    def _run(self, context, index, seed, mode):
        try:
            return self._read_run(context, index, seed, mode)
        except (ValueError, KeyError, TypeError, OSError, AssertionError) as exc:
            raise CaseArchiveIntegrityError('所选案例路径、来源时钟或账本核验失败') from exc

    def _read_run(self, context, index, seed, mode):
        registry, _, cases, records, mapping, mechanism, manifest, directory, _ = context
        case = cases[index]
        name = f'case{index}_seed{seed}_{mode}.json.gz'
        raw = (directory / name).read_bytes()
        expected_hash = manifest['artifacts'][name]
        if sha(raw) != expected_hash:
            raise CaseArchiveIntegrityError('所选案例路径文件已变化，拒绝展示')
        cache_key = (registry['archive_manifest_sha256'], registry['study_config_sha256'], name)
        with self.lock:
            if cache_key in self.cache:
                self.cache.move_to_end(cache_key)
                return self.cache[cache_key]
        value = json.loads(gzip.decompress(raw))
        joined, links = case_inputs(case, records[case['case_id']], mapping, mode)
        result = value['model_result']
        if (value['case_id'] != case['case_id'] or value['case_text_sha256'] != case['case_text_sha256']
                or value['seed'] != seed or value['mode'] != mode
                or value['data_kind'] != 'source_text_with_synthetic_market'
                or value['reviewed_source_facts'] != records[case['case_id']]
                or value['source_links'] != links or len(result['trace']) != 18):
            raise CaseArchiveIntegrityError('案例路径身份、来源时钟或复核事实不一致')
        audit_explanations(value['decision_explanations'], result)
        agents = [AgentParameters(**p) for p in mechanism['agent_parameters']]
        core, background = {**mechanism['core'], 'seed': seed}, {**mechanism['background'], 'seed': seed}
        venue, portfolio = mechanism['venue'], mechanism['portfolio_case']
        _, accounts, _, _ = initial_portfolio(agents, core, background, ['A','B','C'], portfolio, venue)
        previous = {'accounts': {n: asdict(a) for n, a in accounts.items()},
                    'prices': dict.fromkeys(joined, venue['price_start_minor']), 'fee_pool_minor': 0,
                    'initial_cash_minor': sum(sum(a.wallets.values()) for a in accounts.values()),
                    'initial_shares': {a: sum(p.shares[a] for p in accounts.values()) for a in joined}}
        for step, day in enumerate(result['trace']):
            previous = audit_portfolio_day(day, previous, venue, step, dense=step == 0)
            for asset, observation in day['observations'].items():
                expected = joined[asset][step]
                for key in ('trade_date', 'signal_cutoff_date', 'execution_reference_date'):
                    if day[key] != expected[key]:
                        raise CaseArchiveIntegrityError('案例交易时钟不一致')
                for key in ('text_signal', 'text_uncertainty'):
                    if observation[key] != expected[key]:
                        raise CaseArchiveIntegrityError('原文映射与观察输入不一致')
        summary = result['summary']
        if (set(summary['accounts']) != set(previous['accounts'])
                or any(account[key] != summary['accounts'][owner][key]
                       for owner, account in previous['accounts'].items()
                       for key in ('wallets', 'shares', 'sellable'))
                or summary['final_prices_minor'] != previous['prices']
                or summary['fee_pool_minor'] != previous['fee_pool_minor']):
            raise CaseArchiveIntegrityError('独立结算与归档期末账户不一致')
        with self.lock:
            self.cache[cache_key] = (value, expected_hash)
            self.cache.move_to_end(cache_key)
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
        return value, expected_hash

    def verify_all(self):
        context = self._context()
        counts = {'paths': 0, 'portfolio_days': 0, 'decisions': 0}
        for index in range(6):
            for seed in SEEDS:
                for mode in MODES:
                    value, _ = self._run(context, index, seed, mode)
                    counts['paths'] += 1
                    counts['portfolio_days'] += len(value['model_result']['trace'])
                    counts['decisions'] += len(value['decision_explanations'])
        self._context()
        return {'passed': True, **counts, 'llm_called_now': False}

    def get(self, case_id, seed=7, mode='reviewed_llm'):
        if type(seed) is not int or seed not in SEEDS or mode not in MODES:
            raise ValueError('请选择归档的种子与信息条件')
        context = self._context()
        registry, _, cases, _, mapping, _, _, _, notes = context
        index = next((i for i, case in enumerate(cases) if case['case_id'] == case_id), None)
        if index is None:
            raise FileNotFoundError('案例不存在')
        case, note = cases[index], notes[index]
        baseline, baseline_hash = self._run(context, index, seed, 'no_text')
        selected, selected_hash = self._run(context, index, seed, mode)
        comparison = pair(baseline, selected)
        return deepcopy({'case': {**self._metadata(case, note, registry['labels'][case_id]), 'segments': case['segments']},
            'review': {key: note[key] for key in ('raw_facts', 'reviewed_record', 'checks', 'review_basis', 'independent_gold')},
            'mode': mode, 'seed': seed, 'modes': list(MODES), 'seeds': list(SEEDS),
            'mapping_rules': mapping['fact_mapping'], 'comparison': comparison,
            'source_links': {'baseline': baseline['source_links'], 'with_message': selected['source_links']},
            'result': {'mode': 'source_text_with_synthetic_market',
                'paths': {'baseline': baseline['model_result'], 'with_message': selected['model_result']},
                'audit': {'passed': True, 'paths_checked': 1 if mode == 'no_text' else 2,
                          'days_checked': 18 if mode == 'no_text' else 36},
                'provenance': {'case_id': case_id, 'seed': seed, 'condition': mode,
                    'protocol_sha256': registry['study_config_sha256'],
                    'archive_manifest_sha256': registry['archive_manifest_sha256'],
                    'baseline_path_sha256': baseline_hash, 'selected_path_sha256': selected_hash,
                    'llm_called_now': False, 'original_model': 'DeepSeek-V4-Flash-0731-W8A8',
                    'facts_were_reviewed_and_corrected': True, 'independent_semantic_validation': False,
                    'historical_returns_or_prices_applied': False}}})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='只读核验本机六来源案例归档，不调用模型或重跑市场。')
    parser.add_argument('--verify-all', action='store_true', help='核验四条件与五种子的全部 120 条路径')
    args = parser.parse_args()
    library = SourceCaseLibrary()
    try:
        output = library.verify_all() if args.verify_all else library.catalog()
    except (FileNotFoundError, CaseArchiveIntegrityError) as exc:
        parser.exit(1, f'案例归档不可用：{exc}\n')
    print(json.dumps(output, ensure_ascii=False, indent=2))
