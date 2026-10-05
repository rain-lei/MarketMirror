import copy
import unittest

from research.simulation.agents import AgentParameters
from research.simulation.allocation_attribution import COMPONENTS, decompose, submitted_quantity
from research.simulation.audit_pre_wuhan_allocation_attribution_2019 import initial_from_specs
from research.simulation.portfolio_auction import PortfolioAccount
from research.simulation.portfolio_market import portfolio_decision


def fixture(*, belief_a=0, belief_b=0, base=.3, shares=200, risk_budget=.5, maximum=1,
            minimum=0, turnover=1, role="aggressive", asset_cap=1, wait=1, session=0):
    agent = AgentParameters(name='actor', role=role, initial_cash=10000, base_weight=base, max_weight=maximum,
                            max_turnover=turnover, risk_budget=risk_budget, market_sensitivity=1, text_sensitivity=0,
                            uncertainty_aversion=0, confirmation_steps=wait, rebalance_interval=1, min_trade_weight=minimum)
    account = PortfolioAccount({'a': 600000, 'b': 600000}, {'a': shares, 'b': shares}, {'a': shares, 'b': shares})
    prices = {'a': 1000, 'b': 1000}
    observations = {a: {'market_signal': value, 'text_signal': 0, 'text_uncertainty': 0} for a, value in [('a',belief_a),('b',belief_b)]}
    covariance = {'a': {'a': .0004, 'b': 0}, 'b': {'a': 0, 'b': .0004}}
    decision = portfolio_decision(agent, {'momentum_loading': 1, 'market_bias': 0}, account, prices,
                                  observations, covariance, {'institutional_asset_cap':asset_cap}, {'sign':0,'streak':0},session,False)
    parameters = dict(agent.__dict__)
    return parameters, decision, covariance


