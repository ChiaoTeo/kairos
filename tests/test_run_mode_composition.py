from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from uuid import UUID

from kairospy.execution.command import OrderCommand
from kairospy.execution.events import TradeSide
from kairospy.execution.orders import ExecutionInstructions, OrderType, TimeInForce
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.market.canonical import BarPayload, CanonicalEventEnvelope, MarketEventKind
from kairospy.runtime import (
    BoundRunProfile, CompositeRecoveryBinding, DurableOutboxCommandSubmitter,
    EventSourceRunEventProvider, ExecutionPortCommandSubmitter,
    ExecutionRecoveryBinding, IterableRunEventProvider, ManagedServiceEvidenceProvider,
    ManagedServiceSnapshot, ManagedServiceSpec, ManagedServiceStatus, PreparedRun, ProfileResult,
    RecoveryResult, RunArtifactLink, RunCommandSubmitterBinding, RunKernel,
    LiveRuntimeBindingConfig, LiveRuntimeComponents,
    RunModeComposition, RunRequest, RunResult, RunStatus, RuntimeFeedServiceBundle,
    RuntimeRunLauncher, ServiceCriticality, StrategyRunResult,
    SubmitResult, RuntimeRecoveryBinding, backtest_composition, historical_simulation_composition,
    live_composition, paper_trading_composition,
    bind_live_runtime_components,
    load_live_runtime_binding_config,
    runtime_execution_plan, runtime_feed_plan, runtime_strategy_plan,
)
from kairospy.infrastructure.configuration import KairosProjectConfig
from kairospy.integrations import (
    LiveMarketEventSourceBinding,
    LiveProviderPorts,
    build_live_market_event_source,
    build_live_provider_ports,
    parse_account_ref,
)
from kairospy.runtime.profiles.backtest import BacktestProfile, backtest_profile
from kairospy.runtime.profiles.live import LiveProfile, live_profile
from kairospy.runtime.profiles.simulation import (
    SimulationClock,
    SimulationExecutionBinding,
    SimulationMarketSource,
    SimulationProfile,
    exchange_testnet_simulation_profile,
    historical_replay_simulation_profile,
    paper_simulation_profile,
)
from kairospy.governance import (
    GovernanceRunArtifactWriter,
    PromotionEvidence,
    ReadinessError,
    ReadinessEvidence,
    RunArtifactRepository,
)
from kairospy.identity import AccountRef, AccountType, AssetId, InstitutionId, InstrumentId, VenueId
from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway
from kairospy.integrations.ports import ComboLegRequest, ComboOrderRequest, Environment, OrderAck, OrderRequest
from kairospy.data import (
    DataSetContractArtifact, LiveViewFreshnessMonitor, LiveViewManifest, PAPER_LIVE_FRESHNESS_POLICY,
    evaluate_live_view_freshness, live_view_freshness_evidence, live_view_manifest_path, load_live_view_manifest,
    write_live_view_manifest,
)
from kairospy.market.stream import IterableEventSource as AsyncIterableEventSource
from kairospy.data.contracts import RunMode
from kairospy.data.products import BTC_SPOT_DAILY
from kairospy.market.subscriptions import CapturePolicy
from kairospy.runtime.application import FunctionProbe, RuntimeStatus, KairosApplication
from kairospy.runtime.async_runtime import AsyncKairosRuntime
from kairospy.runtime.clock import FixedClock
from kairospy.runtime.config import ApplicationConfig, RuntimePaths
from kairospy.runtime.kernel import CanonicalBarMarketProjection, GovernedStrategyRunLoop
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
from kairospy.strategy.views import FeatureView, MarketView, PortfolioView, ReferenceView
from kairospy.strategy import Context, StrategyLifecycle
from kairospy.execution.command import OutboxStatus
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.execution.outbox import DurableOrderCommandService, DurableOrderDispatcher
from kairospy.governance.kill_switch import KillSwitch
from kairospy.reference import BrokerId, CryptoSpotSpec, ExecutionRoute, ProductType, ReferenceCatalog, RouteId
from tests.reference_support import publish_test_instrument


