"""Shared report and summary calculations for CLI and visual scenario batches."""
import hashlib
import json
from statistics import fmean


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def summarize_scenarios(manifest):
    summary = []
    for scenario, _, _ in manifest['scenarios']:
        completed = [r for r in manifest['records']
                     if r['scenario'] == scenario and r['status'] == 'completed']
        for role in ('aggressive', 'conservative', 'institutional'):
            values = [r['role_return_difference_pp'][role] for r in completed]
            summary.append({'scenario': scenario, 'role': role, 'completed': len(values),
                            'planned': len(manifest['seeds']),
                            'mean_pp': fmean(values) if values else None,
                            'min_pp': min(values) if values else None,
                            'max_pp': max(values) if values else None})
    return summary


def write_report(output, manifest):
    labels = {'neutral': '中性', 'positive': '正向', 'negative': '负向', 'uncertain': '高不确定性'}
    statuses = {'ready': '待运行', 'pending': '待运行', 'completed': '完成',
                'running': '运行中', 'failed': '失败', 'partial': '部分完成', 'interrupted': '已中断'}
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
                 *differences, row.get('experiment_id') or '—']
        lines.append('| ' + ' | '.join(cells) + ' |')
    lines += ['', '收益差为有消息减无消息，以百分点表示；订单数量为策略账户汇总。',
              '完成项参与统计；失败、待运行和中断项不填零收益。',
              '同一种子的各情景核对无消息基线哈希；完整配置与账本保存在对应实验目录，汇总及路径哈希见 batch.json。', '']
    failures = [row for row in rows if row['status'] in ('failed', 'interrupted')]
    if failures:
        lines += ['## 未完成项', '']
        for row in failures:
            error = row.get('error') or {}
            lines.append(f"- {labels[row['scenario']]} / seed {row['seed']}：{error.get('type', 'Interrupted')}。未计入收益汇总。")
        lines.append('')
    lines += ['## 跨种子描述性汇总', '',
              '仅统计完成项；样本数不足时不能与完整情景直接比较。范围不是置信区间，不代表统计显著性。', '',
              '| 情景 | 策略 | 完成/计划 | 平均收益差 pp | 最小值 pp | 最大值 pp |',
              '| --- | --- | --- | --- | --- | --- |']
    roles = {'aggressive': '激进', 'conservative': '保守', 'institutional': '机构'}
    for item in summarize_scenarios(manifest):
        values = ['—' if item[key] is None else f"{item[key]:+.6f}"
                  for key in ('mean_pp', 'min_pp', 'max_pp')]
        lines.append('| ' + ' | '.join([labels[item['scenario']], roles[item['role']],
                     f"{item['completed']}/{item['planned']}", *values]) + ' |')
    lines.append('')
    lines += ['## 运行配置', '', f"Python：{manifest['python_version']}",
              f"撮合配置 SHA-256：`{manifest['mechanism_config_sha256']}`",
              f"策略参数 SHA-256：`{manifest['strategy_parameters_sha256']}`", '']
    temporary = output / 'report.md.tmp'
    temporary.write_text('\n'.join(lines), encoding='utf-8')
    temporary.replace(output / 'report.md')
