import copy
import gzip
import json
import random
import tempfile
import unittest
from pathlib import Path

from test_feedback_auction import make_fixture, FEEDBACK
from test_semantic_auction_experiment import PARAMETERS, VENUE, sample_steps
from research.data_pipeline.provenance import file_sha256
from research.simulation.agents import AgentParameters
from research.simulation.call_auction import AuctionAccount, CallAuction, LimitOrder
from research.simulation.feedback_auction import simulate_feedback
from research.simulation.participant_market import simulate_market, validate_background, background_demand
from research.simulation.background_experiment import run_experiment, load_summary, experiment_inputs, strategy_signature
from research.simulation.audit_background import audit_directory, load_audit, interval_price, reconstruct_day
from research.simulation.audit_auction import audit_day

ROOT = Path(__file__).parents[1]
CONFIG = json.loads((ROOT / 'research/configs/semantic_background_all126_2020.json').read_text(encoding='utf-8'))
PARENT = json.loads((ROOT / 'research/configs/semantic_feedback_all126_2020.json').read_text(encoding='utf-8'))
CORE = next(s for s in PARENT['scenarios'] if s['scenario_id'] == CONFIG['core_scenario_id'])
AGENTS = [AgentParameters(**a) for a in PARAMETERS]


def run_case(case_index, steps=None, use_text=False, case=None):
    return simulate_market(steps or sample_steps(), AGENTS, CORE, VENUE, FEEDBACK,
                           case or CONFIG['background_cases'][case_index], '000001', use_text)


