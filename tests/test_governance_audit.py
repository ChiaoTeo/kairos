from pathlib import Path
from datetime import datetime, timezone
from hashlib import sha256
import json
from tempfile import TemporaryDirectory
import unittest

from kairospy.governance import (
    GovernanceRunArtifactWriter,
    PromotionError,
    PromotionEvidence,
    PromotionPolicy,
    ReadinessError,
    ReadinessEvidence,
    ReadinessStatus,
    RunArtifactRepository,
    audit_governance,
    require_readiness,
)
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.runtime import PreparedRun, ProfileResult, RunRequest, RunStatus, StrategyRunResult
from kairospy.infrastructure.storage.data_lake import write_daily_dataset
from kairospy.data.contracts import DatasetRelease, RunMode
from kairospy.integrations.data_products import BTC_SPOT_DAILY
from kairospy.strategy.contracts import StrategyLifecycle


class GovernanceAuditTest(unittest.TestCase):
    def test_audit_reports_missing_dataset_capabilities(self):
        with TemporaryDirectory() as directory:
            # Unknown catalog datasets are intentionally ignored; create a known prepared path.
            from kairospy.data.catalog import DataCatalog
            release=DatasetRelease(str(BTC_SPOT_DAILY.key),BTC_SPOT_DAILY.key,"1",BTC_SPOT_DAILY.schema_id,"1","test","1",BTC_SPOT_DAILY.relative_path,"parquet","test-hash")
            root=Path(directory);catalog=DataCatalog(root);catalog.register_product(BTC_SPOT_DAILY.product);catalog.register_release(release);catalog.save();path=catalog.path(str(BTC_SPOT_DAILY.key))
            write_daily_dataset(path,[{"period_start":"2026-01-01T00:00:00Z","x":1}],dataset_id=str(BTC_SPOT_DAILY.key),
                schema={"schema_id":"x","columns":{}},lineage={"source":"test"})
            (path/"capabilities.json").unlink()
            audit=audit_governance(root)
            self.assertFalse(audit.passed)
            self.assertTrue(any("capabilities.json" in value for value in audit.violations))


class GovernanceReadinessAndPromotionTest(unittest.TestCase):
    def test_readiness_failure_blocks_run_start(self):
        evidence = ReadinessEvidence(
            "live",
            ReadinessStatus.FAIL,
            required_ports=("execution", "account"),
            reason_codes=("missing_account_entitlement",),
            evidence_refs={"doctor": "providers/live/account"},
            account_binding="ibkr:U123",
            connector_id="ibkr",
        )
        with self.assertRaises(ReadinessError):
            require_readiness("live", (evidence,))

    def test_degraded_readiness_requires_explicit_override(self):
        evidence = ReadinessEvidence(
            "simulation",
            "degraded",
            required_ports=("market",),
            reason_codes=("market_stream_gap",),
            evidence_refs={"stream": "market/soak/gap-report"},
        )
        with self.assertRaises(ReadinessError):
            require_readiness("simulation", (evidence,))
        decision = require_readiness("simulation", (evidence,), allow_degraded=True)
        self.assertEqual(decision.status, ReadinessStatus.DEGRADED)

    def test_promotion_requires_hashes_gate_and_live_readiness_evidence(self):
        evidence = PromotionEvidence(
            StrategyLifecycle.ROBUSTNESS_VALIDATED,
            StrategyLifecycle.PAPER_APPROVED,
            dataset_hash="dataset-hash",
            strategy_hash="strategy-hash",
            config_hash="config-hash",
            gate_passed=True,
            evidence_refs={"readiness": "governance/readiness/paper"},
        )
        decision = PromotionPolicy().require(evidence)
        self.assertTrue(decision.approved)

        missing_readiness = PromotionEvidence(
            StrategyLifecycle.ROBUSTNESS_VALIDATED,
            StrategyLifecycle.PAPER_APPROVED,
            dataset_hash="dataset-hash",
            strategy_hash="strategy-hash",
            config_hash="config-hash",
            gate_passed=True,
        )
        with self.assertRaises(PromotionError):
            PromotionPolicy().require(missing_readiness)

        with self.assertRaises(ValueError):
            PromotionEvidence(
                StrategyLifecycle.DRAFT,
                StrategyLifecycle.RESEARCH_VALIDATED,
                dataset_hash="",
                strategy_hash="strategy-hash",
                config_hash="config-hash",
                gate_passed=True,
            )


class GovernanceRunArtifactWriterTest(unittest.TestCase):
    def test_governance_writer_binds_run_kernel_artifact_boundary(self):
        with TemporaryDirectory() as directory:
            repository = RunArtifactRepository(directory)
            writer = GovernanceRunArtifactWriter(repository)
            request = RunRequest(
                "run-1",
                RunMode.BACKTEST,
                "profile:backtest",
                "workspace-hash",
                "dataset-hash",
                "strategy",
                "1.0.0",
                "strategy-hash",
                "config-hash",
                datetime(2026, 7, 22, tzinfo=timezone.utc),
            )
            prepared = PreparedRun(
                request,
                "profile:backtest",
                RunMode.BACKTEST,
                "dataset-release:dataset-hash",
                "deterministic-fill-model",
                "backtest-artifact",
                "readiness-hash",
                "none",
                "governance-run-artifact",
                "profile-hash",
            )
            context_view_hashes = {
                "market": _hash({"view": "market"}),
                "portfolio": _hash({"view": "portfolio"}),
                "features": _hash({"view": "features"}),
                "reference": _hash({"view": "reference"}),
                "orders": _hash({"view": "orders"}),
                "intents": _hash({"view": "intents"}),
                "budget": _hash({"view": "budget"}),
            }
            context_hash = _hash(context_view_hashes)
            result = StrategyRunResult(
                (),
                (),
                (),
                (),
                _hash(()),
                _hash(()),
                _hash(()),
                _hash({
                    "events": [],
                    "factor_hash": _hash(()),
                    "decision_hash": _hash(()),
                    "intent_hash": _hash(()),
                    "context_hash": context_hash,
                }),
                context_view_hashes,
                context_hash,
            )
            profile_result = ProfileResult(RunStatus.SUCCEEDED, artifact_refs=("backtest:result",))

            link = writer(prepared, result, profile_result)

            self.assertEqual(len(link.artifact_hash), 64)
            self.assertEqual(len(link.artifact_refs), 1)
            artifact = repository.load(link.artifact_refs[0])
            self.assertEqual(artifact.artifact_hash, link.artifact_hash)
            self.assertEqual(artifact.payload["mode"], RunMode.BACKTEST.value)
            self.assertEqual(artifact.payload["input_identity"], "dataset-hash")
            self.assertEqual(artifact.payload["config"]["run_request"]["run_id"], "run-1")
            self.assertEqual(artifact.payload["execution"]["profile_artifact_refs"], ["backtest:result"])
            self.assertEqual(artifact.payload["context_hash"], context_hash)
            self.assertEqual(artifact.payload["context_view_hashes"], context_view_hashes)
            self.assertEqual(set(artifact.payload["context_evidence_refs"]), set(context_view_hashes))
            self.assertEqual(
                artifact.payload["context_evidence_refs"]["market"],
                f"context-view:market:{context_view_hashes['market']}",
            )
            explanation = repository.explain(artifact)
            self.assertEqual(explanation["context"]["context_hash"], context_hash)


def _hash(value: object) -> str:
    return sha256(json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


if __name__=="__main__":unittest.main()
