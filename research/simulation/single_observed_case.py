"""A reviewed single-source development experiment on archived observed returns.

Prepare freezes the case, time window, parameters and source hashes before the
live model call. Extract archives the actual response. Run requires a separate
source review; it does not enable the historical automatic semantic gate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path

from ..baselines.run_experiments import _load_market
from ..data_pipeline.provenance import file_sha256
from ..semantic.local_credential import load_api_key
from ..semantic.run_model import DEFAULT_BASE_URL, DEFAULT_MODEL, endpoint_url, request_completion
from ..semantic.run_source_case_model import load_plan
from ..semantic.source_case_facts import ROOT, canonical, digest, normalize_facts, validate_fact_record
from .agents import AgentParameters
from .historical_replay import prepare_steps
from .semantic_replay import replay_semantic_path
from .source_case_decision_link import load_mapping, mapped_information

VERSION = 'single-source-observed-development-v1'
MODES = ('no_text', 'keywords', 'reviewed_llm')
REPLAY_CONFIG = ROOT / 'research/configs/semantic_replay_all126_2020.json'
PROMPT = ROOT / 'research/semantic/PROMPT_SOURCE_CASE_FACTS_V1.md'
CODE_FILES = (
    'research/simulation/single_observed_case.py', 'research/simulation/agents.py',
    'research/simulation/historical_replay.py', 'research/simulation/semantic_replay.py',
    'research/simulation/source_case_decision_link.py', 'research/semantic/source_case_facts.py',
    'research/baselines/run_experiments.py', 'research/semantic/run_model.py',
    'research/semantic/signal_validation.py', 'research/data_pipeline/market_data.py',
)
ROLE_LABELS = {'aggressive': '激进型', 'conservative': '保守型', 'institutional': '机构型'}
MODE_LABELS = {'no_text': '无文本', 'keywords': '关键词', 'reviewed_llm': '复核后 LLM'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def select_steps(rows, case, config, sessions=30, before=4):
    steps = prepare_steps(rows, config['start_date'], config['end_date'],
                          config['momentum_sessions'], config['volatility_sessions'])
    available = datetime.fromisoformat(case['available_at'])
    first = next((i for i, s in enumerate(steps) if datetime.fromisoformat(
        s['signal_cutoff_date'] + 'T23:59:59.999999+08:00') >= available), None)
    if first is None or first < before or first - before + sessions > len(steps):
        raise ValueError('完整预热、公开前和公开后的历史窗口不足')
    return deepcopy(steps[first-before:first-before+sessions])


def verify_observed_rows(rows, stock_path, benchmark_path):
    """Recompute imported returns from the archived provider records."""
    with stock_path.open(encoding='utf-8-sig', newline='') as stream:
        stocks = {r['date']: r for r in csv.DictReader(stream)}
    with benchmark_path.open(encoding='utf-8-sig', newline='') as stream:
        benchmark = list(csv.DictReader(stream))
    if [r['date'] for r in benchmark] != sorted({r['date'] for r in benchmark}):
        raise ValueError('基准原始行情日期重复或无序')
    market_returns = {current['date']: float(current['close'])/float(previous['close'])-1
                      for previous, current in zip(benchmark, benchmark[1:])}
    for row in rows:
        day = row.trade_date.isoformat()
        if (not math.isclose(row.stock_return, float(stocks[day]['pctChg'])/100, abs_tol=1e-12)
                or not math.isclose(row.market_return, market_returns[day], abs_tol=1e-12)):
            raise ValueError('导入收益与供应商原始行情不一致：'+day)
    return {'stock_return_rows': len(rows), 'benchmark_return_rows': len(rows),
            'stock_basis': 'provider_pctChg_percent', 'benchmark_basis': 'consecutive_close_ratio'}


def prepare(directory):
    source_plan, cases = load_plan()
    # Selection is fixed before simulated performance is inspected.
    case = next(c for c in cases if c['source_kind'] == 'company_reply_workbook')
    config = read(REPLAY_CONFIG)
    market_path = (REPLAY_CONFIG.parent / config['market_manifest']).resolve()
    download_path = (REPLAY_CONFIG.parent / config['download_manifest']).resolve()
    market, download = read(market_path), read(download_path)
    market_csv = market_path.parent / 'market_daily.csv'
    if (market['data_kind'] != 'observed' or download['data_kind'] != 'observed'
            or download['provider'] != 'BaoStock'
            or file_sha256(market_csv) != market['artifacts']['market_daily.csv']['sha256']):
        raise ValueError('需要完整且哈希匹配的真实行情归档')
    files = [REPLAY_CONFIG, market_path, market_csv, download_path, PROMPT,
             ROOT / 'research/configs/source_case_facts_development_2026_v1.json',
             ROOT / 'research/configs/source_case_scenario_mapping_2026_v1.json',
             ROOT / source_plan['source_case_directory'] / 'cases.jsonl',
             ROOT / case['source_archive_path']]
    if file_sha256(ROOT / case['source_archive_path']) != case['source_archive_sha256']:
        raise ValueError('问答原始来源归档发生变化')
    for name, info in download['artifacts'].items():
        path = download_path.parent / name
        if file_sha256(path) != info['sha256']:
            raise ValueError('行情下载原始文件发生变化：' + name)
        files.append(path)
    import_config = Path(market['config_path'])
    if file_sha256(import_config) != market['config_sha256'] or read(import_config) != market['settings']:
        raise ValueError('行情导入规则与归档不一致')
    files.append(import_config)
    for info in market['inputs'].values():
        path = Path(info['path'])
        if path.resolve().parent != download_path.parent or file_sha256(path) != info['sha256']:
            raise ValueError('行情导入源文件已变化')
        files.append(path)
    groups = _load_market(market_csv, market)
    raw_check = verify_observed_rows(groups[case['stock_code']],
        download_path.parent / f"sz_{case['stock_code']}_raw.csv",
        download_path.parent / 'sh_000300_raw.csv')
    steps = select_steps(groups[case['stock_code']], case, config)
    check = next(c for c in download['quote_checks'] if c['symbol'].split('.')[1] == case['stock_code'])
    for step in steps:
        step['execution_available'] = step['execution_reference_date'] not in check['suspended_sessions']
    plan = {
        'schema_version': VERSION, 'selection': 'first_registered_company_reply_before_performance',
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'case': case,
        'sessions': 30, 'pre_information_steps': 4, 'information_duration_steps': 6,
        'modes': list(MODES), 'agents': config['agents'],
        'fee_rate': config['transaction_cost_rate'], 'steps': steps, 'mapping': load_mapping(),
        'market_dataset_id': market['market_dataset_id'], 'market_retrieved_at': download['retrieved_at'],
        'raw_market_reconciliation': raw_check,
        'market_signal_rule': '5-session benchmark momentum and 20-session volatility, observed through t-2',
        'execution_rule': 'previous-close return index; fractional normalized units; cash/long-only and suspension constraints',
        'initial_price_index': 100.0, 'model': DEFAULT_MODEL, 'base_url': DEFAULT_BASE_URL,
        'model_temperature': 0, 'source_review_required': True, 'development_case_not_holdout': True,
        'automatic_semantic_gate_enabled': False, 'investor_parameters_calibrated': False,
        'input_sha256': {str(p.relative_to(ROOT)): file_sha256(p) for p in files},
        'code_sha256': {name: file_sha256(ROOT/name) for name in CODE_FILES},
    }
    directory.mkdir(parents=True, exist_ok=False)
    write_new(directory/'plan.json', plan)
    write_new(directory/'plan_receipt.json', {'schema_version': VERSION, 'plan_sha256': file_sha256(directory/'plan.json')})
    return {'status': 'PREPARED_BEFORE_MODEL_AND_RESULTS', 'stock_code': case['stock_code'],
            'start': steps[0]['trade_date'], 'end': steps[-1]['trade_date'], 'sessions': len(steps),
            'plan_sha256': file_sha256(directory/'plan.json')}


def checked_plan(directory):
    plan = read(directory/'plan.json')
    if plan['schema_version'] != VERSION or read(directory/'plan_receipt.json')['plan_sha256'] != file_sha256(directory/'plan.json'):
        raise ValueError('实验计划已变化')
    for group in ('input_sha256', 'code_sha256'):
        for name, expected in plan[group].items():
            if file_sha256(ROOT/name) != expected:
                raise ValueError('冻结实验依赖已变化：' + name)
    return plan


def extract(directory):
    plan = checked_plan(directory)
    if (directory/'model.json').exists():
        raise ValueError('已保留模型结果；复现不重复调用模型')
    payload = {'source_kind': plan['case']['source_kind'], 'segments': plan['case']['segments']}
    started = datetime.now(timezone.utc).isoformat()
    print(json.dumps({'status': 'ACTUAL_MODEL_REQUEST_STARTED', 'model': plan['model']}, ensure_ascii=False), flush=True)
    raw = request_completion(endpoint_url(plan['base_url']), load_api_key(), plan['model'],
        [{'role':'system','content':PROMPT.read_text(encoding='utf-8')},
         {'role':'user','content':canonical(payload).decode('utf-8')}], 45, 2, plan['model_temperature'])
    # Preserve raw output even when parsing fails. Failures never become empty facts.
    record = {'plan_sha256': file_sha256(directory/'plan.json'), 'model': plan['model'],
              'prompt_sha256': file_sha256(PROMPT), 'sent_payload_sha256': digest(canonical(payload)),
              'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
              'raw_response': raw, 'normalized': None, 'parse_error': None, 'llm_called_now': True}
    try:
        record['normalized'] = normalize_facts(plan['case'], raw)
    except (ValueError, TypeError, KeyError) as exc:
        record['parse_error'] = type(exc).__name__
    write_new(directory/'model.json', record)
    checked_plan(directory)
    if record['parse_error']:
        raise ValueError('模型事实或原文引文不合格，已保留原始响应，未运行回放')
    return {'status': 'ACTUAL_MODEL_EXTRACTION_COMPLETED', 'facts': len(record['normalized']['facts'])}


def checked_review(directory, plan):
    model, review = read(directory/'model.json'), read(directory/'review.json')
    payload = {'source_kind': plan['case']['source_kind'], 'segments': plan['case']['segments']}
    if (model['plan_sha256'] != file_sha256(directory/'plan.json')
            or model['prompt_sha256'] != file_sha256(PROMPT)
            or model['sent_payload_sha256'] != digest(canonical(payload))
            or model['model'] != plan['model'] or model['llm_called_now'] is not True
            or model['parse_error'] is not None or model['normalized'] != normalize_facts(plan['case'], model['raw_response'])):
        raise ValueError('模型响应与冻结来源不一致')
    record = validate_fact_record(plan['case'], review['reviewed_record'])
    if (review.get('model_sha256') != file_sha256(directory/'model.json')
            or review.get('review_basis') != 'single_assistant_source_review_before_replay'
            or review.get('independent_gold') is not False
            or len(review['checks']) != len(model['normalized']['facts'])
            or len(record['facts']) != len(model['normalized']['facts'])
            or [c['fact_index'] for c in review['checks']] != list(range(len(model['normalized']['facts'])))
            or any(not isinstance(c.get('notes'), str) or not c['notes'].strip() for c in review['checks'])):
        raise ValueError('逐条来源复核记录不完整')
    return record


def joined_conditions(plan, record):
    case, mapping = plan['case'], plan['mapping']
    output, visible_count = {mode: [] for mode in MODES}, 0
    for step in plan['steps']:
        cutoff = step['signal_cutoff_date'] + 'T23:59:59.999999+08:00'
        visible = datetime.fromisoformat(cutoff) >= datetime.fromisoformat(case['available_at'])
        visible_count += int(visible)
        active = visible and visible_count <= plan['information_duration_steps']
        for mode in MODES:
            information = mapped_information(case, record, mapping, mode, cutoff)
            output[mode].append({**step, 'source_visible': visible, 'source_active': active,
                'text_signal': information['signal'] if active else 0.0,
                'text_uncertainty': information['uncertainty'] if active else 0.0,
                'text_evidence': f"source:{case['case_text_sha256']}:cutoff:{step['signal_cutoff_date']}",
                'source_link': information if active else None})
    return output


def calculate(plan, record):
    joined = joined_conditions(plan, record)
    agents = [AgentParameters(**p) for p in plan['agents']]
    paths = {mode: replay_semantic_path(joined[mode], agents, plan['fee_rate'], mode != 'no_text') for mode in MODES}
    comparison = []
    for mode in MODES:
        for agent in agents:
            summary, baseline = paths[mode]['summary'][agent.name], paths['no_text']['summary'][agent.name]
            comparison.append({'condition': mode, 'role': agent.role, 'return_pct': summary['return']*100,
                'delta_return_pp': (summary['return']-baseline['return'])*100,
                'max_drawdown_pct': summary['max_drawdown']*100, 'trades': summary['trades'],
                'fees': summary['cumulative_fees'], 'final_wealth': summary['final_wealth']})
    return {'schema_version': VERSION, 'mode': 'observed_return_price_taking_replay',
            'stock_code': plan['case']['stock_code'], 'case_id': plan['case']['case_id'],
            'source_sha256': plan['case']['case_text_sha256'], 'market_dataset_id': plan['market_dataset_id'],
            'agents': plan['agents'], 'paths': paths, 'comparison': comparison,
            'semantic_truth_independently_verified': False, 'automatic_semantic_gate_enabled': False}


def audit(plan, result):
    """Independently reconstruct cash, holdings, fees, NAV, drawdown and source time."""
    days, decisions = 0, 0
    assert set(result['paths']) == set(MODES), '对照条件不完整'
    for mode, path in result['paths'].items():
        cash = {p['name']: float(p['initial_cash']) for p in plan['agents']}
        units = dict.fromkeys(cash, 0.0)
        peaks, drawdown, fees, trades = deepcopy(cash), dict.fromkeys(cash, 0.0), dict.fromkeys(cash, 0.0), dict.fromkeys(cash, 0)
        price = plan['initial_price_index']
        initial = sum(cash.values())
        assert len(path['trace']) == len(plan['steps']) == plan['sessions'], '路径日数不完整'
        assert set(path['summary']) == set(cash), '策略汇总不完整'
        visible_count = 0
        for index, day in enumerate(path['trace']):
            expected = plan['steps'][index]
            for key in ('trade_date','signal_cutoff_date','execution_reference_date','observed_return','market_signal','estimated_volatility','execution_available'):
                assert day[key] == expected[key], f'行情或时钟不一致：{mode}/{index}/{key}'
            visible = datetime.fromisoformat(day['signal_cutoff_date']+'T23:59:59.999999+08:00') >= datetime.fromisoformat(plan['case']['available_at'])
            visible_count += int(visible)
            assert day['source_visible'] == visible
            assert day['source_active'] == (visible and visible_count <= plan['information_duration_steps'])
            assert set(day['agents']) == set(cash), '逐日策略记录不完整'
            if not visible or mode == 'no_text' or not day['source_active']:
                assert day['text_signal'] == day['text_uncertainty'] == 0.0
            assert day['signal_cutoff_date'] < day['execution_reference_date'] < day['trade_date']
            next_price = price * (1 + expected['observed_return'])
            assert math.isclose(day['price_index_before'],price,rel_tol=1e-12)
            assert math.isclose(day['price_index_after'],next_price,rel_tol=1e-12)
            for name, row in day['agents'].items():
                requested = row['decision']['requested_shares']
                expected_fill = min(requested,cash[name]/(price*(1+plan['fee_rate']))) if requested>0 else max(requested,-units[name])
                if not expected['execution_available']:
                    expected_fill = 0.0
                assert math.isclose(row['filled_shares'], expected_fill, abs_tol=1e-8)
                fee = abs(expected_fill*price)*plan['fee_rate']
                cash[name] -= expected_fill*price + fee
                units[name] += expected_fill
                nav = cash[name] + units[name]*next_price
                assert cash[name] >= -1e-8 and units[name] >= -1e-8
                for actual, rebuilt in ((row['cash'],cash[name]),(row['shares'],units[name]),(row['fees_paid'],fee),(row['closing_wealth'],nav)):
                    assert math.isclose(actual,rebuilt,rel_tol=1e-12,abs_tol=1e-7)
                peaks[name] = max(peaks[name],nav)
                drawdown[name] = max(drawdown[name],1-nav/peaks[name])
                fees[name] += fee
                trades[name] += int(abs(expected_fill)>1e-12)
                if not visible:
                    assert row == result['paths']['no_text']['trace'][index]['agents'][name]
                decisions += 1
            assert math.isclose(sum(cash.values())+day['external_cash']+day['fee_pool'],initial,rel_tol=1e-12,abs_tol=1e-6)
            assert math.isclose(sum(units.values())+day['external_shares'],0.0,abs_tol=1e-8)
            assert math.isclose(sum(fees.values()),day['fee_pool'],abs_tol=1e-7)
            price = next_price
            days += 1
        for p in plan['agents']:
            name, summary = p['name'], path['summary'][p['name']]
            assert math.isclose(summary['final_wealth'],cash[name]+units[name]*price,abs_tol=1e-6)
            assert math.isclose(summary['max_drawdown'],drawdown[name],abs_tol=1e-12)
            assert math.isclose(summary['cumulative_fees'],fees[name],abs_tol=1e-7)
            assert math.isclose(summary['return'],summary['final_wealth']/p['initial_cash']-1,abs_tol=1e-12)
            assert summary['trades'] == trades[name]
    return {'passed':True,'paths':len(result['paths']),'market_days':days,'agent_decisions':decisions,
            'checks':['source_time','pre_publication_equal','historical_return_identity','cash_and_units','fees','NAV','drawdown']}


def render_report(plan, result, checks):
    lines = ['# 真实公司回复与真实历史收益：单案例实验', '',
        f"股票：{result['stock_code']}；{plan['steps'][0]['trade_date']} 至 {plan['steps'][-1]['trade_date']}，{plan['sessions']} 个交易日。",
        f"原文可用时间：{plan['case']['available_at']}（仅有回复日期，保守取北京时间日结束）。",
        '真实价格路径来自已归档 BaoStock 股票日收益，市场信号来自截至 t−2 的沪深 300；每类初资 100,000，单边费率 0.1%。', '',
        '| 条件 | 策略 | 收益 % | 相对无文本 pp | 最大回撤 % | 交易次数 | 费用 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for row in result['comparison']:
        lines.append(f"| {MODE_LABELS[row['condition']]} | {ROLE_LABELS[row['role']]} | {row['return_pct']:.4f} | {row['delta_return_pp']:+.4f} | {row['max_drawdown_pct']:.4f} | {row['trades']} | {row['fees']:.2f} |")
    first = next(d for d in result['paths']['reviewed_llm']['trace'] if d['source_active'])
    lines += ['', f"文本首次进入决策：{first['trade_date']}，信息截点 {first['signal_cutoff_date']}，执行参考日 {first['execution_reference_date']}；作用 {plan['information_duration_steps']} 个决策日。",
        f"独立账务复算：{checks['paths']} 路、{checks['market_days']} 个路径日、{checks['agent_decisions']} 次决策，通过。", '',
        '原文、真实模型响应、逐条助手复核、冻结计划、逐日账户和模型输入均单独保存。离线复现不重新调用模型。', '',
        '这是已查看的单案例开发实验，不是独立样本的预测验证。数值文本映射、角色参数、初始资金和 6 日作用期是声明的假设。',
        '收益路径是真实历史数据的当前归档版本，价格指数从 100 归一化；份额为归一化单位。除停牌和现金/持仓限制外，按前一收盘参考指数假定成交，未模拟整手、涨跌停排队、滑点、盘口和市场冲击。',
        'Agent 不改变历史价格；收益差包含路径持仓变化和费用，不能用一家公司推断模型有效、策略优劣或政策因果效果。原自动语义验证门槛保持关闭。', '']
    return '\n'.join(lines)


def run(directory):
    plan = checked_plan(directory)
    record = checked_review(directory,plan)
    result = calculate(plan,record)
    checks = audit(plan,result)
    result['audit'] = checks
    result['plan_sha256'] = file_sha256(directory/'plan.json')
    result['model_sha256'] = file_sha256(directory/'model.json')
    result['review_sha256'] = file_sha256(directory/'review.json')
    checked_plan(directory)
    write_new(directory/'result.json',result)
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer,fieldnames=list(result['comparison'][0]))
    writer.writeheader(); writer.writerows(result['comparison'])
    with (directory/'summary.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        stream.write(buffer.getvalue())
    with (directory/'report.md').open('x',encoding='utf-8') as stream:
        stream.write(render_report(plan,result,checks))
    write_new(directory/'result_manifest.json', {'schema_version':VERSION,'status':'COMPLETED_ACTUAL_OBSERVED_REPLAY',
        'completed_at_utc':datetime.now(timezone.utc).isoformat(),'audit':checks,
        'artifacts':{n:file_sha256(directory/n) for n in ('plan.json','plan_receipt.json','model.json','review.json','result.json','summary.csv','report.md')}})
    return {'status':'COMPLETED_ACTUAL_OBSERVED_REPLAY',**checks,'comparison':result['comparison']}


def verify(directory):
    plan = checked_plan(directory)
    record = checked_review(directory,plan)
    manifest,result = read(directory/'result_manifest.json'),read(directory/'result.json')
    for name,expected in manifest['artifacts'].items():
        if file_sha256(directory/name) != expected:
            raise ValueError('实验产物被改变：'+name)
    rebuilt = calculate(plan,record)
    checks = audit(plan,result)
    for name in ('paths','comparison'):
        if canonical(rebuilt[name]) != canonical(result[name]):
            raise ValueError('离线重放不一致：'+name)
    if checks != result['audit'] or checks != manifest['audit']:
        raise ValueError('独立核验结果不一致')
    return {**checks,'offline_replay_exact':True,'llm_called_now':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('prepare','extract','run','verify'))
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    output = {'prepare':prepare,'extract':extract,'run':run,'verify':verify}[args.stage](args.run_dir.resolve())
    print(json.dumps(output,ensure_ascii=False,indent=2),flush=True)


if __name__ == '__main__':
    main()