class BackgroundMarketTest(unittest.TestCase):
    def test_zero_background_and_idle_resources_preserve_strategy_paths(self):
        for enabled in (False, True):
            old = simulate_feedback(sample_steps(), AGENTS, CORE, VENUE, FEEDBACK, enabled)
            result = run_case(0, use_text=enabled)
            names = set(result['participant_specs'])
            for a, b in zip(old['trace'], result['trace'], strict=True):
                for field in ('auction', 'decisions', 'quotes', 'observations', 'feedback'):
                    self.assertEqual(a[field], b[field])
            for index in (1, 6):
                idle = run_case(index, use_text=enabled)
                for a, b in zip(result['trace'], idle['trace'], strict=True):
                    self.assertEqual(strategy_signature(a, names), strategy_signature(b, names))
                self.assertEqual(idle['summary']['background_orders']['requested'], 0)

    def test_background_draw_and_complete_text_ablation_ignore_future_inputs(self):
        steps = sample_steps()
        result = run_case(3, steps, True)
        changed = copy.deepcopy(steps)
        for step in changed:
            step.update(observed_return=-0.9, market_signal=-0.95, estimated_volatility=0.8)
        self.assertEqual(result, run_case(3, changed, True))
        control = run_case(3, steps)
        for step in changed:
            step.update(text_signal=-0.9, text_uncertainty=0.9, text_evidence='replacement')
        self.assertEqual(control, run_case(3, changed))
        case = CONFIG['background_cases'][3]
        account = AuctionAccount(1000000, 500, 500)
        demand = background_demand('000001', 0, 'background_000', account, 10000, (9000,11000), VENUE, case)
        renamed = {**case, 'case_id':'renamed_case'}
        self.assertEqual(demand, background_demand('000001', 0, 'background_000', account, 10000, (9000,11000), VENUE, renamed))
        future = copy.deepcopy(steps)
        future[-1].update(text_signal=-0.8, text_uncertainty=0.7)
        self.assertEqual(result['trace'][:-1], run_case(3, future, True)['trace'][:-1])

    def test_liquidity_distinguishes_background_only_from_strategy_counterparties(self):
        narrow, active = run_case(2), run_case(3)
        self.assertGreater(narrow['summary']['matched_volume'], 0)
        self.assertEqual(narrow['summary']['strategy_orders']['filled'], 0)
        self.assertEqual(narrow['summary']['volume_by_counterparty']['background_background'], narrow['summary']['matched_volume'])
        self.assertGreater(active['summary']['volume_by_counterparty']['strategy_background'], 0)
        self.assertGreater(active['summary']['strategy_orders']['accepted_fill_fraction'], 0)
        self.assertEqual(sum(active['summary']['volume_by_counterparty'].values()), active['summary']['matched_volume'])
        self.assertEqual(active['summary']['initial_cash_minor'], 180000000)
        self.assertEqual(active['summary']['initial_shares'], 14100)
        self.assertEqual(run_case(1)['summary']['initial_cash_minor'], active['summary']['initial_cash_minor'])

    def test_finite_cash_inventory_session_locks_and_halts(self):
        low = {**CONFIG['background_cases'][3], 'initial_cash':1, 'case_id':'almost_no_cash'}
        result = run_case(3, case=low)
        self.assertGreater(result['summary']['background_orders']['cash_clipped_orders'], 0)
        initial_cash = result['summary']['initial_cash_minor']
        for index, day in enumerate(result['trace']):
            auction = day['auction']
            self.assertEqual(auction['cash_total_minor'] + auction['fee_pool_minor'], initial_cash)
            self.assertEqual(auction['shares_total'], 14100)
            self.assertTrue(all(a['cash_minor'] >= 0 and 0 <= a['sellable_shares'] <= a['shares'] for a in auction['accounts'].values()))
            if index==1:
                self.assertEqual(auction['matched_volume'], 0)
                self.assertTrue(all(o['fee_minor']==0 for o in auction['orders']))
            if not auction['matched_volume']:
                self.assertEqual(auction['price_before_minor'],auction['price_after_minor'])
        self.assertEqual(result['summary']['account_summary']['background_000']['initial_wealth_minor'], 5000100)

    def test_interval_sweep_matches_dense_oracle_and_independent_reconstruction(self):
        rng = random.Random(78231)
        settings = {**VENUE,'price_start_minor':100,'lot_size':1,'fee_bps':7}
        for _ in range(200):
            accounts = {f'a{i}':AuctionAccount(rng.randrange(100,10000),rng.randrange(1,20),0) for i in range(8)}
            for a in accounts.values():
                a.sellable_shares=a.shares
            venue = CallAuction(accounts,100,1,1,7,1000)
            orders = [LimitOrder(str(i),name,rng.choice(('buy','sell')),rng.randrange(1,20),rng.randrange(90,111),i) for i,name in enumerate(accounts)]
            auction = venue.clear(0,orders)
            candidates = []
            for price in range(90,111):
                demand=sum(o['accepted_quantity'] for o in auction['orders'] if o['side']=='buy' and o['limit_price_minor']>=price)
                supply=sum(o['accepted_quantity'] for o in auction['orders'] if o['side']=='sell' and o['limit_price_minor']<=price)
                candidates.append((-min(demand,supply),abs(demand-supply),abs(price-100),price,demand-supply))
            best=min(candidates)
            expected=(best[3],-best[0],best[4]) if best[0] else (100,0,next(r[4] for r in candidates if r[3]==100))
            self.assertEqual(interval_price(auction,settings),expected)
            previous={'accounts':{n:a.__dict__ for n,a in accounts.items()},'price_minor':100,'fee_pool_minor':0,
                      'initial_cash_minor':sum(a.cash_minor for a in accounts.values()),'initial_shares':sum(a.shares for a in accounts.values())}
            record={'auction':auction,'trade_date':'2020-07-03','signal_cutoff_date':'2020-07-01','execution_reference_date':'2020-07-02'}
            self.assertEqual(reconstruct_day(record,previous,settings,0),audit_day(record,previous,settings,0))
            changed=copy.deepcopy(record)
            changed['auction']['accounts']['a0']['cash_minor']+=1
            with self.assertRaisesRegex(ValueError,'balances'):
                reconstruct_day(changed,previous,settings,0)

    def test_full_source_chain_reexecution_audit_and_semantic_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            make_fixture(root)
            cfg={**CONFIG,'feedback_config':'feedback.json','run_id':'background_fixture'}
            path=root/'background.json'
            path.write_text(json.dumps(cfg),encoding='utf-8')
            summary=run_experiment(path,root/'output')
            self.assertEqual(summary,run_experiment(path,root/'rerun'))
            self.assertEqual(summary,load_summary(root/'output'))
            self.assertEqual((summary['paths'],summary['ledger_rows']), (64,384))
            self.assertEqual(summary['no_background_parity_paths'],8)
            self.assertEqual(summary['idle_resources_parity_paths'],16)
            manifests=[json.loads((root/name/'background_manifest.json').read_text(encoding='utf-8')) for name in ('output','rerun')]
            self.assertEqual(manifests[0]['artifacts'],manifests[1]['artifacts'])
            audit=audit_directory(root/'output',path,root/'audit')
            self.assertEqual(audit,load_audit(root/'output',root/'audit'))
            self.assertEqual(audit['halted_sessions'],32)
            self.assertEqual(audit['sampled_full_tick_sessions'],64)
            ledger=root/'output/background_ledger.jsonl.gz'
            with gzip.open(ledger,'rt',encoding='utf-8') as stream:
                rows=[json.loads(line) for line in stream]
            changed=next(r for r in rows if r['case_id']=='active_quote25')
            changed['background_demands']['background_000']['target_shares']+=100
            with gzip.open(ledger,'wt',encoding='utf-8') as stream:
                for row in rows:
                    stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            manifest_path=root/'output/background_manifest.json'
            manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['artifacts']['background_ledger.jsonl.gz']['sha256']=file_sha256(ledger)
            manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'background demand differs'):
                audit_directory(root/'output',path,root/'tampered_audit')

    def test_budget_controls_and_parameters_are_explicit(self):
        malformed={**CONFIG['background_cases'][0], 'initial_cash':1}
        with self.assertRaisesRegex(ValueError,'zero resources'):
            validate_background(malformed,100)
        malformed={**CONFIG['background_cases'][3], 'initial_cash':1.001}
        with self.assertRaisesRegex(ValueError,'two decimal'):
            validate_background(malformed,100)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            make_fixture(root)
            cfg={**CONFIG,'feedback_config':'feedback.json'}
            cfg['background_cases']=[c for c in cfg['background_cases'] if c['case_id']!='idle_lowcash']
            path=root/'bad.json'
            path.write_text(json.dumps(cfg),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'matched idle control'):
                experiment_inputs(path)


if __name__=='__main__':
    unittest.main()
