"""Explain a completed observed replay; plotting is an optional local dependency."""
from __future__ import annotations

import argparse
from datetime import datetime
import math
from pathlib import Path
import sys

from ..data_pipeline.provenance import file_sha256
from .single_observed_case import MODE_LABELS, ROLE_LABELS, read, verify, write_new


def attribution(plan, result):
    baseline = result['paths']['no_text']['trace']
    output = []
    for mode in ('keywords', 'reviewed_llm'):
        trace = result['paths'][mode]['trace']
        for parameter in plan['agents']:
            name = parameter['name']
            active_pnl, later_pnl, fee_difference, changed_orders = 0.0, 0.0, 0.0, 0
            days = []
            for day, control in zip(trace, baseline):
                row, base = day['agents'][name], control['agents'][name]
                market_pnl = ((row['shares']-base['shares'])
                              * day['price_index_before'] * day['observed_return'])
                extra_fee = row['fees_paid']-base['fees_paid']
                if day['source_active']:
                    active_pnl += market_pnl
                else:
                    later_pnl += market_pnl
                fee_difference += extra_fee
                changed_orders += int(not math.isclose(row['filled_shares'],base['filled_shares'],abs_tol=1e-8))
                days.append({'trade_date':day['trade_date'],'source_active':day['source_active'],
                    'realized_return':day['observed_return'],'market_pnl_difference':market_pnl,
                    'fee_difference':extra_fee,'closing_weight_difference':row['closing_weight']-base['closing_weight']})
            actual = trace[-1]['agents'][name]['closing_wealth']-baseline[-1]['agents'][name]['closing_wealth']
            reconstructed = active_pnl+later_pnl-fee_difference
            if not math.isclose(actual,reconstructed,abs_tol=1e-7):
                raise ValueError('收益差分解与实际账户结果不一致')
            output.append({'condition':mode,'role':parameter['role'],
                'active_days_market_pnl_difference':active_pnl,
                'other_days_market_pnl_difference':later_pnl,'fee_difference':fee_difference,
                'final_wealth_difference':actual,'changed_order_days':changed_orders,'daily':days})
    return {'baseline':'no_text','decomposition':'difference in units times observed price change minus fee difference',
            'all_account_differences_reconciled':True,'rows':output}


def plot(directory, plan, result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update({'font.family':['Microsoft YaHei','DejaVu Sans'],
                         'axes.unicode_minus':False,'font.size':10,'svg.fonttype':'none'})
    colors = {'no_text':'#64748b','keywords':'#d28b16','reviewed_llm':'#2563eb'}
    dates = [datetime.fromisoformat(d['trade_date']) for d in plan['steps']]
    active = [d for d in result['paths']['reviewed_llm']['trace'] if d['source_active']]
    left, right = datetime.fromisoformat(active[0]['trade_date']), datetime.fromisoformat(active[-1]['trade_date'])
    fig, axes = plt.subplots(2,3,figsize=(15,8),layout='constrained',sharex=True)
    fig.patch.set_facecolor('#f8fafc')
    for column, parameter in enumerate(plan['agents']):
        name = parameter['name']
        for mode,path in result['paths'].items():
            nav = [d['agents'][name]['closing_wealth']/parameter['initial_cash'] for d in path['trace']]
            weight = [d['agents'][name]['closing_weight'] for d in path['trace']]
            axes[0,column].plot(dates,nav,color=colors[mode],label=MODE_LABELS[mode],lw=1.8)
            axes[1,column].step(dates,weight,where='post',color=colors[mode],lw=1.5)
        axes[0,column].set_title(ROLE_LABELS[parameter['role']],fontweight='bold',pad=12)
        axes[0,column].axhline(1,color='#cbd5e1',lw=.8,ls='--')
        axes[1,column].yaxis.set_major_formatter(PercentFormatter(1))
        for axis in axes[:,column]:
            axis.axvspan(left,right,color='#dbeafe',alpha=.5,zorder=0)
            axis.grid(axis='y',color='#e2e8f0',lw=.7)
            axis.set_axisbelow(True)
            axis.spines[['top','right']].set_visible(False)
            axis.spines[['left','bottom']].set_color('#cbd5e1')
            axis.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO,interval=2))
            axis.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    axes[0,0].set_ylabel('账户净值（初始资金 = 1）')
    axes[1,0].set_ylabel('收盘持仓比例')
    axes[0,0].legend(frameon=False,loc='lower left',fontsize=9)
    fig.suptitle('MarketMirror · 300294 真实历史行情实验\n2020-07-22 — 2020-09-01｜蓝色区域：文本作用的 6 个决策日',
                 fontsize=15,fontweight='bold',color='#0f172a')
    for suffix in ('png','svg'):
        path = directory/f'comparison.{suffix}'
        if path.exists():
            raise ValueError('图形产物已存在，不覆盖历史结果')
        fig.savefig(path,dpi=170,facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--plot-packages',type=Path,help='Optional directory installed with pip --target.')
    parser.add_argument('--plot',action='store_true')
    args = parser.parse_args()
    directory = args.run_dir.resolve()
    verify(directory)
    plan,result = read(directory/'plan.json'),read(directory/'result.json')
    analysis = attribution(plan,result)
    write_new(directory/'attribution.json',analysis)
    names = ['attribution.json']
    if args.plot:
        if args.plot_packages:
            sys.path.insert(0,str(args.plot_packages.resolve()))
        plot(directory,plan,result)
        names += ['comparison.png','comparison.svg']
    write_new(directory/'analysis_manifest.json',{
        'result_manifest_sha256':file_sha256(directory/'result_manifest.json'),
        'analysis_code_sha256':file_sha256(Path(__file__)),
        'artifacts':{name:file_sha256(directory/name) for name in names},
        'all_account_differences_reconciled':True})
    print('Account differences reconciled; analysis artifacts saved.')


if __name__ == '__main__':
    main()
