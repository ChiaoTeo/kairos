from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from kairospy import initialize_project
from kairospy.runtime.subscriptions import MarketDataSubscriptionRequest, RunSubscriptionSet, RunWorkspaceSession
from kairospy.surface.cli.main import main


async def _idle_service() -> None:
    import asyncio

    await asyncio.Future()


class RunSubscriptionTests(unittest.TestCase):
    def test_subscription_set_applies_stream_and_space_updates(self) -> None:
        at = datetime(2026, 7, 23, tzinfo=timezone.utc)
        subscriptions = RunSubscriptionSet("funding-arb")

        subscriptions = subscriptions.apply(
            scope="streams",
            operation="add",
            values=("binance_swap_btcusdt.orderbook", "hyperliquid_perp_btc.orderbook"),
            at=at,
        )
        self.assertEqual(subscriptions.active_streams, (
            "binance_swap_btcusdt.orderbook",
            "hyperliquid_perp_btc.orderbook",
        ))

        subscriptions = subscriptions.apply(
            scope="spaces",
            operation="set",
            values=("binance_swap_ethusdt",),
            at=at,
        )
        self.assertEqual(subscriptions.active_spaces, ("binance_swap_ethusdt",))

        subscriptions = subscriptions.apply(
            scope="streams",
            operation="remove",
            values=("binance_swap_btcusdt.orderbook",),
            at=at,
        )
        self.assertEqual(subscriptions.active_streams, ("hyperliquid_perp_btc.orderbook",))

    def test_workspace_session_builds_resolved_live_feed_plan_from_active_streams(self) -> None:
        subscriptions = RunSubscriptionSet(
            "funding-arb",
            active_streams=("binance_swap_btcusdt.orderbook", "hyperliquid_perp_btc.ohlcv_1h"),
            active_spaces=("binance_swap_ethusdt",),
            updated_at="2026-07-23T00:00:00+00:00",
        )

        session = RunWorkspaceSession.from_subscription_set(subscriptions).to_payload()

        self.assertEqual(session["active_streams"], [
            "binance_swap_btcusdt.orderbook",
            "hyperliquid_perp_btc.ohlcv_1h",
        ])
        self.assertEqual(session["feed_plan"]["status"], "partial")
        ready = session["feed_plan"]["streams"][0]
        self.assertEqual(ready["stream"], "binance_swap_btcusdt.orderbook")
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["source_plan"]["product_key"], "binance.orderbook")
        not_live = session["feed_plan"]["streams"][1]
        self.assertEqual(not_live["status"], "not_live")
        self.assertEqual(session["feed_plan"]["spaces"][0]["status"], "missing_template")

    def test_workspace_session_expands_active_spaces_through_workspace_stream_templates(self) -> None:
        subscriptions = RunSubscriptionSet(
            "funding-arb",
            active_spaces=("binance_swap_solusdt",),
            updated_at="2026-07-23T00:00:00+00:00",
        )
        workspace_snapshot = {
            "schema_version": 1,
            "attachments": {
                "book_template": {
                    "name": "book_template",
                    "kind": "attachment",
                    "dataset": "{space}.orderbook",
                    "stream": "{space}.orderbook",
                    "params": {"template": True, "view": "live"},
                },
            },
        }

        session = RunWorkspaceSession.from_subscription_set(
            subscriptions,
            workspace_snapshot=workspace_snapshot,
        ).to_payload()

        self.assertEqual(session["active_spaces"], ["binance_swap_solusdt"])
        self.assertEqual(session["feed_plan"]["spaces"][0]["status"], "expanded")
        self.assertEqual(session["feed_plan"]["expanded_streams"], ["binance_swap_solusdt.orderbook"])
        self.assertEqual(session["feed_plan"]["streams"][0]["stream"], "binance_swap_solusdt.orderbook")
        self.assertEqual(session["feed_plan"]["streams"][0]["status"], "ready")

    def test_strategy_subscription_request_submits_runtime_operator_command(self) -> None:
        from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

        with TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            at = datetime(2026, 7, 23, tzinfo=timezone.utc)

            command = MarketDataSubscriptionRequest.streams(
                "funding-arb",
                "add",
                ("binance_swap_btcusdt.orderbook",),
                actor="strategy:funding-arb",
                reason="rotate hedge venue",
                request_id="strategy-subscription:1",
            ).submit(store, at=at)

            self.assertEqual(command.command_type.value, "update_subscriptions")
            self.assertEqual(command.actor, "strategy:funding-arb")
            self.assertEqual(command.reason, "rotate hedge venue")
            self.assertEqual(command.payload["scope"], "streams")
            self.assertEqual(command.payload["operation"], "add")
            self.assertEqual(command.payload["values"], ["binance_swap_btcusdt.orderbook"])
            self.assertEqual(command.payload["request_id"], "strategy-subscription:1")

    def test_run_live_stream_commands_submit_operator_command(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Run Subscriptions")

            with _cwd(root), StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--format", "json",
                    "run", "live", "streams", "add",
                    "--run-id", "funding-arb",
                    "binance_swap_btcusdt.orderbook",
                    "hyperliquid_perp_btc.orderbook",
                ]), 0)
                submitted = json.loads(output.getvalue())

            self.assertEqual(submitted["status"], "command_submitted")
            self.assertEqual(submitted["operator_command"]["command_type"], "update_subscriptions")
            self.assertEqual(submitted["operator_command"]["payload"]["scope"], "streams")
            self.assertEqual(submitted["operator_command"]["payload"]["operation"], "add")
            self.assertEqual(submitted["operator_command"]["payload"]["values"], [
                "binance_swap_btcusdt.orderbook",
                "hyperliquid_perp_btc.orderbook",
            ])

            with _cwd(root), StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--format", "json",
                    "run", "live", "commands",
                    "--run-id", "funding-arb",
                ]), 0)
                commands = json.loads(output.getvalue())
            self.assertEqual(commands["operator_commands"][0]["command_type"], "update_subscriptions")

    def test_update_subscriptions_command_persists_run_workspace_session(self) -> None:
        from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
        from kairospy.surface.product import _run_live_apply_operator_command

        with TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            at = datetime(2026, 7, 23, tzinfo=timezone.utc)
            clock = SimpleNamespace(now=lambda: at)
            phase = SimpleNamespace(value="running")
            daemon = SimpleNamespace(
                run_id="funding-arb",
                clock=clock,
                application=SimpleNamespace(store=store),
                status=lambda: SimpleNamespace(phase=phase),
                operator_command_handler=None,
            )
            command = SimpleNamespace(
                command_id="operator:add-btc",
                command_type="update_subscriptions",
                actor="cli",
                reason="add stream",
                payload={
                    "scope": "streams",
                    "operation": "add",
                    "values": ("binance_swap_btcusdt.orderbook",),
                    "lake_root": str(Path(directory) / "lake"),
                },
            )

            result = _run_live_apply_operator_command(daemon, command)

            self.assertEqual(result["workspace_session"]["feed_plan"]["streams"][0]["status"], "ready")
            self.assertEqual(result["subscription_changed_event"]["added_streams"], ["binance_swap_btcusdt.orderbook"])
            self.assertEqual(result["runtime_feed_reconciliation"]["added_streams"], ["binance_swap_btcusdt.orderbook"])
            self.assertEqual(result["runtime_feed_reconciliation"]["targets"][0]["action"], "start")
            self.assertTrue(Path(result["runtime_feed_reconciliation"]["targets"][0]["live_root"]).exists())
            session = store.runtime_state("workspace_session:funding-arb")
            self.assertEqual(session["active_streams"], ["binance_swap_btcusdt.orderbook"])
            self.assertEqual(session["feed_plan"]["streams"][0]["source_plan"]["product_key"], "binance.orderbook")
            event = store.runtime_state("subscription_changed:funding-arb:last")
            self.assertEqual(event["event_type"], "subscription_changed")
            self.assertEqual(event["added_streams"], ["binance_swap_btcusdt.orderbook"])
            feed_state = store.runtime_state("runtime_feed_reconciliation:funding-arb:last")
            self.assertEqual(feed_state["targets"][0]["service_status"], "pending_start")

    def test_remove_subscription_records_safety_transition_before_feed_removal(self) -> None:
        from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
        from kairospy.surface.product import _run_live_apply_operator_command

        with TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            at = datetime(2026, 7, 23, tzinfo=timezone.utc)
            store.set_runtime_state(
                "subscription_set:funding-arb",
                RunSubscriptionSet(
                    "funding-arb",
                    active_streams=("binance_swap_btcusdt.orderbook", "hyperliquid_perp_btc.orderbook"),
                    updated_at=at.isoformat(),
                ).to_payload(),
                at,
            )
            clock = SimpleNamespace(now=lambda: at)
            phase = SimpleNamespace(value="running")
            daemon = SimpleNamespace(
                run_id="funding-arb",
                clock=clock,
                application=SimpleNamespace(store=store),
                status=lambda: SimpleNamespace(phase=phase),
                operator_command_handler=None,
            )
            command = SimpleNamespace(
                command_id="operator:remove-btc",
                command_type="update_subscriptions",
                actor="strategy:funding-arb",
                reason="rotate out btc hedge",
                payload={
                    "scope": "streams",
                    "operation": "remove",
                    "values": ("binance_swap_btcusdt.orderbook",),
                    "lake_root": str(Path(directory) / "lake"),
                },
            )

            result = _run_live_apply_operator_command(daemon, command)

            self.assertEqual(result["subscription_changed_event"]["removed_streams"], ["binance_swap_btcusdt.orderbook"])
            self.assertEqual(result["runtime_feed_reconciliation"]["removed_streams"], ["binance_swap_btcusdt.orderbook"])
            stop_targets = [
                item for item in result["runtime_feed_reconciliation"]["targets"]
                if item["stream"] == "binance_swap_btcusdt.orderbook"
            ]
            self.assertEqual(stop_targets[0]["action"], "stop")
            self.assertEqual(stop_targets[0]["service_status"], "pending_stop")
            self.assertEqual(result["subscription_removal_safety"]["event_type"], "subscription_removal_safety_transition")
            self.assertTrue(result["subscription_removal_safety"]["pause_new_orders"])
            self.assertEqual(result["subscription_removal_safety"]["feed_stop_policy"], "after_order_position_policy_completes")
            risk_state = store.runtime_state("risk_runtime:last")
            self.assertEqual(risk_state["status"], "paused")
            self.assertEqual(risk_state["source"], "subscription_removal")
            safety = store.runtime_state("subscription_removal_safety:funding-arb:last")
            self.assertEqual(safety["removed_streams"], ["binance_swap_btcusdt.orderbook"])
            self.assertEqual(safety["cancel_open_orders_policy"], "requires_explicit_keep_open_or_bound_cancel_adapter")

    def test_strategy_market_view_can_carry_runtime_subscription_state(self) -> None:
        from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
        from kairospy.strategy.views import MarketView
        from kairospy.surface.product import _market_view_from_snapshot_with_runtime_state, _runtime_subscription_state

        with TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            at = datetime(2026, 7, 23, tzinfo=timezone.utc)
            store.set_runtime_state(
                "subscription_set:funding-arb",
                RunSubscriptionSet(
                    "funding-arb",
                    active_streams=("binance_swap_btcusdt.orderbook",),
                    updated_at=at.isoformat(),
                ).to_payload(),
                at,
            )
            store.set_runtime_state(
                "subscription_changed:funding-arb:last",
                {
                    "event_type": "subscription_changed",
                    "run_id": "funding-arb",
                    "added_streams": ["binance_swap_btcusdt.orderbook"],
                    "removed_streams": [],
                },
                at,
            )
            store.set_runtime_state(
                "runtime_feed_services:funding-arb:last",
                {
                    "run_id": "funding-arb",
                    "status": "applied",
                    "dynamic_feed_services": ["feed:dynamic:binance_swap_btcusdt.orderbook"],
                },
                at,
            )
            store.set_runtime_state(
                "market_freshness:funding-arb:last",
                {"run_id": "funding-arb", "status": "fresh"},
                at,
            )

            state = _runtime_subscription_state(store, "funding-arb")
            market = _market_view_from_snapshot_with_runtime_state(
                MarketView(at, 1, ()),
                state,
            )

            self.assertEqual(market.subscription_set["active_streams"], ["binance_swap_btcusdt.orderbook"])
            self.assertEqual(market.subscription_changed_event["added_streams"], ["binance_swap_btcusdt.orderbook"])
            self.assertEqual(market.runtime_feed_services["dynamic_feed_services"], ["feed:dynamic:binance_swap_btcusdt.orderbook"])
            self.assertEqual(market.market_freshness["status"], "fresh")
            self.assertEqual(len(market.view_hash), 64)

    def test_live_daemon_applies_feed_reconciliation_to_dynamic_services(self) -> None:
        import asyncio

        from kairospy.environment import Environment
        from kairospy.runtime import LiveRunDaemon, ManagedServiceSpec
        from kairospy.runtime.application import KairosApplication
        from kairospy.runtime.config import ApplicationConfig, RuntimePaths
        from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
        from kairospy.surface.product import _run_live_apply_operator_command

        async def scenario() -> None:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                store = SQLiteRuntimeStore(root / "runtime.sqlite3")
                app = KairosApplication(
                    ApplicationConfig(Environment.LIVE, RuntimePaths.under(root / "runtime")),
                    store,
                    runtime_id="dynamic-feed-runtime",
                )
                daemon = LiveRunDaemon(
                    app,
                    (ManagedServiceSpec("strategy-run:funding-arb", _idle_service),),
                    run_id="funding-arb",
                )
                await daemon.start()
                try:
                    at = daemon.clock.now()
                    command = SimpleNamespace(
                        command_id="operator:add-dynamic-feed",
                        command_type="update_subscriptions",
                        actor="cli",
                        reason="add btc feed",
                        payload={
                            "scope": "streams",
                            "operation": "add",
                            "values": ("binance_swap_btcusdt.orderbook",),
                            "lake_root": str(root / "lake"),
                        },
                    )

                    result = _run_live_apply_operator_command(daemon, command)
                    await asyncio.sleep(0)

                    self.assertEqual(result["runtime_feed_services"]["status"], "applied")
                    self.assertIn(
                        "feed:dynamic:binance_swap_btcusdt.orderbook",
                        result["runtime_feed_services"]["dynamic_feed_services"],
                    )
                    self.assertIn(
                        "feed:dynamic:binance_swap_btcusdt.orderbook",
                        [item.name for item in daemon.status().services],
                    )
                    feed_services = store.runtime_state("runtime_feed_services:funding-arb:last")
                    self.assertIn("feed:dynamic:binance_swap_btcusdt.orderbook", feed_services["dynamic_feed_services"])

                    remove = SimpleNamespace(
                        command_id="operator:remove-dynamic-feed",
                        command_type="update_subscriptions",
                        actor="cli",
                        reason="remove btc feed",
                        payload={
                            "scope": "streams",
                            "operation": "remove",
                            "values": ("binance_swap_btcusdt.orderbook",),
                            "lake_root": str(root / "lake"),
                        },
                    )
                    removed = _run_live_apply_operator_command(daemon, remove)
                    await asyncio.sleep(0)
                    self.assertEqual(removed["runtime_feed_services"]["dynamic_feed_services"], [])
                    self.assertNotIn(
                        "feed:dynamic:binance_swap_btcusdt.orderbook",
                        [item.name for item in daemon.status().services],
                    )
                finally:
                    await daemon.stop()

        asyncio.run(scenario())

    def test_run_live_streams_show_exposes_runtime_feed_services(self) -> None:
        from kairospy.runtime.config import RuntimePaths
        from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Run Feed Services")
            run_id = "funding-arb"
            runtime_root = root / ".kairos" / "runtime" / "live" / run_id
            store = SQLiteRuntimeStore(RuntimePaths.under(runtime_root).runtime_database)
            at = datetime(2026, 7, 23, tzinfo=timezone.utc)
            store.set_runtime_state(
                f"runtime_feed_services:{run_id}:last",
                {
                    "run_id": run_id,
                    "status": "applied",
                    "dynamic_feed_services": ["feed:dynamic:binance_swap_btcusdt.orderbook"],
                    "updated_at": at.isoformat(),
                },
                at,
            )

            with _cwd(root), StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--format", "json",
                    "run", "live", "streams", "show",
                    "--run-id", run_id,
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["runtime_feed_services"]["status"], "applied")
            self.assertEqual(
                payload["runtime_feed_services"]["dynamic_feed_services"],
                ["feed:dynamic:binance_swap_btcusdt.orderbook"],
            )

    def test_run_live_spaces_show_reports_subscription_set_without_workspace_mutation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_project(root, name="Run Spaces")

            with _cwd(root), StringIO() as output, redirect_stdout(output):
                self.assertEqual(main([
                    "--format", "json",
                    "run", "live", "spaces", "show",
                    "--run-id", "funding-arb",
                ]), 0)
                payload = json.loads(output.getvalue())

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["subscription_set"]["active_spaces"], [])
            self.assertEqual(payload["workspace_session"]["active_spaces"], [])
            self.assertFalse((root / ".kairos" / "workspace" / "funding-arb" / "workspace.json").exists())


class _cwd:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.previous: Path | None = None

    def __enter__(self) -> None:
        import os

        self.previous = Path.cwd()
        os.chdir(self.path)

    def __exit__(self, exc_type, exc, tb) -> None:
        import os

        assert self.previous is not None
        os.chdir(self.previous)


if __name__ == "__main__":
    unittest.main()