class AllocationAttributionTest(unittest.TestCase):
    def test_neutral_base_anchor_explains_reallocation_without_any_view(self):
        p, decision, covariance = fixture(base=.2, shares=200)
        analysis = decompose(p, decision, covariance)
        self.assertEqual(analysis['portfolio_belief_score'], 0)
        self.assertAlmostEqual(analysis['component_weight_changes']['base_anchor_minus_current']['a'], -.025)
        self.assertEqual(analysis['component_weight_changes']['belief_total_exposure'], {'a':0,'b':0})
        self.assertAlmostEqual(sum(analysis['factual_order_weight_changes'].values()), -.05)

    def test_score_and_relative_allocation_are_distinct_and_relative_sum_is_zero(self):
        p, decision, covariance = fixture(belief_a=.2,belief_b=-.2)
        analysis = decompose(p, decision, covariance)
        self.assertEqual(analysis['portfolio_belief_score'], 0)
        self.assertAlmostEqual(sum(analysis['component_weight_changes']['belief_relative_allocation'].values()), 0)
        self.assertGreater(analysis['component_weight_changes']['belief_relative_allocation']['a'], 0)
        self.assertLess(analysis['component_weight_changes']['belief_relative_allocation']['b'], 0)

    def test_common_negative_view_changes_total_exposure_without_relative_rotation(self):
        args = fixture(belief_a=-.2,belief_b=-.2)
        analysis = decompose(*args)
        self.assertAlmostEqual(sum(analysis['component_weight_changes']['belief_total_exposure'].values()), -.08)
        self.assertEqual(analysis['component_weight_changes']['belief_relative_allocation'], {'a':0,'b':0})

    def test_total_risk_cap_is_separate_from_static_base_restoration(self):
        analysis = decompose(*fixture(base=.6, risk_budget=.3 * (.0002 ** .5)))
        self.assertAlmostEqual(sum(analysis['component_weight_changes']['total_risk_weight_cap'].values()), -.3)
        self.assertAlmostEqual(sum(analysis['component_weight_changes']['base_anchor_minus_current'].values()), .35)

    def test_concentration_clip_is_recorded_without_renormalizing_lost_exposure(self):
        analysis = decompose(*fixture(base=.8,role='institutional',asset_cap=.15))
        self.assertAlmostEqual(sum(analysis['component_weight_changes']['asset_concentration_clip'].values()), -.5)
        self.assertEqual(analysis['factual_desired_weights'], {'a':.15,'b':.15})

    def test_risk_liquidation_is_not_canceled_by_minimum_or_turnover_gates(self):
        for minimum, turnover in ((.5,.5),(.05,.1)):
            p, decision, cov = fixture(base=.1,shares=500,risk_budget=.001,minimum=minimum,turnover=turnover)
            self.assertTrue(decision['risk_liquidation'])
            analysis = decompose(p,decision,cov)
            self.assertTrue(all(v <= 0 for v in analysis['factual_order_weight_changes'].values()))
            self.assertEqual(analysis['component_weight_changes']['minimum_trade_gate'], {'a':0,'b':0})
            self.assertEqual(analysis['component_weight_changes']['turnover_cap'], {'a':0,'b':0})

    def test_wait_cancels_the_proposal_but_keeps_its_components_visible(self):
        analysis = decompose(*fixture(belief_a=.1,belief_b=.1,wait=2))
        self.assertEqual(analysis['factual_order_weight_changes'], {'a':0,'b':0})
        self.assertNotEqual(analysis['component_weight_changes']['confirmation_rebalance_wait'], {'a':0,'b':0})

    def test_minimum_gate_and_turnover_cap_have_separate_reconciling_stages(self):
        minimum = decompose(*fixture(base=.26,minimum=.02))
        self.assertEqual(minimum['factual_order_weight_changes'], {'a':0,'b':0})
        self.assertAlmostEqual(sum(minimum['component_weight_changes']['minimum_trade_gate'].values()), -.01)
        turnover = decompose(*fixture(base=.8,turnover=.1))
        self.assertAlmostEqual(sum(abs(v) for v in turnover['factual_order_weight_changes'].values()), .1)
        self.assertLess(sum(turnover['component_weight_changes']['turnover_cap'].values()), 0)

    def test_changed_final_weights_or_order_delta_fails(self):
        for field in ('desired_weights','order_weight_changes'):
            args = fixture();args[1][field]['a'] += .05
            with self.assertRaisesRegex(ValueError,'attribution differs'):
                decompose(*args)

    def test_inputs_are_not_modified_and_all_components_telescope(self):
        args = fixture(belief_a=.3,belief_b=-.2,turnover=.02);old=copy.deepcopy(args)
        analysis = decompose(*args)
        self.assertEqual(args,old)
        self.assertEqual(set(analysis['component_weight_changes']),set(COMPONENTS))
        for a in ('a','b'):
            self.assertAlmostEqual(sum(v[a] for v in analysis['component_weight_changes'].values()),args[1]['order_weight_changes'][a])

    def test_lot_floor_and_inventory_limit_are_not_mislabeled_as_execution(self):
        self.assertEqual(submitted_quantity(.019,1000000,100,1000,100),('buy',100))
        self.assertEqual(submitted_quantity(-.5,1000000,100,150,100),('sell',150))
        self.assertEqual(submitted_quantity(0,1000000,100,150,100),('hold',0))
        with self.assertRaises(ValueError):submitted_quantity(float('nan'),1000000,100,150,100)

    def test_initial_resources_are_rebuilt_from_specs_without_mixing_cash(self):
        specs={'aggressive_00':{'kind':'strategy','parameters':{'initial_cash':100}},
               'background_a_000':{'kind':'background','asset':'a'}}
        base={'core':{'inventory':[200]},'background':{'initial_cash':50,'initial_shares':100},'venue':{'price_start_minor':1000}}
        state=initial_from_specs(specs,['a','b'],base,{'cash_mode':'separated'})
        self.assertEqual(state['initial_cash_minor'],25000)
        self.assertEqual(state['initial_shares'],{'a':300,'b':200})
        self.assertEqual(state['accounts']['background_a_000']['wallets'],{'a':5000,'b':0})


if __name__ == '__main__':
    unittest.main()
