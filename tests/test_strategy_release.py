from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from kairos.features import SmaFactorConfig, SmaFactorRuntime
from kairos.strategies import StrategyImplementation, StrategyRegistry
from kairos.strategies.specs import builtin_strategy_specs


class StrategyReleaseTests(unittest.TestCase):
    def test_release_binds_strategy_code_factor_and_execution_policy(self) -> None:
        spec, policy = next(item for item in builtin_strategy_specs() if item[0].strategy_id == "sma-cross-v1")
        factor = SmaFactorRuntime(SmaFactorConfig(20, 50), input_identity="logical:btc-1h").spec
        source = Path("kairos/strategies/sma_cross_strategy.py")
        implementation = StrategyImplementation(
            "kairos.strategies.sma_cross_strategy:SmaCrossStrategy", sha256(source.read_bytes()).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            registry = StrategyRegistry(directory)
            target = registry.register(
                spec, policy, implementation=implementation, factor_specs=(factor,),
            )
            release = registry.load(spec.strategy_id, spec.version)

            self.assertEqual(release.directory, target)
            self.assertEqual(release.strategy_id, spec.strategy_id)
            self.assertEqual(release.implementation, implementation)
            self.assertEqual(release.factor_bindings[0]["factor_spec_hash"], factor.spec_hash)


if __name__ == "__main__":
    unittest.main()
