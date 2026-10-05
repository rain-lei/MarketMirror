import unittest

from research.simulation.verify_pre_wuhan_allocation_attribution_2019 import CheckedSum, validate_position


def fixture():
    return {'component_share_equivalents':{'base':-100.,'belief':40.},'factual_delta_share_equivalent':-60.,
            'portfolio_belief_score':0.,'asset_belief':.1,'current_weight':.3,'desired_weight':.2,
            'submitted_side':'sell','submitted_quantity':100,'accepted_quantity':100,'filled_quantity':0}


class AllocationStatisticsTest(unittest.TestCase):
    def test_compensated_sum_retains_small_term_between_large_opposing_terms(self):
        total=CheckedSum()
        for value in (1e16,1.,-1e16):total.add(value)
        self.assertEqual(total.total(),1.)
        with self.assertRaisesRegex(ValueError,'nonfinite'):total.add(float('nan'))

    def test_component_omission_or_non_reconciling_sum_fails(self):
        row=fixture();validate_position(row,['base','belief'])
        row['component_share_equivalents']['belief']=50.
        with self.assertRaisesRegex(ValueError,'telescope'):validate_position(row,['base','belief'])
        row=fixture();row['component_share_equivalents'].pop('belief')
        with self.assertRaisesRegex(ValueError,'membership'):validate_position(row,['base','belief'])

    def test_submission_acceptance_and_execution_cannot_be_confused(self):
        row=fixture();row['filled_quantity']=101
        with self.assertRaisesRegex(ValueError,'flow differs'):validate_position(row,['base','belief'])
        row=fixture();row.update(submitted_quantity=0,accepted_quantity=0,filled_quantity=0,submitted_side='hold')
        validate_position(row,['base','belief'])
        row['accepted_quantity']=100
        with self.assertRaises(ValueError):validate_position(row,['base','belief'])


if __name__ == '__main__':unittest.main()
