import copy
import gzip
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from research.simulation.call_auction import AuctionAccount, CallAuction, LimitOrder
from research.simulation.portfolio_auction import PortfolioAccount, PortfolioAuction
from research.simulation.portfolio_audit import audit_portfolio_day
from research.simulation.portfolio_market import simulate_portfolio, portfolio_decision
from research.simulation.portfolio_experiment import run_experiment, load_summary
from research.simulation.audit_portfolio import audit_directory, load_audit
from research.data_pipeline.provenance import file_sha256
from research.simulation.feedback_auction import NEUTRAL
from test_feedback_auction import make_fixture
from research.simulation.agents import AgentParameters
from test_background_market import CONFIG, CORE, FEEDBACK
from test_semantic_auction_experiment import PARAMETERS, VENUE, sample_steps


def prior(venue):
    return {"accounts": {n: asdict(a) for n,a in venue.accounts.items()}, "prices": dict(venue.prices),
            "fee_pool_minor": venue.fee_pool_minor, "initial_cash_minor": venue.initial_cash_minor,
            "initial_shares": dict(venue.initial_shares)}


def record(result):
    return {"trade_date":"2020-07-03", "signal_cutoff_date":"2020-07-01", "execution_reference_date":"2020-07-02",
            "portfolio_auction":result}


