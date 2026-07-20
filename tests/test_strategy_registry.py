from dataclasses import replace
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kairos.domain.capability import TimeInForce
from kairos.domain.strategy_contract import StrategyLifecycle
from kairos.execution.policy import ExecutionMode,ExecutionPolicy
from kairos.strategies.btc_iron_condor import BtcIronCondorStrategy
from kairos.strategies.registry import PromotionEvidence,StrategyImplementation,StrategyRegistry
from kairos.features import SmaFactorConfig,SmaFactorRuntime
from kairos.strategies.specs import sma_strategy_spec
from kairos.strategies.sma_cross_study_backtest import SmaCrossConfig


class StrategyRegistryTest(unittest.TestCase):
    def test_registry_requires_hashed_evidence_and_records_promotion(self):
        base=BtcIronCondorStrategy(study_spec_hash="r").strategy_spec
        spec=replace(base,lifecycle=StrategyLifecycle.DRAFT)
        policy=ExecutionPolicy("taker-combo-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("10"))
        with TemporaryDirectory() as directory:
            root=Path(directory);registry=StrategyRegistry(root/"strategies");target=registry.register(spec,policy)
            evidence_file=root/"results.json";evidence_file.write_text("{}")
            digest=hashlib.sha256(evidence_file.read_bytes()).hexdigest()
            evidence=PromotionEvidence(StrategyLifecycle.STUDY_VALIDATED,(str(evidence_file),),(digest,),"study-review",
                Decimal("10000"),"signal evidence invalidated",datetime.now(timezone.utc).isoformat(),True)
            promoted=registry.promote(spec,StrategyLifecycle.STUDY_VALIDATED,evidence)
            self.assertEqual(promoted.lifecycle,StrategyLifecycle.STUDY_VALIDATED)
            self.assertTrue((target/"promotions.jsonl").exists())
            record=json.loads((target/"promotions.jsonl").read_text().splitlines()[-1])
            bundle=target/record["evidence_bundle"]
            manifest=json.loads(bundle.read_text())
            self.assertEqual(manifest["kind"],"strategy_promotion_evidence_bundle")
            self.assertEqual(manifest["to"],StrategyLifecycle.STUDY_VALIDATED.value)
            self.assertEqual(manifest["evidence"]["evidence_paths"],[str(evidence_file)])
            self.assertEqual(manifest["evidence"]["evidence_hashes"],[digest])
            self.assertEqual(registry.status(spec.strategy_id,spec.version).latest_promotion_bundle,str(bundle))
            self.assertTrue((target/"manifest.json").exists())

    def test_same_strategy_version_rejects_execution_semantic_change(self):
        base=BtcIronCondorStrategy(study_spec_hash="r").strategy_spec
        policy=ExecutionPolicy("taker-combo-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("10"))
        with TemporaryDirectory() as directory:
            registry=StrategyRegistry(directory);registry.register(base,policy)
            changed=ExecutionPolicy("taker-combo-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("20"))
            with self.assertRaises(ValueError):registry.register(base,changed)

    def test_registry_cannot_bootstrap_directly_into_live(self):
        base=BtcIronCondorStrategy(study_spec_hash="r").strategy_spec
        live=replace(base,lifecycle=StrategyLifecycle.LIVE_APPROVED)
        policy=ExecutionPolicy("taker-combo-v1","1",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("10"))
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):StrategyRegistry(directory).register(live,policy)

    def test_active_version_and_audited_rollback_are_explicit(self):
        spec,policy=sma_strategy_spec(SmaCrossConfig());factor=SmaFactorRuntime(SmaFactorConfig(),input_identity="x").spec
        implementation=StrategyImplementation("kairos.strategies.sma_cross_strategy:SmaCrossStrategy","a"*64)
        with TemporaryDirectory() as directory:
            registry=StrategyRegistry(directory);registry.register(spec,policy,implementation=implementation,factor_specs=(factor,))
            newer=replace(spec,version="1.3.0");registry.register(newer,policy,implementation=implementation,factor_specs=(factor,))
            registry.activate(spec.strategy_id,spec.version,actor="operator",reason="baseline")
            registry.activate(spec.strategy_id,newer.version,actor="operator",reason="candidate")
            self.assertEqual(registry.active_version(spec.strategy_id),"1.3.0")
            registry.rollback(spec.strategy_id,actor="operator",reason="regression")
            self.assertEqual(registry.active_version(spec.strategy_id),spec.version)
            self.assertTrue(registry.status(spec.strategy_id,spec.version).active)


if __name__=="__main__":unittest.main()