class RunModeCompositionTests(unittest.IsolatedAsyncioTestCase):
    def _run_request(
        self,
        *,
        mode: RunMode = RunMode.BACKTEST,
        profile_id: str = "profile:backtest",
        data_binding_hash: str = "data-binding-hash",
        strategy_hash: str = "strategy-hash",
        config_hash: str = "config-hash",
    ) -> RunRequest:
        return RunRequest(
            "run-1",
            mode,
            profile_id,
            "workspace-hash",
            data_binding_hash,
            "strategy",
            "1.0.0",
            strategy_hash,
            config_hash,
            datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

    def test_all_promotion_modes_have_explicit_replaceable_dependencies(self) -> None:
        values = (
            backtest_composition(), historical_simulation_composition(),
            paper_trading_composition("binance"), live_composition("binance", "binance-live"),
        )

        self.assertEqual([item.mode for item in values], [
            RunMode.BACKTEST, RunMode.HISTORICAL_SIMULATION,
            RunMode.PAPER_TRADING, RunMode.LIVE,
        ])
        self.assertEqual(len({item.composition_hash for item in values}), len(values))
        self.assertEqual(paper_trading_composition("binance").composition_hash,
                         paper_trading_composition("binance").composition_hash)

    def test_canonical_bar_projection_preserves_market_view_visibility_evidence(self) -> None:
        start = datetime(2026, 7, 22, 10, tzinfo=timezone.utc)
        end = datetime(2026, 7, 22, 11, tzinfo=timezone.utc)
        available = datetime(2026, 7, 22, 11, 0, 5, tzinfo=timezone.utc)
        receive = datetime(2026, 7, 22, 11, 0, 2, tzinfo=timezone.utc)
        event = CanonicalEventEnvelope(
            UUID(int=1),
            "market.bar.v1",
            1,
            MarketEventKind.BAR,
            InstrumentId("equity:aapl"),
            BarPayload(start, end, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("1000")),
            "massive",
            "release:hourly-bars",
            "equity.hourly",
            "equity:aapl",
            end,
            receive,
            available,
            available,
            source_sequence=7,
            canonical_sequence=3,
        )

        snapshot = CanonicalBarMarketProjection().apply(event)
        assert snapshot is not None
        view = MarketView.from_snapshot(snapshot)

        self.assertEqual(view.data_binding, "release:hourly-bars")
        self.assertEqual(view.event_window, (start, end))
        self.assertEqual(view.available_time, available)
        self.assertEqual(view.freshness_seconds, Decimal("2.0"))
        self.assertEqual(view.reference_prices, ((InstrumentId("equity:aapl"), Decimal("101")),))

    def test_canonical_factor_view_inherits_input_available_time(self) -> None:
        from kairospy.analytics.features import SmaFactorRuntime

        start = datetime(2026, 7, 22, 10, tzinfo=timezone.utc)
        end = datetime(2026, 7, 22, 11, tzinfo=timezone.utc)
        available = datetime(2026, 7, 22, 11, 0, 5, tzinfo=timezone.utc)
        receive = datetime(2026, 7, 22, 11, 0, 2, tzinfo=timezone.utc)
        event = CanonicalEventEnvelope(
            UUID(int=2),
            "market.bar.v1",
            1,
            MarketEventKind.BAR,
            InstrumentId("equity:aapl"),
            BarPayload(start, end, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("1000")),
            "massive",
            "release:hourly-bars",
            "equity.hourly",
            "equity:aapl",
            end,
            receive,
            available,
            available,
        )

        factor = SmaFactorRuntime(input_identity="release:hourly-bars").update(event)
        assert factor is not None
        view = FeatureView.from_snapshots(factor_snapshots=(factor,))

        self.assertEqual(factor.available_time, available)
        self.assertEqual(view.as_of, end)
        self.assertEqual(view.available_time, available)
        self.assertEqual(view.factor("sma-spread").available_time, available)
        self.assertEqual(view.factor("sma-spread").input_identity, "release:hourly-bars")

    async def test_governed_strategy_run_loop_records_reconstructable_context_hashes(self) -> None:
        start = datetime(2026, 7, 22, 10, tzinfo=timezone.utc)
        end = datetime(2026, 7, 22, 11, tzinfo=timezone.utc)
        available = datetime(2026, 7, 22, 11, 0, 5, tzinfo=timezone.utc)
        event = CanonicalEventEnvelope(
            UUID(int=3),
            "market.bar.v1",
            1,
            MarketEventKind.BAR,
            InstrumentId("equity:msft"),
            BarPayload(start, end, Decimal("200"), Decimal("203"), Decimal("199"), Decimal("202"), Decimal("1000")),
            "massive",
            "release:hourly-bars",
            "equity.hourly",
            "equity:msft",
            end,
            available,
            available,
            available,
        )
        source = _AsyncEventSource((event,))
        factor_runtime = _SingleFactorRuntime(event.instrument_id, end, available)
        strategy_runtime = _NoopStrategyRuntime()

        result = await GovernedStrategyRunLoop(
            source,
            factor_runtime,
            strategy_runtime,
            lambda market: Context(
                MarketView.from_snapshot(market),
                PortfolioView(timestamp=market.timestamp),
                reference=ReferenceView(as_of=market.timestamp),
            ),
            approved_capital=Decimal("1000"),
        ).run()

        self.assertEqual(set(result.context_view_hashes), {
            "market", "portfolio", "features", "reference", "orders", "intents", "budget",
        })
        self.assertEqual(result.context_hash, _hash(result.context_view_hashes))
        self.assertEqual(result.audit_hash, _hash({
            "events": result.event_message_ids,
            "factor_hash": result.factor_hash,
            "decision_hash": result.decision_hash,
            "intent_hash": result.intent_hash,
            "context_hash": result.context_hash,
        }))
        self.assertNotEqual(result.context_view_hashes["market"], result.context_view_hashes["budget"])

    def test_legacy_study_mode_alias_is_not_public_api(self) -> None:
        with self.assertRaises(ValueError):
            RunMode("re" + "search")
        with self.assertRaises(ValueError):
            RunMode("study")

    def test_live_modes_fail_without_capture_or_persistence(self) -> None:
        with self.assertRaisesRegex(ValueError, "capture"):
            RunModeComposition(
                RunMode.PAPER_TRADING, "live", "system", "simulated", "runtime-store",
                "paper", CapturePolicy.NONE,
            )
        with self.assertRaisesRegex(ValueError, "persistence"):
            RunModeComposition(
                RunMode.LIVE, "live", "system", "venue", "none", "live",
                CapturePolicy.RAW_AND_CANONICAL,
            )

    def test_backtest_cannot_silently_use_wall_clock(self) -> None:
        with self.assertRaisesRegex(ValueError, "replay clock"):
            RunModeComposition(
                RunMode.BACKTEST, "release", "system", "fill-model", "artifact",
                "backtest", CapturePolicy.NONE,
            )

    def test_declaration_binds_real_components_and_executes(self) -> None:
        declaration=backtest_composition();calls=[]
        executable=declaration.bind(event_source=object(),clock=object(),execution_driver=object(),
            persistence=object(),safety_policy=object(),runner=lambda:calls.append("ran") or {"passed":True})
        self.assertEqual(executable.run(),{"passed":True});self.assertEqual(calls,["ran"])
        self.assertEqual(executable.composition_hash,declaration.composition_hash)

    def test_run_kernel_delegates_profile_boundaries_and_collects_evidence(self) -> None:
        profile = _MemoryRunProfile()
        request = self._run_request()

        def strategy_runner(prepared: PreparedRun) -> StrategyRunResult:
            self.assertEqual(prepared.profile_id, profile.profile_id)
            return StrategyRunResult((), (), (), (), "factor-hash", "decision-hash", "intent-hash", "strategy-audit")

        result = RunKernel(profile).run(request, strategy_runner)

        self.assertEqual(profile.calls, ["prepare", "recover", "finalize"])
        self.assertIsInstance(result, RunResult)
        self.assertEqual(result.status, RunStatus.SUCCEEDED)
        self.assertEqual(result.request_hash, request.request_hash)
        self.assertEqual(result.strategy_run_hash, "strategy-audit")
        self.assertEqual(result.artifact_hash, "artifact-hash")
        self.assertEqual(result.artifact_refs, ("artifact:run-1",))
        self.assertEqual(len(result.evidence_hash), 64)
        self.assertEqual(len(result.result_hash), 64)

    def test_run_kernel_uses_injected_artifact_writer_without_owning_governance(self) -> None:
        profile = _MemoryRunProfile()
        request = self._run_request()

        def strategy_runner(_prepared: PreparedRun) -> StrategyRunResult:
            return StrategyRunResult((), (), (), (), "factor-hash", "decision-hash", "intent-hash", "strategy-audit")

        def artifact_writer(prepared: PreparedRun, _strategy_result: StrategyRunResult, profile_result: ProfileResult) -> RunArtifactLink:
            self.assertEqual(prepared.profile_id, profile.profile_id)
            self.assertEqual(profile_result.artifact_refs, ("artifact:run-1",))
            return RunArtifactLink("governance-artifact-hash", ("governance/artifact/manifest.json",))

        result = RunKernel(profile).run(request, strategy_runner, artifact_writer=artifact_writer)

        self.assertEqual(result.artifact_hash, "governance-artifact-hash")
        self.assertEqual(result.artifact_refs, ("artifact:run-1", "governance/artifact/manifest.json"))

    def test_runtime_run_launcher_binds_application_gates_services_and_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = RuntimePaths.under(root / "runtime")
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths),
                SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="runtime-launch",
                probes=(FunctionProbe("market", lambda: (True, "ready")),),
            )
            service_evidence = ManagedServiceEvidenceProvider(
                _SupervisorEvidence(),
                "runtime-services",
            )
            launcher = RuntimeRunLauncher(
                application,
                RunKernel(_MemoryRunProfile()),
                service_evidence_provider=service_evidence,
            )
            request = self._run_request()

            result = launcher.run(
                request,
                lambda _prepared: _empty_strategy_result(),
                artifact_writer_factory=lambda evidence: GovernanceRunArtifactWriter(
                    RunArtifactRepository(root / "artifacts"),
                    execution={"runtime_launch": evidence},
                ),
            )

            self.assertEqual(application.status, RuntimeStatus.RUNNING)
            self.assertEqual(result.run_result.status, RunStatus.SUCCEEDED)
            self.assertEqual(result.evidence["status"], "running")
            self.assertEqual(result.evidence["services"]["binding_id"], "runtime-services")
            artifact = RunArtifactRepository(root / "artifacts").load(result.artifact_refs[-1])
            runtime_launch = artifact.payload["execution"]["runtime_launch"]
            self.assertEqual(runtime_launch["runtime_id"], "runtime-launch")
            self.assertEqual(runtime_launch["status"], "running")
            self.assertEqual(runtime_launch["services"]["services"][0]["name"], "feed:bars")

    def test_runtime_run_launcher_starts_and_stops_managed_services_before_artifact_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = RuntimePaths.under(root / "runtime")
            application = KairosApplication(
                ApplicationConfig(Environment.TESTNET, paths),
                SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="runtime-launch-managed",
                probes=(FunctionProbe("market", lambda: (True, "ready")),),
            )
            lifecycle: list[str] = []

            async def feed_service() -> None:
                lifecycle.append("started")
                try:
                    await asyncio.Future()
                finally:
                    lifecycle.append("stopped")

            launcher = RuntimeRunLauncher(
                application,
                RunKernel(_MemoryRunProfile()),
                managed_services=(ManagedServiceSpec("feed:bars", feed_service),),
                service_evidence_binding_id="runtime-services",
            )

            result = launcher.run(
                self._run_request(),
                lambda _prepared: _empty_strategy_result(),
                artifact_writer_factory=lambda evidence: GovernanceRunArtifactWriter(
                    RunArtifactRepository(root / "artifacts"),
                    execution={"runtime_launch": evidence},
                ),
            )

            self.assertEqual(lifecycle, ["started", "stopped"])
            self.assertEqual(result.evidence["services"]["services"][0]["status"], "stopped")
            artifact = RunArtifactRepository(root / "artifacts").load(result.artifact_refs[-1])
            runtime_launch = artifact.payload["execution"]["runtime_launch"]
            self.assertEqual(runtime_launch["services"]["binding_id"], "runtime-services")
            self.assertEqual(runtime_launch["services"]["services"][0]["name"], "feed:bars")
            self.assertEqual(runtime_launch["services"]["services"][0]["status"], "stopped")

    def test_run_kernel_rejects_profile_mismatch_before_prepare(self) -> None:
        profile = _MemoryRunProfile()
        request = self._run_request(profile_id="other-profile")

        with self.assertRaisesRegex(ValueError, "profile_id"):
            RunKernel(profile).prepare(request)
        self.assertEqual(profile.calls, [])

    def test_run_kernel_exposes_profile_event_and_submit_boundaries(self) -> None:
        profile = _MemoryRunProfile()
        kernel = RunKernel(profile)
        prepared = kernel.prepare(self._run_request())

        self.assertEqual(tuple(kernel.market_events(prepared)), ("market-event",))
        self.assertEqual(tuple(kernel.execution_events(prepared)), ("execution-event",))
        self.assertEqual(kernel.submit(("command-1",)).accepted_command_ids, ("command-1",))
        self.assertEqual(profile.calls, ["prepare"])

    def test_bound_run_profile_binds_runtime_sources_gateway_and_recovery_without_changing_profile_hash(self) -> None:
        profile = _MemoryRunProfile()
        submitted: list[object] = []
        bound = BoundRunProfile(
            profile,
            "runtime-binding",
            market_event_provider=IterableRunEventProvider(("bound-market",), "market-source-binding"),
            execution_event_provider=IterableRunEventProvider(("bound-execution",), "execution-source-binding"),
            command_submitter=RunCommandSubmitterBinding(lambda command: submitted.append(command), "gateway-binding"),
            recovery_handler=lambda _prepared: RecoveryResult(True, True, {"source": "runtime-recovery"}),
        )
        kernel = RunKernel(bound)
        request = self._run_request()

        prepared = kernel.prepare(request)
        submit = kernel.submit(("command-1",))
        result = kernel.run(
            request,
            lambda _prepared: StrategyRunResult((), (), (), (), "factor", "decision", "intent", "strategy-audit"),
        )

        self.assertEqual(bound.profile_hash, profile.profile_hash)
        self.assertEqual(len(bound.binding_hash), 64)
        self.assertEqual(prepared.profile_hash, profile.profile_hash)
        self.assertEqual(prepared.evidence["runtime_bindings"]["binding_id"], "runtime-binding")
        self.assertEqual(prepared.evidence["runtime_bindings"]["market_event_provider"], "market-source-binding")
        self.assertEqual(prepared.evidence["runtime_bindings"]["command_submitter"], "gateway-binding")
        self.assertEqual(len(prepared.evidence["runtime_binding_hash"]), 64)
        self.assertEqual(tuple(kernel.market_events(prepared)), ("bound-market",))
        self.assertEqual(tuple(kernel.execution_events(prepared)), ("bound-execution",))
        self.assertEqual(submit.accepted_command_ids, ("command-1",))
        self.assertEqual(submitted, ["command-1"])
        self.assertEqual(result.status, RunStatus.SUCCEEDED)

    def test_command_submitter_binding_reports_gateway_rejections_as_submit_evidence(self) -> None:
        submitter = RunCommandSubmitterBinding(
            lambda _command: (_ for _ in ()).throw(RuntimeError("gateway down")),
            "failing-gateway",
        )

        result = submitter(("command-1",))

        self.assertEqual(result.accepted_command_ids, ())
        self.assertEqual(result.rejected_command_ids, ("command-1",))
        self.assertEqual(result.evidence["binding_id"], "failing-gateway")
        self.assertEqual(result.evidence["errors"], (("command-1", "RuntimeError"),))

    def test_runtime_recovery_binding_adapts_recovery_service_result(self) -> None:
        class Recovery:
            def __init__(self) -> None:
                self.called_at = None

            def recover(self, at):
                self.called_at = at
                return SimpleNamespace(ready=True, recovered_at=at, reason="ready")

        recovery = Recovery()
        prepared = RunKernel(_MemoryRunProfile()).prepare(self._run_request())
        result = RuntimeRecoveryBinding(recovery, "runtime-recovery")(prepared)

        self.assertEqual(recovery.called_at, prepared.request.requested_at)
        self.assertTrue(result.required)
        self.assertTrue(result.recovered)
        self.assertEqual(result.evidence["binding_id"], "runtime-recovery")

    def test_execution_recovery_binding_adapts_order_recovery_report(self) -> None:
        class Recovery:
            service_id = "venue-order-recovery"

            def __init__(self) -> None:
                self.called_at = None

            def recover(self, at):
                self.called_at = at
                return SimpleNamespace(complete=True, resolved=("client-1",), unresolved=())

        recovery = Recovery()
        prepared = RunKernel(_MemoryRunProfile()).prepare(self._run_request())
        result = ExecutionRecoveryBinding(recovery, "execution-recovery")(prepared)

        self.assertEqual(recovery.called_at, prepared.request.requested_at)
        self.assertTrue(result.required)
        self.assertTrue(result.recovered)
        self.assertEqual(result.evidence["binding_id"], "execution-recovery")
        self.assertEqual(result.evidence["recovery"], "venue-order-recovery")
        self.assertEqual(result.evidence["resolved_command_ids"], ("client-1",))
        self.assertEqual(result.evidence["unresolved_command_ids"], ())

    def test_composite_recovery_binding_combines_live_runtime_and_execution_recovery(self) -> None:
        prepared = RunKernel(_MemoryRunProfile()).prepare(self._run_request())
        handler = CompositeRecoveryBinding(
            (
                lambda _prepared: RecoveryResult(True, True, {"source": "runtime"}),
                lambda _prepared: RecoveryResult(True, False, {"unresolved_command_ids": ("client-2",)}),
            ),
            "live-recovery",
        )

        result = handler(prepared)

        self.assertTrue(result.required)
        self.assertFalse(result.recovered)
        self.assertEqual(result.evidence["binding_id"], "live-recovery")
        self.assertEqual(len(result.evidence["handlers"]), 2)
        self.assertEqual(result.evidence["handlers"][1]["evidence"]["unresolved_command_ids"], ("client-2",))

    def test_live_profile_uses_bound_composite_recovery_before_strategy_runner(self) -> None:
        readiness = (ReadinessEvidence(
            "live",
            "pass",
            required_ports=("market", "reference", "execution", "account"),
            evidence_refs={"connector": "binance-live-ready"},
            account_binding="account-binding-hash",
            connector_id="binance",
        ),)
        promotion = PromotionEvidence(
            StrategyLifecycle.PAPER_APPROVED,
            StrategyLifecycle.LIVE_LIMITED,
            "live-data-hash",
            "strategy-hash",
            "config-hash",
            True,
            evidence_refs={"readiness": "readiness:live"},
        )
        runtime_recovery = RuntimeRecoveryBinding(
            _ReadyRuntimeRecovery(),
            "runtime-recovery",
        )
        execution_recovery = ExecutionRecoveryBinding(
            _CompleteExecutionRecovery(),
            "execution-recovery",
        )
        profile = BoundRunProfile(
            live_profile(
                profile_id="profile:live",
                provider="binance",
                execution_driver="binance-live",
                account_binding_hash="account-binding-hash",
                data_binding_hash="live-data-hash",
                strategy_hash="strategy-hash",
                config_hash="config-hash",
                readiness_evidence=readiness,
                promotion_evidence=promotion,
            ),
            "live-runtime-binding",
            recovery_handler=CompositeRecoveryBinding(
                (runtime_recovery, execution_recovery),
                "live-recovery",
            ),
        )
        request = self._run_request(
            mode=RunMode.LIVE,
            profile_id="profile:live",
            data_binding_hash="live-data-hash",
        )
        calls: list[str] = []

        result = RunKernel(profile).run(
            request,
            lambda _prepared: calls.append("strategy") or StrategyRunResult((), (), (), (), "", "", "", "strategy-audit"),
        )

        self.assertEqual(calls, ["strategy"])
        self.assertEqual(result.strategy_run_hash, "strategy-audit")
        self.assertEqual(result.status, RunStatus.FAILED)

    def test_managed_service_evidence_provider_summarizes_long_lived_services(self) -> None:
        class Supervisor:
            healthy = True

            def snapshots(self):
                return (
                    ManagedServiceSnapshot(
                        "feed:bars",
                        ServiceCriticality.CRITICAL,
                        ManagedServiceStatus.RUNNING,
                        1,
                        0,
                        None,
                    ),
                )

        evidence = ManagedServiceEvidenceProvider(Supervisor(), "runtime-services")()

        self.assertTrue(evidence["healthy"])
        self.assertEqual(evidence["binding_id"], "runtime-services")
        self.assertEqual(evidence["services"][0]["name"], "feed:bars")
        self.assertEqual(evidence["services"][0]["status"], "running")

    def test_event_source_run_event_provider_collects_finite_async_source_for_bound_profile(self) -> None:
        profile = BoundRunProfile(
            _MemoryRunProfile(),
            "runtime-binding",
            market_event_provider=EventSourceRunEventProvider(
                AsyncIterableEventSource(("market-1", "market-2")),
                "async-market-source",
            ),
        )
        kernel = RunKernel(profile)
        prepared = kernel.prepare(self._run_request())

        self.assertEqual(tuple(kernel.market_events(prepared)), ("market-1", "market-2"))
        self.assertEqual(prepared.evidence["runtime_bindings"]["market_event_provider"], "async-market-source")
        self.assertEqual(profile.profile_hash, "profile-hash")

    async def test_event_source_run_event_provider_rejects_running_event_loop_collection(self) -> None:
        prepared = RunKernel(_MemoryRunProfile()).prepare(self._run_request())
        provider = EventSourceRunEventProvider(AsyncIterableEventSource(("market-1",)), "async-market-source")

        with self.assertRaisesRegex(RuntimeError, "running event loop"):
            tuple(provider(prepared))

    def test_execution_port_command_submitter_routes_order_command_to_execution_gateway(self) -> None:
        gateway = SimulatedExecutionAccountGateway(VenueId("SIM"), _account())
        submitter = ExecutionPortCommandSubmitter(gateway, "simulated-execution")
        command = OrderCommand("submit:client-1", _order_request("client-1"), self._run_request().requested_at)

        result = submitter((command,))

        self.assertEqual(result.accepted_command_ids, ("submit:client-1",))
        self.assertEqual(result.rejected_command_ids, ())
        self.assertEqual(result.evidence["binding_id"], "simulated-execution")
        self.assertEqual(result.evidence["gateway"], "execution")
        self.assertEqual(result.evidence["acknowledgements"][0]["client_order_id"], "client-1")
        self.assertEqual(len(gateway.open_orders(_account())), 1)

    def test_execution_port_command_submitter_routes_combo_requests_without_capability_graph(self) -> None:
        gateway = SimulatedExecutionAccountGateway(VenueId("SIM"), _account())
        submitter = ExecutionPortCommandSubmitter(gateway, "simulated-execution")
        combo = _combo_order_request("combo-1")

        result = submitter((combo,))

        self.assertEqual(result.accepted_command_ids, ("combo-1",))
        self.assertEqual(result.rejected_command_ids, ())
        self.assertEqual(result.evidence["acknowledgements"][0]["client_order_id"], "combo-1")

    def test_execution_port_command_submitter_reports_rejections_as_submit_evidence(self) -> None:
        class OrderOnlyGateway:
            service_id = "order-only"

            def place_order(self, request):
                raise RuntimeError("closed")

        submitter = ExecutionPortCommandSubmitter(OrderOnlyGateway(), "order-only-binding")

        result = submitter((_order_request("client-1"),))

        self.assertEqual(result.accepted_command_ids, ())
        self.assertEqual(result.rejected_command_ids, ("client-1",))
        self.assertEqual(result.evidence["gateway"], "order-only")
        self.assertEqual(result.evidence["errors"], (("client-1", "RuntimeError"),))

    def test_durable_outbox_command_submitter_routes_live_submit_through_outbox(self) -> None:
        class Router:
            service_id = "live-router"

            def __init__(self) -> None:
                self.submissions = 0

            def submit(self, request, at):
                self.submissions += 1
                return OrderAck(
                    request.internal_order_id,
                    request.client_order_id,
                    request.strategy_id,
                    request.intent_id,
                    request.correlation_id,
                    "venue-1",
                    at,
                )

            def submit_combo(self, request, at):
                return self.submit(request, at)

        with tempfile.TemporaryDirectory() as directory:
            at = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
            root = Path(directory)
            paths = RuntimePaths.under(root)
            store = SQLiteRuntimeStore(paths.runtime_database)
            application = KairosApplication(
                ApplicationConfig(Environment.LIVE, paths),
                store,
                runtime_id="live-outbox-binding",
                clock=FixedClock(at),
            )
            application.start()
            application.run()
            router = Router()
            service = DurableOrderCommandService(
                store,
                application,
                KillSwitch((), FixedClock(at), store),
                lambda _request: None,
                clock=FixedClock(at),
            )
            dispatcher = DurableOrderDispatcher(store, router, clock=FixedClock(at))
            submitter = DurableOutboxCommandSubmitter(service, dispatcher, "live-outbox-binding")

            result = submitter((_order_request("client-1"),))

            self.assertEqual(result.accepted_command_ids, ("submit:client-1",))
            self.assertEqual(result.rejected_command_ids, ())
            self.assertEqual(router.submissions, 1)
            self.assertEqual(result.evidence["binding_id"], "live-outbox-binding")
            self.assertEqual(result.evidence["dispatch_count"], 1)
            self.assertEqual(result.evidence["outbox"][0]["command_id"], "submit:client-1")
            self.assertEqual(result.evidence["outbox"][0]["status"], OutboxStatus.COMPLETED.value)
            order = store.order("client-1")
            assert order is not None
            self.assertEqual(order.status, DurableOrderStatus.ACKNOWLEDGED)
            self.assertEqual(order.ack.venue_order_id, "venue-1")  # type: ignore[union-attr]

    def test_backtest_profile_prepares_run_profile_contract(self) -> None:
        readiness = (ReadinessEvidence(
            "backtest",
            "pass",
            required_ports=("market", "reference"),
            evidence_refs={"dataset": "dataset-hash", "reference": "reference-hash"},
        ),)
        profile = backtest_profile(
            profile_id="profile:backtest",
            dataset_hash="dataset-hash",
            strategy_hash="strategy-hash",
            config_hash="config-hash",
            reference_hash="reference-hash",
            readiness_evidence=readiness,
        )
        request = self._run_request(data_binding_hash="dataset-hash")

        prepared = RunKernel(profile).prepare(request)

        self.assertIsInstance(profile, BacktestProfile)
        self.assertEqual(prepared.mode, RunMode.BACKTEST)
        self.assertEqual(prepared.market_source, "dataset-release:dataset-hash")
        self.assertEqual(prepared.execution_driver, "deterministic-fill-model")
        self.assertEqual(prepared.store_policy, "backtest-artifact")
        self.assertEqual(profile.recover(prepared).required, False)
        self.assertEqual(profile.submit(("cmd-1",)).rejected_command_ids, ("cmd-1",))
        self.assertEqual(profile.finalize(prepared).status, RunStatus.SUCCEEDED)

    def test_simulation_profile_prepares_run_profile_contract(self) -> None:
        readiness = (ReadinessEvidence(
            "simulation",
            "pass",
            required_ports=("market", "reference", "execution"),
            evidence_refs={"dataset": "dataset-hash"},
        ),)
        profile = historical_replay_simulation_profile(
            profile_id="profile:simulation",
            dataset_hash="dataset-hash",
            strategy_hash="strategy-hash",
            config_hash="config-hash",
            readiness_evidence=readiness,
        )
        request = self._run_request(
            mode=RunMode.HISTORICAL_SIMULATION,
            profile_id="profile:simulation",
            data_binding_hash="dataset-hash",
        )

        prepared = RunKernel(profile).prepare(request)

        self.assertEqual(prepared.mode, RunMode.HISTORICAL_SIMULATION)
        self.assertEqual(prepared.market_source, SimulationMarketSource.HISTORICAL_REPLAY.value)
        self.assertEqual(prepared.execution_driver, SimulationExecutionBinding.LOCAL_SIMULATED.value)
        self.assertEqual(prepared.store_policy, "runtime-store")
        self.assertEqual(profile.recover(prepared).required, True)
        self.assertEqual(profile.submit(("cmd-1",)).rejected_command_ids, ("cmd-1",))

    def test_live_profile_prepares_and_fails_closed_without_recovery_binding(self) -> None:
        readiness = (ReadinessEvidence(
            "live",
            "pass",
            required_ports=("market", "reference", "execution", "account"),
            evidence_refs={"connector": "binance-live-ready"},
            account_binding="account-binding-hash",
            connector_id="binance",
        ),)
        promotion = PromotionEvidence(
            StrategyLifecycle.PAPER_APPROVED,
            StrategyLifecycle.LIVE_LIMITED,
            "live-data-hash",
            "strategy-hash",
            "config-hash",
            True,
            evidence_refs={"readiness": "readiness:live"},
        )
        profile = live_profile(
            profile_id="profile:live",
            provider="binance",
            execution_driver="binance-live",
            account_binding_hash="account-binding-hash",
            data_binding_hash="live-data-hash",
            strategy_hash="strategy-hash",
            config_hash="config-hash",
            readiness_evidence=readiness,
            promotion_evidence=promotion,
        )
        request = self._run_request(
            mode=RunMode.LIVE,
            profile_id="profile:live",
            data_binding_hash="live-data-hash",
        )
        calls: list[str] = []

        result = RunKernel(profile).run(
            request,
            lambda _prepared: calls.append("strategy") or StrategyRunResult((), (), (), (), "", "", "", ""),
        )

        self.assertIsInstance(profile, LiveProfile)
        self.assertEqual(calls, [])
        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.strategy_run_hash, "not-run")
        self.assertEqual(profile.submit(("cmd-1",)).rejected_command_ids, ("cmd-1",))

    def test_profile_run_artifacts_reconstruct_context_evidence_across_modes(self) -> None:
        backtest_readiness = (ReadinessEvidence(
            "backtest",
            "pass",
            required_ports=("data", "reference"),
            evidence_refs={"dataset": "dataset-hash"},
        ),)
        simulation_readiness = (ReadinessEvidence(
            "simulation",
            "pass",
            required_ports=("market", "reference", "execution"),
            evidence_refs={"dataset": "dataset-hash"},
        ),)
        live_readiness = (ReadinessEvidence(
            "live",
            "pass",
            required_ports=("market", "reference", "execution", "account"),
            evidence_refs={"connector": "binance-live-ready"},
            account_binding="account-binding-hash",
            connector_id="binance",
        ),)
        live_promotion = PromotionEvidence(
            StrategyLifecycle.PAPER_APPROVED,
            StrategyLifecycle.LIVE_LIMITED,
            "live-data-hash",
            "strategy-hash",
            "config-hash",
            True,
            evidence_refs={"readiness": "readiness:live"},
        )
        cases = (
            (
                "backtest",
                backtest_profile(
                    profile_id="profile:backtest",
                    dataset_hash="dataset-hash",
                    strategy_hash="strategy-hash",
                    config_hash="config-hash",
                    readiness_evidence=backtest_readiness,
                ),
                self._run_request(data_binding_hash="dataset-hash"),
                RunStatus.SUCCEEDED,
            ),
            (
                "simulation",
                historical_replay_simulation_profile(
                    profile_id="profile:simulation",
                    dataset_hash="dataset-hash",
                    strategy_hash="strategy-hash",
                    config_hash="config-hash",
                    readiness_evidence=simulation_readiness,
                ),
                self._run_request(
                    mode=RunMode.HISTORICAL_SIMULATION,
                    profile_id="profile:simulation",
                    data_binding_hash="dataset-hash",
                ),
                RunStatus.SUCCEEDED,
            ),
            (
                "live",
                BoundRunProfile(
                    live_profile(
                        profile_id="profile:live",
                        provider="binance",
                        execution_driver="binance-live",
                        account_binding_hash="account-binding-hash",
                        data_binding_hash="live-data-hash",
                        strategy_hash="strategy-hash",
                        config_hash="config-hash",
                        readiness_evidence=live_readiness,
                        promotion_evidence=live_promotion,
                    ),
                    "live-runtime-binding",
                    recovery_handler=lambda _prepared: RecoveryResult(
                        True,
                        True,
                        {"binding_id": "live-recovery", "reason": "recovered"},
                    ),
                ),
                self._run_request(
                    mode=RunMode.LIVE,
                    profile_id="profile:live",
                    data_binding_hash="live-data-hash",
                ),
                RunStatus.FAILED,
            ),
        )

        for name, profile, request, expected_status in cases:
            with self.subTest(mode=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repository = RunArtifactRepository(root / "artifacts")
                strategy_result = _strategy_result_with_context()

                result = RunKernel(profile).run(
                    request,
                    lambda _prepared: strategy_result,
                    artifact_writer=GovernanceRunArtifactWriter(repository),
                )

                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.strategy_run_hash, strategy_result.audit_hash)
                artifact = repository.load(result.artifact_refs[-1])
                self.assertEqual(result.artifact_hash, artifact.artifact_hash)
                explanation = repository.explain(artifact)

                self.assertEqual(explanation["mode"], request.mode.value)
                self.assertEqual(explanation["context"]["context_hash"], strategy_result.context_hash)
                self.assertEqual(
                    explanation["context"]["view_hashes"],
                    dict(strategy_result.context_view_hashes),
                )
                self.assertEqual(
                    explanation["context"]["evidence_refs"],
                    {
                        view: f"context-view:{view}:{view_hash}"
                        for view, view_hash in sorted(strategy_result.context_view_hashes.items())
                    },
                )
                self.assertEqual(artifact.payload["execution"]["profile_status"], expected_status.value)

    def test_live_runtime_config_builds_bound_profile_without_connector_capability_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "kairos.toml"
            config_path.write_text(
                "\n".join([
                    "[project]",
                    'name = "live-runtime-config"',
                    "",
                    "[data]",
                    'lake_root = ".kairos/data"',
                    "",
                    "[runtime.live]",
                    "enabled = true",
                    'profile_id = "profile:live"',
                    'provider = "binance"',
                    'execution_driver = "binance-live"',
                    'account_binding_hash = "account-binding-hash"',
                    'data_binding_hash = "live-data-hash"',
                    'strategy_hash = "strategy-hash"',
                    'config_hash = "config-hash"',
                    'binding_id = "live-runtime-binding"',
                    "",
                    "[runtime.live.recovery]",
                    'binding_id = "live-recovery"',
                    "ready = true",
                    'reason = "startup recovery complete"',
                    "",
                    "[runtime.live.promotion]",
                    'from_stage = "PAPER_APPROVED"',
                    'to_stage = "LIVE_LIMITED"',
                    'dataset_hash = "live-data-hash"',
                    'strategy_hash = "strategy-hash"',
                    'config_hash = "config-hash"',
                    "gate_passed = true",
                    'evidence_refs = { readiness = "readiness:live" }',
                    "",
                    "[[runtime.live.readiness]]",
                    'status = "pass"',
                    'required_ports = ["market", "reference", "execution", "account"]',
                    'account_binding = "account-binding-hash"',
                    'connector_id = "binance"',
                    'evidence_refs = { connector = "binance-live-ready" }',
                    "",
                ]) + "\n",
                encoding="utf-8",
            )
            config = KairosProjectConfig.load(config_path)

            live_config = load_live_runtime_binding_config(
                config,
                workspace_hash="live-data-hash",
                strategy_hash="strategy-hash",
                config_hash="config-hash",
            )
            profile = live_config.bind()
            request = self._run_request(
                mode=RunMode.LIVE,
                profile_id="profile:live",
                data_binding_hash="live-data-hash",
            )
            calls: list[str] = []
            result = RunKernel(profile).run(
                request,
                lambda _prepared: calls.append("strategy") or StrategyRunResult((), (), (), (), "", "", "", "strategy-audit"),
            )

        self.assertIsInstance(live_config, LiveRuntimeBindingConfig)
        self.assertEqual(profile.profile_hash, live_config.to_live_profile().profile_hash)
        prepared = RunKernel(profile).prepare(request)
        self.assertEqual(prepared.evidence["runtime_bindings"]["binding_id"], "live-runtime-binding")
        self.assertEqual(prepared.evidence["runtime_bindings"]["recovery_handler"], "live-recovery")
        self.assertEqual(calls, ["strategy"])
        self.assertEqual(result.strategy_run_hash, "strategy-audit")
        self.assertEqual(result.status, RunStatus.FAILED)

    def test_live_runtime_components_bind_provider_ports_to_market_outbox_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            at = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
            root = Path(directory)
            paths = RuntimePaths.under(root)
            store = SQLiteRuntimeStore(paths.runtime_database)
            account = _account()
            application = KairosApplication(
                ApplicationConfig(Environment.LIVE, paths),
                store,
                runtime_id="live-components",
                accounts=(account,),
                recovery=_ReadyRuntimeRecovery(),
                clock=FixedClock(at),
            )
            application.start()
            application.run()
            gateway = SimulatedExecutionAccountGateway(
                VenueId("simulated"),
                account,
                environment=Environment.LIVE,
                clock=FixedClock(at),
            )
            components = LiveRuntimeComponents(
                _live_runtime_config(),
                application,
                store,
                _live_catalog(account),
                gateway,
                gateway,
                accounts=(account,),
                market_event_source=AsyncIterableEventSource(("market-1",)),
                order_recovery_gateway=gateway,
                clock=FixedClock(at),
                max_market_events=1,
            )

            profile = bind_live_runtime_components(components)
            kernel = RunKernel(profile)
            request = self._run_request(
                mode=RunMode.LIVE,
                profile_id="profile:live",
                data_binding_hash="live-data-hash",
            )
            prepared = kernel.prepare(request)
            submit = kernel.submit((_order_request("client-1"),))

            self.assertEqual(prepared.evidence["runtime_bindings"]["binding_id"], "live-runtime-binding")
            self.assertEqual(prepared.evidence["runtime_bindings"]["market_event_provider"], "live-runtime-binding:market-events")
            self.assertEqual(prepared.evidence["runtime_bindings"]["command_submitter"], "live-runtime-binding:outbox")
            self.assertEqual(prepared.evidence["runtime_bindings"]["recovery_handler"], "live-runtime-binding:recovery")
            self.assertEqual(tuple(kernel.market_events(prepared)), ("market-1",))
            self.assertEqual(submit.accepted_command_ids, ("submit:client-1",))
            self.assertEqual(submit.evidence["outbox"][0]["status"], OutboxStatus.COMPLETED.value)
            order = store.order("client-1")
            assert order is not None
            self.assertEqual(order.status, DurableOrderStatus.ACKNOWLEDGED)
            self.assertEqual(gateway.open_orders(account), (order.ack.venue_order_id,))  # type: ignore[union-attr]

    def test_live_provider_ports_factory_builds_binance_live_ports_without_capability_model(self) -> None:
        account = AccountRef(InstitutionId("binance"), "main", AccountType.CRYPTO_SPOT)
        config = KairosProjectConfig(
            Path("/tmp/kairospy-live-test"),
            Path("/tmp/kairospy-live-test/kairos.toml"),
            {
                "providers": {
                    "binance": {
                        "live": {
                            "api_key": "test-key",
                            "api_secret": "test-secret",
                        },
                    },
                },
            },
        )

        ports = build_live_provider_ports(
            config,
            provider="binance",
            execution_driver="binance-live",
            account=account.value,
            reference_catalog=_binance_live_catalog(),
            transport_factory=lambda base_url: SimpleNamespace(base_url=base_url),
        )

        self.assertIsInstance(ports, LiveProviderPorts)
        self.assertEqual(parse_account_ref(account.value), account)
        self.assertEqual(ports.account, account)
        self.assertEqual(ports.provider, "binance")
        self.assertIs(ports.execution_gateway.environment, Environment.LIVE)
        self.assertIs(ports.account_gateway.environment, Environment.LIVE)
        self.assertIs(ports.order_recovery_gateway, ports.execution_gateway)
        self.assertEqual(ports.execution_gateway.instrument_symbols[InstrumentId("BTC-USDT")], "BTCUSDT")

    def test_live_market_event_source_factory_builds_feed_channel_from_live_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_id = str(BTC_SPOT_DAILY.key)
            live_view_id = "live:binance:btcusdt-book"
            _write_binance_live_view_manifest(root, dataset_id, live_view_id)
            config = KairosProjectConfig(
                root,
                root / "kairos.toml",
                {
                    "data": {"lake_root": str(root)},
                    "providers": {"binance": {"live": {"api_key": "key", "api_secret": "secret"}}},
                },
            )

            binding = build_live_market_event_source(
                config,
                provider="binance",
                name="ticks",
                dataset=dataset_id,
                live_view_id=live_view_id,
                journal_root=root / "journals",
            )

            self.assertIsInstance(binding, LiveMarketEventSourceBinding)
            self.assertEqual(binding.provider, "binance")
            self.assertEqual(binding.service_id, "feed:ticks:live:binance:btcusdt-book")
            self.assertEqual(len(binding.plan_hash), 64)
            self.assertEqual(len(binding.service_bundle_hash), 64)
            self.assertTrue(hasattr(binding.event_source, "events"))
            self.assertEqual(
                sorted(item.name for item in binding.managed_services),
                [
                    "feed-monitor:ticks:live:binance:btcusdt-book",
                    "feed:ticks:live:binance:btcusdt-book",
                ],
            )

    def test_simulation_profile_separates_runtime_rehearsal_from_backtest_and_live(self) -> None:
        readiness = (ReadinessEvidence(
            "simulation",
            "pass",
            required_ports=("market", "reference", "execution"),
            evidence_refs={"dataset": "release:abc", "strategy": "strategy:def"},
        ),)
        profile = historical_replay_simulation_profile(
            profile_id="hist-sim-v1",
            dataset_hash="dataset-hash",
            strategy_hash="strategy-hash",
            config_hash="config-hash",
            readiness_evidence=readiness,
        )

        self.assertEqual(profile.mode, RunMode.HISTORICAL_SIMULATION)
        self.assertEqual(profile.market_source, SimulationMarketSource.HISTORICAL_REPLAY)
        self.assertEqual(profile.execution_adapter, SimulationExecutionBinding.LOCAL_SIMULATED)
        self.assertEqual(profile.clock, SimulationClock.REPLAY)
        self.assertEqual(profile.required_ports, ("market", "reference", "execution"))
        self.assertTrue(profile.require_ready().passed)
        self.assertEqual(len(profile.profile_hash), 64)

    def test_simulation_profile_models_paper_and_testnet_without_live_execution(self) -> None:
        readiness = (ReadinessEvidence(
            "simulation",
            "pass",
            required_ports=("market", "reference", "execution", "account"),
            evidence_refs={"account": "paper-account-ready"},
        ),)
        paper = paper_simulation_profile(
            provider="binance",
            dataset_hash="dataset-hash",
            strategy_hash="strategy-hash",
            config_hash="config-hash",
            readiness_evidence=readiness,
        )
        testnet = exchange_testnet_simulation_profile(
            provider="binance",
            dataset_hash="dataset-hash",
            strategy_hash="strategy-hash",
            config_hash="config-hash",
            readiness_evidence=readiness,
        )

        self.assertEqual(paper.mode, RunMode.PAPER_TRADING)
        self.assertEqual(testnet.mode, RunMode.PAPER_TRADING)
        self.assertEqual(paper.execution_adapter, SimulationExecutionBinding.PAPER_ACCOUNT)
        self.assertEqual(testnet.execution_adapter, SimulationExecutionBinding.TESTNET)
        self.assertEqual(paper.required_ports, ("market", "reference", "execution", "account"))

    def test_simulation_profile_requires_evidence_and_rejects_live_mode(self) -> None:
        profile = historical_replay_simulation_profile(
            profile_id="hist-sim-v1",
            dataset_hash="dataset-hash",
            strategy_hash="strategy-hash",
            config_hash="config-hash",
        )
        with self.assertRaises(ReadinessError):
            profile.require_ready()
        with self.assertRaisesRegex(ValueError, "only supports"):
            SimulationProfile(
                profile_id="live",
                mode=RunMode.LIVE,
                market_source=SimulationMarketSource.LIVE_CONNECTOR,
                execution_adapter=SimulationExecutionBinding.PAPER_ACCOUNT,
                clock=SimulationClock.SYSTEM,
                dataset_hash="dataset-hash",
                strategy_hash="strategy-hash",
                config_hash="config-hash",
                connector_id="binance",
            )

    def test_runtime_feed_plan_consumes_live_view_bindings(self) -> None:
        plan = runtime_feed_plan("paper", ({
            "name": "bars",
            "dataset": "market.ohlcv.test",
            "live_view_id": "live:test",
            "event_source_contract": "EventSource[DataSetRecord]",
            "channel_contract": "BoundedEventChannel",
            "freshness_gate": {"passed": True},
        },))

        self.assertEqual(plan.mode, RunMode.PAPER_TRADING)
        self.assertEqual(plan.services[0].live_view_id, "live:test")
        self.assertEqual(plan.services[0].service_id, "feed:bars:live:test")
        self.assertEqual(plan.manifest()["services"][0]["service_id"], "feed:bars:live:test")
        self.assertEqual(plan.services[0].capture_policy, CapturePolicy.RAW_AND_CANONICAL)
        self.assertEqual(len(plan.plan_hash), 64)
        self.assertEqual(plan.service_bundle_manifest()["feed_service_ids"], ["feed:bars:live:test"])
        self.assertEqual(plan.service_bundle_manifest()["monitor_service_ids"], ["feed-monitor:bars:live:test"])
        self.assertEqual(plan.service_bundle_manifest()["plan_hash"], plan.plan_hash)
        self.assertEqual(len(plan.service_bundle_hash), 64)

    def test_runtime_feed_plan_rejects_unhealthy_binding(self) -> None:
        with self.assertRaisesRegex(ValueError, "freshness gate"):
            runtime_feed_plan("paper", ({
                "name": "bars",
                "dataset": "market.ohlcv.test",
                "live_view_id": "live:test",
                "freshness_gate": {"passed": False},
            },))

    def test_runtime_feed_plan_rejects_incomplete_binding_contracts(self) -> None:
        with self.assertRaisesRegex(ValueError, "event_source_contract"):
            runtime_feed_plan("paper", ({
                "name": "bars",
                "dataset": "market.ohlcv.test",
                "live_view_id": "live:test",
                "event_source_contract": "",
                "channel_contract": "BoundedEventChannel",
                "freshness_gate": {"passed": True},
            },))

    def test_runtime_feed_plan_rejects_duplicate_service_ids(self) -> None:
        binding = {
            "name": "bars",
            "dataset": "market.ohlcv.test",
            "live_view_id": "live:test",
            "event_source_contract": "EventSource[DataSetRecord]",
            "channel_contract": "BoundedEventChannel",
            "freshness_gate": {"passed": True},
        }
        with self.assertRaisesRegex(ValueError, "service ids"):
            runtime_feed_plan("paper", (binding, binding))

    async def test_runtime_feed_plan_starts_managed_feed_services(self) -> None:
        stopped = asyncio.Event()
        plan = runtime_feed_plan("paper", ({
            "name": "bars",
            "dataset": "market.ohlcv.test",
            "live_view_id": "live:test",
            "event_source_contract": "EventSource[DataSetRecord]",
            "channel_contract": "BoundedEventChannel",
            "freshness_gate": {"passed": True},
        },))

        def runner_factory(_service):
            async def run():
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            return run

        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(Path(directory))
            app = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="feed-plan-runtime",
            )
            runtime = AsyncKairosRuntime(app, plan.managed_services(runner_factory))
            await runtime.start()

            self.assertEqual(app.status, RuntimeStatus.RUNNING)
            self.assertEqual(runtime.service_snapshots()[0].status, ManagedServiceStatus.RUNNING)
            await runtime.stop()

        self.assertTrue(stopped.is_set())

    async def test_unbound_feed_plan_fails_closed_at_runtime_start(self) -> None:
        plan = runtime_feed_plan("paper", ({
            "name": "bars",
            "dataset": "market.ohlcv.test",
            "live_view_id": "live:test",
            "event_source_contract": "EventSource[DataSetRecord]",
            "channel_contract": "BoundedEventChannel",
            "freshness_gate": {"passed": True},
        },))

        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(Path(directory))
            app = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="unbound-feed-plan-runtime",
            )
            runtime = AsyncKairosRuntime(app, plan.managed_services())

            with self.assertRaisesRegex(RuntimeError, "critical managed service failed"):
                await runtime.start()

    async def test_runtime_execution_plan_starts_injected_gateway_service(self) -> None:
        stopped = asyncio.Event()
        plan = runtime_execution_plan("paper", paper_trading_composition("binance"))

        def runner_factory(_service):
            async def run():
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            return run

        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(Path(directory))
            app = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="execution-plan-runtime",
            )
            runtime = AsyncKairosRuntime(app, plan.managed_services(runner_factory))
            await runtime.start()
            snapshots = {item.name: item.status for item in runtime.service_snapshots()}
            await runtime.stop()

        self.assertEqual(len(plan.plan_hash), 64)
        self.assertEqual(plan.services[0].service_id, "execution:paper-trading:simulated")
        self.assertEqual(snapshots["execution:paper-trading:simulated"], ManagedServiceStatus.RUNNING)
        self.assertTrue(stopped.is_set())

    async def test_unbound_execution_plan_fails_closed_at_runtime_start(self) -> None:
        plan = runtime_execution_plan("live", live_composition("binance", "binance-live"))
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(Path(directory))
            app = KairosApplication(
                ApplicationConfig(Environment.LIVE, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="unbound-execution-plan-runtime",
            )
            runtime = AsyncKairosRuntime(app, plan.managed_services())

            with self.assertRaisesRegex(RuntimeError, "critical managed service failed"):
                await runtime.start()

    async def test_runtime_strategy_plan_starts_injected_strategy_service(self) -> None:
        stopped = asyncio.Event()
        plan = runtime_strategy_plan("paper", strategy_id="strategy-v1", target_hash="abc123")

        def runner_factory(_service):
            async def run():
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            return run

        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(Path(directory))
            app = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="strategy-plan-runtime",
            )
            runtime = AsyncKairosRuntime(app, plan.managed_services(runner_factory))
            await runtime.start()
            snapshots = {item.name: item.status for item in runtime.service_snapshots()}
            await runtime.stop()

        self.assertEqual(len(plan.plan_hash), 64)
        self.assertEqual(plan.services[0].service_id, "strategy:paper-trading:strategy-v1")
        self.assertEqual(snapshots["strategy:paper-trading:strategy-v1"], ManagedServiceStatus.RUNNING)
        self.assertTrue(stopped.is_set())

    async def test_unbound_strategy_plan_fails_closed_at_runtime_start(self) -> None:
        plan = runtime_strategy_plan("paper", strategy_id="strategy-v1", target_hash="abc123")
        with tempfile.TemporaryDirectory() as directory:
            paths = RuntimePaths.under(Path(directory))
            app = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="unbound-strategy-plan-runtime",
            )
            runtime = AsyncKairosRuntime(app, plan.managed_services())

            with self.assertRaisesRegex(RuntimeError, "critical managed service failed"):
                await runtime.start()

    async def test_runtime_supervises_freshness_monitor_writing_live_view_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_id = str(BTC_SPOT_DAILY.key)
            contract_hash = DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash
            path = live_view_manifest_path(root, dataset_id, "live:monitor")
            write_live_view_manifest(path, LiveViewManifest(
                dataset_id,
                "live:monitor",
                contract_hash,
                "connector-hash",
                "available_time",
                ("available_time", "close"),
                {"channel_contract": "BoundedEventChannel", "freshness": {"max_age_seconds": 60}},
                {"kind": "live_connector"},
                "configured",
                "2026-07-20T00:00:00+00:00",
            ))
            class Metrics:
                capacity = 16
                peak_depth = 1
                dropped = 0

            class Channel:
                metrics = Metrics()

            class Service:
                raw_messages = 3
                canonical_events = 3
                ignored_messages = 0
                reconnects = 0
                canonical_capture = None

            monitor = LiveViewFreshnessMonitor(
                path,
                lambda: live_view_freshness_evidence(
                    Service(), Channel(), source="fixture", stream_id="fixture@quote",
                ),
                interval_seconds=0.01,
            )
            paths = RuntimePaths.under(root / "runtime")
            app = KairosApplication(
                ApplicationConfig(Environment.PAPER, paths), SQLiteRuntimeStore(paths.runtime_database),
                runtime_id="freshness-monitor-runtime",
            )
            feed_plan = runtime_feed_plan("paper", ({
                "name": "bars",
                "dataset": dataset_id,
                "live_view_id": "live:monitor",
                "event_source_contract": "EventSource[DataSetRecord]",
                "channel_contract": "BoundedEventChannel",
                "freshness_gate": {"passed": True},
            },))
            feed_stopped = asyncio.Event()

            def feed_runner_factory(_service):
                async def run():
                    try:
                        await asyncio.Event().wait()
                    finally:
                        feed_stopped.set()
                return run

            bundle = feed_plan.managed_service_bundle(
                feed_runner_factory=feed_runner_factory,
                monitor_runner_factory=lambda _service: monitor.run,
            )
            self.assertIsInstance(bundle, RuntimeFeedServiceBundle)
            self.assertEqual(len(bundle.bundle_hash), 64)
            self.assertEqual(
                bundle.manifest()["monitor_service_ids"],
                ["feed-monitor:bars:live:monitor"],
            )
            runtime = AsyncKairosRuntime(app, bundle.services)
            await runtime.start()
            await asyncio.sleep(0.02)
            snapshots = {item.name: item.status for item in runtime.service_snapshots()}
            await runtime.stop()

            updated = evaluate_live_view_freshness(
                write_manifest := load_live_view_manifest(path),
                policy=PAPER_LIVE_FRESHNESS_POLICY,
            )

        self.assertEqual(app.status, RuntimeStatus.STOPPED)
        self.assertEqual(snapshots["feed:bars:live:monitor"], ManagedServiceStatus.RUNNING)
        self.assertEqual(snapshots["feed-monitor:bars:live:monitor"], ManagedServiceStatus.RUNNING)
        self.assertTrue(feed_stopped.is_set())
        self.assertTrue(updated.passed)
        self.assertEqual(write_manifest.freshness_status, "healthy")

    def test_runtime_feed_service_bundle_requires_monitor_factory(self) -> None:
        plan = runtime_feed_plan("paper", ({
            "name": "bars",
            "dataset": "market.ohlcv.test",
            "live_view_id": "live:test",
            "event_source_contract": "EventSource[DataSetRecord]",
            "channel_contract": "BoundedEventChannel",
            "freshness_gate": {"passed": True},
        },))

        with self.assertRaisesRegex(ValueError, "monitor"):
            plan.managed_service_bundle(
                feed_runner_factory=lambda _service: (lambda: asyncio.sleep(60)),
                monitor_runner_factory=None,
            )

