from dataclasses import replace
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest

from trading.adapters.base import Environment
from trading.domain.capability import TimeInForce
from trading.domain.strategy_contract import StrategyLifecycle
from trading.execution.policy import ExecutionMode,ExecutionPolicy
from trading.strategies.btc_iron_condor import BtcIronCondorStrategy
from trading.strategies.deployment import StrategyDeploymentGate
from trading.strategies.registry import StrategyRegistry


class StrategyDeploymentTest(unittest.TestCase):
    def test_draft_is_simulation_only_and_live_needs_live_lifecycle(self):
        spec=BtcIronCondorStrategy(research_spec_hash="x").strategy_spec
        policy=ExecutionPolicy("taker-combo-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("10"))
        with TemporaryDirectory() as directory:
            StrategyRegistry(directory).register(spec,policy);gate=StrategyDeploymentGate(directory)
            self.assertTrue(gate.evaluate(spec.strategy_id,Environment.PAPER,simulated_venue=True).allowed)
            self.assertFalse(gate.evaluate(spec.strategy_id,Environment.PAPER).allowed)
            # Deployment gate consumes the authoritative registry payload.
            import json
            path=__import__("pathlib").Path(directory)/spec.strategy_id/"2.0.0";path.mkdir(parents=True)
            payload={"lifecycle":StrategyLifecycle.LIVE_APPROVED.value};(path/"strategy_spec.json").write_text(json.dumps(payload))
            self.assertTrue(gate.evaluate(spec.strategy_id,Environment.LIVE).allowed)


if __name__=="__main__":unittest.main()
