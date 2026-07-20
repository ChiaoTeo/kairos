from tempfile import TemporaryDirectory
import unittest

from kairospy.domain.strategy_contract import StrategyLifecycle
from kairospy.strategies.specs import builtin_strategy_specs,register_builtin_strategies


class BuiltinStrategySpecsTest(unittest.TestCase):
    def test_all_reference_strategies_have_draft_specs_and_policies(self):
        values=builtin_strategy_specs()
        self.assertEqual(len({spec.strategy_id for spec,_ in values}),len(values))
        self.assertTrue(all(spec.lifecycle is StrategyLifecycle.DRAFT for spec,_ in values))
        self.assertTrue(all(policy.policy_id in spec.required_execution_capabilities for spec,policy in values))
        with TemporaryDirectory() as directory:self.assertEqual(len(register_builtin_strategies(directory)),len(values))


if __name__=="__main__":unittest.main()