class _MemoryRunProfile:
    profile_id = "profile:backtest"
    mode = RunMode.BACKTEST
    profile_hash = "profile-hash"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def manifest(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "mode": self.mode.value, "profile_hash": self.profile_hash}

    def prepare(self, request: RunRequest) -> PreparedRun:
        self.calls.append("prepare")
        return PreparedRun(
            request,
            self.profile_id,
            self.mode,
            "frozen-release",
            "fill-model",
            "backtest-artifact",
            "readiness-hash",
            "none",
            "governance-run-artifact",
            self.profile_hash,
            {"prepared": True},
        )

    def market_events(self, prepared: PreparedRun):
        return ("market-event",)

    def execution_events(self, prepared: PreparedRun):
        return ("execution-event",)

    def submit(self, commands) -> SubmitResult:
        return SubmitResult(tuple(str(item) for item in commands))

    def recover(self, prepared: PreparedRun) -> RecoveryResult:
        self.calls.append("recover")
        return RecoveryResult(False, True, {"recovered": True})

    def finalize(self, prepared: PreparedRun) -> ProfileResult:
        self.calls.append("finalize")
        return ProfileResult(
            RunStatus.SUCCEEDED,
            evidence={"finalized": True},
            artifact_refs=("artifact:run-1",),
            artifact_hash="artifact-hash",
        )


