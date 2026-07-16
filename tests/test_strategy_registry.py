from dataclasses import replace
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from trading.domain.capability import TimeInForce
from trading.domain.strategy_contract import StrategyLifecycle
from trading.execution.policy import ExecutionMode,ExecutionPolicy
from trading.strategies.btc_iron_condor import BtcIronCondorStrategy
from trading.strategies.registry import PromotionEvidence,StrategyRegistry


class StrategyRegistryTest(unittest.TestCase):
    def test_registry_requires_hashed_evidence_and_records_promotion(self):
        base=BtcIronCondorStrategy(research_spec_hash="r").strategy_spec
        spec=replace(base,lifecycle=StrategyLifecycle.DRAFT)
        policy=ExecutionPolicy("taker-combo-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("10"))
        with TemporaryDirectory() as directory:
            root=Path(directory);registry=StrategyRegistry(root/"strategies");target=registry.register(spec,policy)
            evidence_file=root/"results.json";evidence_file.write_text("{}")
            digest=hashlib.sha256(evidence_file.read_bytes()).hexdigest()
            evidence=PromotionEvidence(StrategyLifecycle.RESEARCH_VALIDATED,(str(evidence_file),),(digest,),"research-review",
                Decimal("10000"),"signal evidence invalidated",datetime.now(timezone.utc).isoformat(),True)
            promoted=registry.promote(spec,StrategyLifecycle.RESEARCH_VALIDATED,evidence)
            self.assertEqual(promoted.lifecycle,StrategyLifecycle.RESEARCH_VALIDATED)
            self.assertTrue((target/"promotions.jsonl").exists())
            self.assertTrue((target/"manifest.json").exists())

    def test_same_strategy_version_rejects_execution_semantic_change(self):
        base=BtcIronCondorStrategy(research_spec_hash="r").strategy_spec
        policy=ExecutionPolicy("taker-combo-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("10"))
        with TemporaryDirectory() as directory:
            registry=StrategyRegistry(directory);registry.register(base,policy)
            changed=ExecutionPolicy("taker-combo-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("20"))
            with self.assertRaises(ValueError):registry.register(base,changed)

    def test_registry_cannot_bootstrap_directly_into_live(self):
        base=BtcIronCondorStrategy(research_spec_hash="r").strategy_spec
        live=replace(base,lifecycle=StrategyLifecycle.LIVE_APPROVED)
        policy=ExecutionPolicy("taker-combo-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("10"))
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):StrategyRegistry(directory).register(live,policy)


if __name__=="__main__":unittest.main()
