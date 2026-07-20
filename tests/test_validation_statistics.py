from datetime import date
import unittest

from kairos.research_platform.validation import (
    assess_robustness,block_bootstrap_mean_ci,chronological_split,
    validate_predictability,walk_forward_splits,
)


class ValidationStatisticsTest(unittest.TestCase):
    def test_chronological_and_walk_forward_splits_are_embargoed(self):
        split=chronological_split(date(2020,1,1),date(2021,1,1),embargo_days=7)
        self.assertGreaterEqual((split.test[0]-split.validation[1]).days,7)
        self.assertTrue(tuple(walk_forward_splits(date(2020,1,1),date(2021,1,1),development_days=100,test_days=30,step_days=30,embargo_days=7)))

    def test_predictability_reports_rank_linear_and_overlap_aware_inference(self):
        feature=list(range(100));target=[-value for value in feature]
        result=validate_predictability(feature,target,high_threshold=49,expected_sign=-1,block_length=5,minimum_observations=20)
        self.assertAlmostEqual(result.pearson,-1);self.assertAlmostEqual(result.spearman,-1);self.assertTrue(result.supported)
        self.assertLess(result.confidence_interval[1],0)

    def test_bootstrap_is_seed_deterministic_and_robustness_is_explicit(self):
        self.assertEqual(block_bootstrap_mean_ci(range(20),3,seed=1),block_bootstrap_mean_ci(range(20),3,seed=1))
        result=assess_robustness([1,1,-1,1],[1,-1,1],[.1,.05],minimum_stress_metric=0)
        self.assertTrue(result.parameter_stable);self.assertTrue(result.regime_stable);self.assertTrue(result.stress_cost_passed)


if __name__=="__main__":unittest.main()