class _ReadyRuntimeRecovery:
    binding_id = "runtime-recovery-service"

    def recover(self, at):
        return SimpleNamespace(ready=True, recovered_at=at, reason="ready")


class _CompleteExecutionRecovery:
    service_id = "venue-order-recovery"

    def recover(self, at):
        return SimpleNamespace(complete=True, resolved=("client-1",), unresolved=())


class _SupervisorEvidence:
    healthy = True

    def snapshots(self):
        return (
            ManagedServiceSnapshot(
                "feed:bars",
                ServiceCriticality.CRITICAL,
                ManagedServiceStatus.RUNNING,
                1,
                0,
                None,
            ),
        )


def _account() -> AccountRef:
    return AccountRef(InstitutionId("simulated"), "paper", AccountType.CRYPTO_SPOT)


def _order_request(client_order_id: str) -> OrderRequest:
    return OrderRequest(
        f"internal-{client_order_id}",
        client_order_id,
        "strategy",
        "intent",
        "correlation",
        _account(),
        InstrumentId("BTC-USDT"),
        TradeSide.BUY,
        Decimal("1"),
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("100")),
    )


def _combo_order_request(client_order_id: str) -> ComboOrderRequest:
    return ComboOrderRequest(
        f"internal-{client_order_id}",
        client_order_id,
        "strategy",
        "intent",
        "correlation",
        _account(),
        (
            ComboLegRequest(InstrumentId("BTC-USDT-CALL"), TradeSide.BUY, 1),
            ComboLegRequest(InstrumentId("BTC-USDT-PUT"), TradeSide.SELL, 1),
        ),
        Decimal("1"),
        ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("10")),
    )