class PortfolioMarketTest(unittest.TestCase):
    def test_concentration_clipping_rechecks_risk_after_removing_a_hedge(self):
        agent=replace(AgentParameters(**PARAMETERS[2]),base_weight=0.9,max_weight=0.95,risk_budget=0.005,
                      market_sensitivity=2,text_sensitivity=0,confirmation_steps=1,rebalance_interval=1,max_turnover=1,min_trade_weight=0)
        account=PortfolioAccount({'shared':100000},{'A':0,'B':0},{'A':0,'B':0})
        observations={a:{'market_signal':v,'text_signal':0,'text_uncertainty':0} for a,v in [('A',0),('B',0.8)]}
        matrix={'A':{'A':0.01,'B':-0.00195},'B':{'A':-0.00195,'B':0.0004}}
        decision=portfolio_decision(agent,NEUTRAL,account,{'A':100,'B':100},observations,matrix,
             {'case_id':'cap','cash_mode':'shared','institutional_asset_cap':0.2},{'sign':0,'streak':0},0,True)
        self.assertIn('actual_portfolio_risk_cap',decision['reasons'])
        self.assertLessEqual(decision['desired_portfolio_risk'],agent.risk_budget+1e-12)
        self.assertTrue(all(v<=0.2 for v in decision['desired_weights'].values()))

    def test_full_source_chain_reexecution_and_rehashed_escrow_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);make_fixture(root)
            bgcfg={**CONFIG,'feedback_config':'feedback.json'}
            (root/'background.json').write_text(json.dumps(bgcfg),encoding='utf-8')
            cfg=json.loads((Path(__file__).parents[1]/'research/configs/semantic_portfolio_all126_2020.json').read_text(encoding='utf-8'))
            cfg.update(background_config='background.json',basket_size=2)
            config=root/'portfolio.json';config.write_text(json.dumps(cfg),encoding='utf-8')
            summary=run_experiment(config,root/'output')
            self.assertEqual(summary,run_experiment(config,root/'repeat'))
            self.assertEqual(summary,load_summary(root/'output'))
            self.assertEqual((summary['paths'],summary['ledger_rows'],summary['asset_call_rows']),(16,96,192))
            a=json.loads((root/'output/portfolio_manifest.json').read_text(encoding='utf-8'))
            b=json.loads((root/'repeat/portfolio_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(a['artifacts'],b['artifacts'])
            audited=audit_directory(root/'output',config,root/'audit')
            self.assertEqual(audited,load_audit(root/'output',root/'audit'))
            self.assertEqual(audited['sampled_dense_asset_calls'],32)
            self.assertTrue(audited['checks']['grouped_results'])
            summary_file=root/'output/portfolio_summary.json'
            results_file=root/'output/portfolio_results.json'
            manifest_file=root/'output/portfolio_manifest.json'
            original={p:p.read_bytes() for p in (summary_file,results_file,manifest_file)}
            for p in (summary_file,results_file):
                data=json.loads(p.read_text(encoding='utf-8'))
                data['grouped'][0]['no_text_cash_clipped_orders']+=1
                p.write_text(json.dumps(data),encoding='utf-8')
            changed=json.loads(manifest_file.read_text(encoding='utf-8'))
            for p in (summary_file,results_file):
                changed['artifacts'][p.name]['sha256']=file_sha256(p)
            manifest_file.write_text(json.dumps(changed),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'portfolio group integer totals differ'):
                audit_directory(root/'output',config,root/'bad_group_audit')
            for p,contents in original.items():p.write_bytes(contents)
            ledger=root/'output/portfolio_ledger.jsonl.gz'
            with gzip.open(ledger,'rt',encoding='utf-8') as stream:rows=[json.loads(line) for line in stream]
            row=next(r for r in rows if r['portfolio_auction']['escrows'])
            row['portfolio_auction']['escrows'][0]['escrow_minor']+=1
            with gzip.open(ledger,'wt',encoding='utf-8') as stream:
                for row in rows:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            a['artifacts']['portfolio_ledger.jsonl.gz']['sha256']=file_sha256(ledger)
            (root/'output/portfolio_manifest.json').write_text(json.dumps(a),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'escrow'):
                audit_directory(root/'output',config,root/'damaged_audit')

    def test_shared_cash_is_escrowed_once_and_separate_cash_cannot_transfer(self):
        settings={**VENUE,'price_start_minor':100,'lot_size':1}
        def run(shared):
            accounts={'buyer':PortfolioAccount({'shared':1002} if shared else {'A':501,'B':501}, {'A':0,'B':0},{'A':0,'B':0}),
                      'seller':PortfolioAccount({'A':0,'B':0},{'A':20,'B':20},{'A':20,'B':20})}
            venue=PortfolioAuction(accounts,['A','B'],settings)
            before=prior(venue)
            books={'A':[LimitOrder('a','buyer','buy',8,100,0),LimitOrder('as','seller','sell',20,100,1)],
                   'B':[LimitOrder('b','buyer','buy',2,100,0),LimitOrder('bs','seller','sell',20,100,1)]}
            result=venue.clear(0,books,{'A':True,'B':True})
            self.assertEqual(prior(venue),audit_portfolio_day(record(result),before,settings,0,True))
            return venue,result
        shared,s=run(True)
        separated,i=run(False)
        self.assertEqual(shared.accounts['buyer'].shares,{'A':8,'B':2})
        self.assertEqual(separated.accounts['buyer'].shares,{'A':5,'B':2})
        self.assertLessEqual(sum(e['escrow_minor'] for e in s['escrows']),1002)
        damaged=copy.deepcopy(s)
        damaged['escrows'][0]['escrow_minor']+=1
        with self.assertRaisesRegex(ValueError,'escrow'):
            accounts={'buyer':PortfolioAccount({'shared':1002},{'A':0,'B':0},{'A':0,'B':0}),
                      'seller':PortfolioAccount({'A':0,'B':0},{'A':20,'B':20},{'A':20,'B':20})}
            audit_portfolio_day(record(damaged),prior(PortfolioAuction(accounts,['A','B'],settings)),settings,0)

    def test_sale_proceeds_do_not_finance_same_session_and_invalid_books_are_atomic(self):
        settings={**VENUE,'price_start_minor':100,'lot_size':1,'fee_bps':0}
        accounts={'switcher':PortfolioAccount({'shared':0},{'A':10,'B':0},{'A':10,'B':0}),
                  'other':PortfolioAccount({'shared':1000},{'A':0,'B':10},{'A':0,'B':10})}
        venue=PortfolioAuction(accounts,['A','B'],settings)
        books={'A':[LimitOrder('s','switcher','sell',10,100,0),LimitOrder('ob','other','buy',10,100,1)],
               'B':[LimitOrder('b','switcher','buy',10,100,0),LimitOrder('os','other','sell',10,100,1)]}
        before=prior(venue)
        invalid=copy.deepcopy(books);invalid['B'].append(books['B'][0])
        with self.assertRaises(ValueError):venue.clear(0,invalid,{'A':True,'B':True})
        self.assertEqual(before,prior(venue));self.assertEqual(venue.session,-1)
        cleared=venue.clear(0,books,{'A':True,'B':True})
        self.assertEqual(cleared['asset_calls']['B']['matched_volume'],0)
        self.assertEqual(venue.accounts['switcher'].wallets['shared'],1000)
        next_result=venue.clear(1,{'A':[],'B':books['B']},{'A':True,'B':True})
        self.assertEqual(next_result['asset_calls']['B']['matched_volume'],10)
        self.assertEqual(venue.accounts['switcher'].sellable['B'],0)
        venue.clear(2,{'A':[],'B':[]},{'A':True,'B':True})
        self.assertEqual(venue.accounts['switcher'].sellable['B'],10)

    def test_single_asset_matches_archived_engine_and_input_order_is_invariant(self):
        settings={**VENUE,'price_start_minor':100,'lot_size':1}
        accounts={'buyer':AuctionAccount(1200,0,0),'seller':AuctionAccount(200,10,10)}
        orders=[LimitOrder('buy','buyer','buy',8,101,0),LimitOrder('sell','seller','sell',9,99,1)]
        old=CallAuction(accounts,100,1,1,settings['fee_bps'],settings['price_band_bps'])
        expected=old.clear(0,orders)
        wrapped=PortfolioAuction({n:PortfolioAccount({'shared':a.cash_minor},{'A':a.shares},{'A':a.sellable_shares}) for n,a in accounts.items()},['A'],settings)
        actual=wrapped.clear(0,{'A':list(reversed(orders))},{'A':True})
        for field in ('orders','trades','price_after_minor','matched_volume','fee_pool_minor'):
            self.assertEqual(actual['asset_calls']['A'][field],expected[field])
        for n,a in old.accounts.items():
            self.assertEqual(wrapped.accounts[n].wallets['shared'],a.cash_minor)
            self.assertEqual(wrapped.accounts[n].shares['A'],a.shares)

    def test_portfolio_constraints_future_isolation_and_full_text_ablation(self):
        steps=sample_steps()
        joined={'000001':steps,'000002':copy.deepcopy(steps),'000003':copy.deepcopy(steps)}
        agents=[AgentParameters(**p) for p in PARAMETERS]
        case={'case_id':'shared','cash_mode':'shared','institutional_asset_cap':0.20}
        bg=CONFIG['background_cases'][3]
        result=simulate_portfolio(joined,agents,CORE,bg,VENUE,FEEDBACK,case,True)
        for day in result['trace']:
            for name,d in day['decisions'].items():
                self.assertLessEqual(sum(d['desired_weights'].values()),d['risk_weight_cap']+1e-12)
                if name.startswith('institutional'):
                    self.assertTrue(all(v<=0.20+1e-12 for v in d['desired_weights'].values()))
                if not d['risk_liquidation']:
                    agent=next(a for a in result['participant_specs'].values() if a.get('parameters',{}).get('name')==name)
                    self.assertLessEqual(sum(abs(v) for v in d['order_weight_changes'].values()),agent['parameters']['max_turnover']+1e-12)
        changed=copy.deepcopy(joined)
        for rows in changed.values():
            for s in rows:s.update(observed_return=-0.9,market_signal=-0.9,estimated_volatility=0.8)
        self.assertEqual(result,simulate_portfolio(changed,agents,CORE,bg,VENUE,FEEDBACK,case,True))
        control=simulate_portfolio(joined,agents,CORE,bg,VENUE,FEEDBACK,case,False)
        for rows in changed.values():
            for s in rows:s.update(text_signal=-0.8,text_uncertainty=0.9,text_evidence='changed')
        self.assertEqual(control,simulate_portfolio(changed,agents,CORE,bg,VENUE,FEEDBACK,case,False))
        changed=copy.deepcopy(joined);changed['000001'][-1]['text_signal']=-1
        self.assertEqual(result['trace'][:-1],simulate_portfolio(changed,agents,CORE,bg,VENUE,FEEDBACK,case,True)['trace'][:-1])


if __name__=='__main__':unittest.main()
