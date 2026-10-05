import copy
import unittest

from research.simulation.allocation_belief_sources import decompose_sources


def fixture():
    parameters={'market_sensitivity':.5,'base_weight':.3}
    profile={'market_bias':.1,'momentum_loading':1}
    observations={'a':{'market_signal':-.4,'scenario_shock':.2,'text_signal':0,'text_uncertainty':0},
                  'b':{'market_signal':-.4,'scenario_shock':.2,'text_signal':0,'text_uncertainty':0}}
    receipts={a:{'applied_belief_signal':.2} for a in observations}
    decision={'beliefs':{'a':.05,'b':.05},'issuer_base_beliefs':{'a':-.05,'b':-.05},'issuer_belief_contributions':{'a':.1,'b':.1}}
    return parameters,profile,observations,receipts,decision


class AllocationBeliefSourcesTest(unittest.TestCase):
    def test_known_bias_momentum_shared_and_private_exposure_changes_reconcile(self):
        result=decompose_sources(*fixture());parts=result['component_equal_asset_weight_changes']
        for k,v in {'profile_bias':.01,'own_price_momentum_given_bias':-.04,'shared_shocks_given_prior':.02,'private_issuer_given_prior':.02}.items():
            self.assertAlmostEqual(parts[k],v)
        self.assertAlmostEqual(sum(parts.values()),.01)

    def test_unreceived_private_signal_has_exactly_no_private_stage(self):
        args=fixture()
        for r in args[3].values():r['applied_belief_signal']=0
        args[4]['beliefs']={'a':-.05,'b':-.05};args[4]['issuer_belief_contributions']={'a':0,'b':0}
        self.assertEqual(decompose_sources(*args)['component_equal_asset_weight_changes']['private_issuer_given_prior'],0)

    def test_clipping_is_preserved_and_private_signal_cannot_bypass_one(self):
        args=fixture();args[1]['market_bias']=.9
        for o in args[2].values():o.update(market_signal=.4,scenario_shock=.2)
        args[4].update(beliefs={'a':.5,'b':.5},issuer_base_beliefs={'a':.5,'b':.5},issuer_belief_contributions={'a':0,'b':0})
        result=decompose_sources(*args)
        self.assertEqual(result['component_equal_asset_weight_changes']['private_issuer_given_prior'],0)
        self.assertAlmostEqual(result['component_equal_asset_weight_changes']['own_price_momentum_given_bias'],.01)

    def test_zero_floor_is_retained_instead_of_negative_equity_exposure(self):
        args=fixture();args[0]['base_weight']=.01;args[1]['market_bias']=-.9
        for o in args[2].values():o.update(market_signal=0,scenario_shock=0)
        for r in args[3].values():r['applied_belief_signal']=0
        args[4].update(beliefs={'a':-.45,'b':-.45},issuer_base_beliefs={'a':-.45,'b':-.45},issuer_belief_contributions={'a':0,'b':0})
        result=decompose_sources(*args)
        self.assertEqual(result['stage_total_exposures_before_risk_cap'][1:],[0,0,0,0])
        self.assertAlmostEqual(result['component_equal_asset_weight_changes']['profile_bias'],-.005)

    def test_wrong_base_or_private_contribution_is_rejected(self):
        for key in ('beliefs','issuer_base_beliefs','issuer_belief_contributions'):
            args=fixture();args[4][key]['a'] += .02
            with self.assertRaisesRegex(ValueError,'differs from sources'):decompose_sources(*args)

    def test_text_is_not_silently_added_and_source_inputs_are_unchanged(self):
        args=fixture();saved=copy.deepcopy(args);decompose_sources(*args);self.assertEqual(args,saved)
        args[2]['a']['text_signal']=.2
        with self.assertRaisesRegex(ValueError,'disabled text'):decompose_sources(*args)


if __name__=='__main__':unittest.main()