def _live_runtime_config() -> LiveRuntimeBindingConfig:
    readiness = (ReadinessEvidence(
        "live",
        "pass",
        required_ports=("market", "reference", "execution", "account"),
        evidence_refs={"connector": "simulated-live-ready"},
        account_binding="account-binding-hash",
        connector_id="simulated",
    ),)
    promotion = PromotionEvidence(
        StrategyLifecycle.PAPER_APPROVED,
        StrategyLifecycle.LIVE_LIMITED,
        "live-data-hash",
        "strategy-hash",
        "config-hash",
        True,
        evidence_refs={"readiness": "readiness:live"},
    )
    return LiveRuntimeBindingConfig(
        "profile:live",
        "simulated",
        "simulated-live",
        "account-binding-hash",
        "live-data-hash",
        "strategy-hash",
        "config-hash",
        readiness,
        promotion,
        "live-runtime-binding",
        "runtime-recovery",
        True,
        "ready",
    )


def _live_catalog(account: AccountRef) -> ReferenceCatalog:
    at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    catalog = ReferenceCatalog()
    publish_test_instrument(
        catalog,
        InstrumentId("BTC-USDT"),
        ProductType.CRYPTO_SPOT,
        "BTC/USDT",
        CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")),
        AssetId("USDT"),
        VenueId("simulated"),
        "BTCUSDT",
        at,
        quantity_increment=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
    )
    listing = catalog.active_listings(InstrumentId("BTC-USDT"), at)[0]
    catalog.routes.add(ExecutionRoute(
        RouteId("route:simulated:paper"),
        BrokerId("simulated"),
        account,
        listing.listing_id,
        at,
    ))
    return catalog


