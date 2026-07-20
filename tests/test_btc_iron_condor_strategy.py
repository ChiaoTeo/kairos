from decimal import Decimal
import unittest

from kairos.domain.product import ProductType
from kairos.domain.strategy_contract import StrategyLifecycle
from kairos.strategies.btc_iron_condor import BtcIronCondorConfig, BtcIronCondorStrategy


class BtcIronCondorStrategyTest(unittest.TestCase):
    def test_strategy_spec_preserves_study_and_economic_semantics(self):
        strategy=BtcIronCondorStrategy(study_spec_hash="abc123")
        spec=strategy.strategy_spec
        self.assertEqual(spec.lifecycle,StrategyLifecycle.DRAFT)
        self.assertEqual(spec.products,(ProductType.CRYPTO_OPTION,))
        self.assertEqual(spec.study_spec_hash,"abc123")
        self.assertIn("short_gamma",spec.strategy_archetypes)
        self.assertIn("synchronous_quotes",spec.required_data_capabilities)

    def test_config_rejects_invalid_risk_budget(self):
        with self.assertRaises(ValueError): BtcIronCondorConfig(risk_budget_fraction=Decimal("1.1"))


if __name__=="__main__":unittest.main()
