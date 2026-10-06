"""Reproducible synthetic scenario batch using the platform's actual storage/engine."""
import argparse
import hashlib
import json
from pathlib import Path

from .server import PlatformStore, atomic_json

SCENARIOS = (("neutral", 0, 0), ("positive", .6, .2),
             ("negative", -.6, .2), ("uncertain", 0, .8))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_report(output, manifest):
    labels = {'neutral': '中性', 'positive': '正向', 'negative': '负向', 'uncertain': '高不确定性'}
    statuses = {'completed': '完成', 'running': '运行中', 'failed': '失败'}
    rows = manifest['records']
    completed = sum(row['status'] == 'completed' for row in rows)
    total = len(manifest['seeds']) * len(manifest['scenarios'])
    lines = ['# MarketMirror 合成情景对照', '',
             f"批次状态：{statuses[manifest['status']]}。完成 {completed}/{total} 项；每项 {manifest['sessions']} 步。", '',
             '信号与不确定性均为预设假设，未调用大模型，不代表历史预测或真实投资者表现。', '',
             '| 情景 | 种子 | 状态 | 请求/接受/成交（股） | 激进收益差 pp | 保守收益差 pp | 机构收益差 pp | 实验编号 |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in rows:
        if row['status'] == 'completed':
            quantities = f"{row['requested']}/{row['accepted']}/{row['filled']}"
            differences = [f"{row['role_return_difference_pp'][role]:+.6f}"
                           for role in ('aggressive', 'conservative', 'institutional')]
        else:
            quantities, differences = '未完成', ['—'] * 3
        cells = [labels[row['scenario']], str(row['seed']), statuses[row['status']], quantities,
                 *differences, row['experiment_id']]
        lines.append('| ' + ' | '.join(cells) + ' |')
    lines += ['', '收益差为有消息减无消息，以百分点表示；订单数量为策略账户汇总。',
              '未启动项不出现在表格中，可用计划总数与完成数区分。失败项不填零收益。',
              '同一种子的各情景核对无消息基线哈希；完整配置与账本保存在 workspace/，汇总及路径哈希见 batch.json。', '']
    temporary = output / 'report.md.tmp'
    temporary.write_text('\n'.join(lines), encoding='utf-8')
    temporary.replace(output / 'report.md')


def run_batch(output, seeds=(1, 7, 19), sessions=18):
    seeds = tuple(seeds)
    if not seeds or len(set(seeds)) != len(seeds) or any(type(s) is not int or not 0 <= s <= 999999 for s in seeds):
        raise ValueError('Seeds must be distinct integers from 0 to 999999')
    if type(sessions) is not int or not 1 <= sessions <= 60:
        raise ValueError('Sessions must be an integer from 1 to 60')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    store = PlatformStore(output / 'workspace')
    manifest = {'schema': 'platform-scenario-batch-v1', 'status': 'running',
                'seeds': seeds, 'sessions': sessions, 'scenarios': SCENARIOS,
                'mode': 'synthetic_market', 'llm_called': False, 'records': []}
    atomic_json(output / 'batch.json', manifest)
    write_report(output, manifest)
    baselines = {}
    try:
        for seed in seeds:
            for name, signal, uncertainty in SCENARIOS:
                record = store.create({'title': f'批次 {name} · seed {seed}',
                    'source': '合成对照实验：方向与不确定性均为预设变量，不来自真实公告或模型推断。',
                    'type': '其他消息', 'published_at': '2000-01-01T00:00',
                    'signal': signal, 'uncertainty': uncertainty, 'duration': 6,
                    'sessions': sessions, 'seed': seed, 'cash': 1000000})
                row = {'scenario': name, 'seed': seed, 'experiment_id': record['id'], 'status': 'running'}
                manifest['records'].append(row)
                atomic_json(output / 'batch.json', manifest)
                write_report(output, manifest)
                result = store.run(record['id'])
                baseline, message = result['paths']['baseline'], result['paths']['with_message']
                baseline_hash = digest(baseline)
                if seed in baselines and baselines[seed] != baseline_hash:
                    raise ValueError('Baseline changed between paired scenarios')
                baselines[seed] = baseline_hash
                row.update(status='completed', baseline_sha256=baseline_hash,
                    paths_sha256=digest(result['paths']), audit=result['audit'],
                    role_return_difference_pp={role: 100 * (value - baseline['summary']['role_wealth_multiple'][role])
                        for role, value in message['summary']['role_wealth_multiple'].items()},
                    requested=message['summary']['strategy_requested'],
                    accepted=message['summary']['strategy_accepted'],
                    filled=message['summary']['strategy_filled'])
                atomic_json(output / 'batch.json', manifest)
                write_report(output, manifest)
        manifest['status'] = 'completed'
    except Exception as exc:
        manifest.update(status='failed', error_type=type(exc).__name__)
        if manifest['records'] and manifest['records'][-1]['status'] == 'running':
            manifest['records'][-1].update(status='failed', error_type=type(exc).__name__)
        atomic_json(output / 'batch.json', manifest)
        write_report(output, manifest)
        raise
    atomic_json(output / 'batch.json', manifest)
    write_report(output, manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--seeds', nargs='+', type=int, default=[1, 7, 19])
    parser.add_argument('--sessions', type=int, default=18)
    args = parser.parse_args()
    report = run_batch(args.output, args.seeds, args.sessions)
    print(f"{report['status']}: {len(report['records'])} experiments; {args.output}")