class _AsyncEventSource:
    def __init__(self, events: tuple[object, ...]) -> None:
        self._events = events

    async def events(self):
        for event in self._events:
            yield event


class _SingleFactorRuntime:
    def __init__(self, instrument_id: InstrumentId, as_of: datetime, available_time: datetime) -> None:
        self.instrument_id = instrument_id
        self.as_of = as_of
        self.available_time = available_time

    def update(self, event: object):
        return _TestFactorSnapshot(
            factor_id="test-factor",
            as_of=self.as_of,
            values=(("instrument_id", self.instrument_id.value),),
            quality="computed",
            input_identity=getattr(event, "source_instance", "test-source"),
            state_hash="factor-state",
            available_time=self.available_time,
        )


@dataclass(frozen=True, slots=True)
class _TestFactorSnapshot:
    factor_id: str
    as_of: datetime
    values: tuple[tuple[str, object], ...]
    quality: str
    input_identity: str
    state_hash: str
    available_time: datetime


class _NoopStrategyRuntime:
    strategy = SimpleNamespace(decisions=())

    def on_start(self, context: Context):
        return None

    def on_market(self, context: Context):
        return None

    def on_end(self, context: Context):
        return None


def _binance_live_catalog() -> ReferenceCatalog:
    at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    catalog = ReferenceCatalog()
    publish_test_instrument(
        catalog,
        InstrumentId("BTC-USDT"),
        ProductType.CRYPTO_SPOT,
        "BTC/USDT",
        CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")),
        AssetId("USDT"),
        VenueId("binance"),
        "BTCUSDT",
        at,
        quantity_increment=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
    )
    return catalog


