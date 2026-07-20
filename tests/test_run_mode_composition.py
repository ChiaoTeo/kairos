from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from kairospy.application import (
    ApplicationConfig, AsyncKairosRuntime, KairosApplication, ManagedServiceStatus, RuntimePaths,
    RunModeComposition, RuntimeFeedServiceBundle, RuntimeStatus, backtest_composition,
    historical_simulation_composition, live_composition, paper_trading_composition, study_composition,
    runtime_execution_plan, runtime_feed_plan, runtime_strategy_plan,
)
from kairospy.ports import Environment
from kairospy.data import (
    DataSetContractArtifact, LiveViewFreshnessMonitor, LiveViewManifest, PAPER_LIVE_FRESHNESS_POLICY,
    evaluate_live_view_freshness, live_view_freshness_evidence, live_view_manifest_path, load_live_view_manifest,
    write_live_view_manifest,
)
from kairospy.data.contracts import RunMode
from kairospy.data.products import BTC_SPOT_DAILY
from kairospy.market_data import CapturePolicy
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore


class RunModeCompositionTests(unittest.IsolatedAsyncioTestCase):
    def test_all_promotion_modes_have_explicit_replaceable_dependencies(self) -> None:
        values = (
            study_composition(), backtest_composition(), historical_simulation_composition(),
            paper_trading_composition("binance"), live_composition("binance", "binance-live"),
        )

        self.assertEqual([item.mode for item in values], [
            RunMode.STUDY, RunMode.BACKTEST, RunMode.HISTORICAL_SIMULATION,
            RunMode.PAPER_TRADING, RunMode.LIVE,
        ])
        self.assertEqual(len({item.composition_hash for item in values}), len(values))
        self.assertEqual(paper_trading_composition("binance").composition_hash,
                         paper_trading_composition("binance").composition_hash)

    def test_legacy_study_mode_alias_is_not_public_api(self) -> None:
        with self.assertRaises(ValueError):
            RunMode("re" + "search")
        self.assertEqual(study_composition().mode, RunMode.STUDY)

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


if __name__ == "__main__":
    unittest.main()