def _write_binance_live_view_manifest(root: Path, dataset_id: str, live_view_id: str) -> Path:
    manifest = LiveViewManifest(
        dataset_id,
        live_view_id,
        DataSetContractArtifact.from_product_contract(BTC_SPOT_DAILY).contract_hash,
        "connector-hash",
        "available_time",
        ("available_time", "bid", "ask"),
        {
            "provider": "binance",
            "event_source_contract": "EventSource[DataSetRecord]",
            "channel_contract": "BoundedEventChannel",
            "freshness": {"max_age_seconds": 60},
            "channel_capacity": 8,
        },
        {
            "kind": "binance_market_stream",
            "provider": "binance",
            "symbol": "BTCUSDT",
            "channel": "bookTicker",
            "instrument_id": "crypto:binance:spot:BTCUSDT",
            "public_only": True,
        },
        "configured",
        "2026-07-20T00:00:00+00:00",
    )
    path = live_view_manifest_path(root, dataset_id, live_view_id)
    write_live_view_manifest(path, manifest)
    return path


def _empty_strategy_result() -> StrategyRunResult:
    factor_hash = _hash(())
    decision_hash = _hash(())
    intent_hash = _hash(())
    audit_hash = _hash({
        "events": [],
        "factor_hash": factor_hash,
        "decision_hash": decision_hash,
        "intent_hash": intent_hash,
    })
    return StrategyRunResult((), (), (), (), factor_hash, decision_hash, intent_hash, audit_hash)


def _strategy_result_with_context() -> StrategyRunResult:
    factor_hash = _hash(())
    decision_hash = _hash(())
    intent_hash = _hash(())
    context_view_hashes = {
        view: _hash({"view": view, "schema": "test"})
        for view in ("market", "portfolio", "features", "reference", "orders", "intents", "budget")
    }
    context_hash = _hash(context_view_hashes)
    audit_hash = _hash({
        "events": [],
        "factor_hash": factor_hash,
        "decision_hash": decision_hash,
        "intent_hash": intent_hash,
        "context_hash": context_hash,
    })
    return StrategyRunResult(
        (),
        (),
        (),
        (),
        factor_hash,
        decision_hash,
        intent_hash,
        audit_hash,
        context_view_hashes,
        context_hash,
    )


def _hash(value: object) -> str:
    return sha256(json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


if __name__ == "__main__":
    unittest.main()
