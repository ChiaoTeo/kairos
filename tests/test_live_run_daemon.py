from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from kairospy.data.contracts import RunMode
from kairospy.integrations.ports import Environment
from kairospy.reference.contracts import ProductType
from kairospy.runtime import (
    LiveRunDaemon,
    LiveRunDaemonPhase,
    LiveRunRegistry,
    LiveRunKernelService,
    ManagedServiceSpec,
    ManagedServiceStatus,
    OperatorCommandBus,
    OperatorCommandStatus,
    OperatorCommandType,
    PreparedRun,
    ProfileResult,
    RecoveryResult,
    RunArtifactLink,
    RunKernel,
    RunRequest,
    RunStatus,
    StrategyRunResult,
    SubmitResult,
    StructuredRuntimeLog,
)
from kairospy.runtime.application import FunctionProbe, KairosApplication, RuntimeStatus
from kairospy.runtime.clock import FixedClock
from kairospy.runtime.config import ApplicationConfig, RuntimePaths
from kairospy.runtime.stop_controller import RuntimeStopController
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
from kairospy.governance.reconciliation import ReconciliationDifference, ReconciliationReport
from kairospy.identity import AccountRef, AccountType, InstitutionId
from kairospy.strategy.contracts import StrategyLifecycle, StrategySpec
from kairospy.strategy.stop_policy import StopReason
from kairospy.surface import product as product_surface


class LiveRunDaemonTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_run_daemon_persists_start_and_stop_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lifecycle: list[str] = []

            async def feed_service() -> None:
                lifecycle.append("started")
                try:
                    await asyncio.Future()
                finally:
                    lifecycle.append("stopped")

            app = _application(Path(directory), runtime_id="live-daemon")
            structured_log_path = Path(directory) / "runtime.jsonl"
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-a",
                structured_log_path=str(structured_log_path),
                clock=app.clock,
            )

            running = await daemon.start()

            self.assertEqual(running.phase, LiveRunDaemonPhase.RUNNING)
            self.assertEqual(running.application_status, RuntimeStatus.RUNNING)
            self.assertEqual(running.services[0].status, ManagedServiceStatus.RUNNING)
            persisted = app.store.runtime_state(daemon.state_key)
            assert isinstance(persisted, dict)
            self.assertEqual(persisted["phase"], LiveRunDaemonPhase.RUNNING.value)
            self.assertEqual(persisted["services"][0]["name"], "feed:live")
            self.assertEqual(persisted["snapshot_hash"], running.snapshot_hash)

            stopped = await daemon.stop()

            self.assertEqual(lifecycle, ["started", "stopped"])
            self.assertEqual(stopped.phase, LiveRunDaemonPhase.STOPPED)
            self.assertEqual(stopped.application_status, RuntimeStatus.STOPPED)
            self.assertEqual(stopped.services[0].status, ManagedServiceStatus.STOPPED)
            persisted = app.store.runtime_state(daemon.state_key)
            assert isinstance(persisted, dict)
            self.assertEqual(persisted["phase"], LiveRunDaemonPhase.STOPPED.value)
            self.assertEqual(persisted["application_status"], RuntimeStatus.STOPPED.value)
            records = StructuredRuntimeLog(structured_log_path).read()
            self.assertEqual(records[0]["event"], "daemon_start_requested")
            self.assertIn("daemon_started", [item["event"] for item in records])
            self.assertEqual(records[-1]["event"], "daemon_stopped")
            self.assertEqual(records[-1]["run_id"], "live-run-a")
            self.assertEqual(len(records[-1]["record_hash"]), 64)

    async def test_live_run_daemon_lock_rejects_duplicate_run_id_until_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            root = Path(directory)
            lock_path = root / "run.lock"
            first = LiveRunDaemon(
                _application(root / "first", runtime_id="live-run-lock"),
                (ManagedServiceSpec("feed:live:first", feed_service),),
                run_id="live-run-lock",
                run_lock_path=str(lock_path),
            )
            duplicate = LiveRunDaemon(
                _application(root / "duplicate", runtime_id="live-run-lock"),
                (ManagedServiceSpec("feed:live:duplicate", feed_service),),
                run_id="live-run-lock",
                run_lock_path=str(lock_path),
            )

            await first.start()
            with self.assertRaisesRegex(RuntimeError, "already locked"):
                await duplicate.start()

            await first.stop()
            await duplicate.start()
            await duplicate.stop()

    async def test_live_run_daemon_runs_stop_handler_before_services_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lifecycle: list[str] = []

            async def feed_service() -> None:
                try:
                    await asyncio.Future()
                finally:
                    lifecycle.append(f"service:{app.status.value}")

            def stop_handler() -> None:
                lifecycle.append(f"handler:{app.status.value}")

            app = _application(Path(directory), runtime_id="live-stop-handler")
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-stop-handler",
                stop_handler=stop_handler,
                clock=app.clock,
            )

            await daemon.start()
            await daemon.stop()

            self.assertEqual(lifecycle, ["handler:running", "service:running"])

    async def test_live_run_daemon_recover_restarts_from_stopped_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            starts: list[str] = []

            async def feed_service() -> None:
                starts.append("started")
                await asyncio.Future()

            app = _application(Path(directory), runtime_id="live-recover", recovery=_ReadyRecovery())
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-recover",
                clock=app.clock,
            )

            await daemon.start()
            await daemon.stop()
            recovered = await daemon.recover()

            self.assertEqual(starts, ["started", "started"])
            self.assertEqual(recovered.phase, LiveRunDaemonPhase.RUNNING)
            self.assertEqual(recovered.reason, "recovered")
            self.assertEqual(recovered.recovery_ready, True)
            persisted = app.store.runtime_state(daemon.state_key)
            assert isinstance(persisted, dict)
            self.assertEqual(persisted["reason"], "recovered")
            await daemon.stop()

    async def test_live_run_daemon_stop_request_is_persisted_until_actual_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            app = _application(Path(directory), runtime_id="live-stop-request")
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-stop",
                clock=app.clock,
            )
            await daemon.start()

            requested = daemon.request_stop("operator requested maintenance")

            self.assertEqual(requested.phase, LiveRunDaemonPhase.STOPPING)
            self.assertEqual(daemon.stop_requested(), True)
            persisted = app.store.runtime_state(daemon.state_key)
            assert isinstance(persisted, dict)
            self.assertEqual(persisted["stop_requested"], True)
            self.assertEqual(persisted["reason"], "operator requested maintenance")

            stopped = await daemon.stop()

            self.assertEqual(stopped.phase, LiveRunDaemonPhase.STOPPED)
            persisted = app.store.runtime_state(daemon.state_key)
            assert isinstance(persisted, dict)
            self.assertEqual(persisted["stop_requested"], False)

    async def test_live_run_foreground_daemon_consumes_durable_stop_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            app = _application(Path(directory), runtime_id="live-command-stop")
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-command-stop",
                clock=app.clock,
            )
            task = asyncio.create_task(product_surface._run_live_foreground_daemon(
                daemon,
                "start",
                duration_seconds=None,
                poll_seconds=0.01,
            ))
            await _wait_for_runtime_state(app.store, daemon.state_key, LiveRunDaemonPhase.RUNNING.value)
            submitted = OperatorCommandBus(app.store).submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.STOP,
                payload={},
                actor="cli",
                reason="operator maintenance",
                idempotency_key="stop:operator-maintenance",
                at=app.clock.now(),
            )

            result = await asyncio.wait_for(task, timeout=2)
            commands = OperatorCommandBus(app.store).commands(daemon.run_id)

            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["operator_command"]["command_id"], submitted.command_id)
            self.assertEqual(commands[-1].status, OperatorCommandStatus.SUCCEEDED)
            self.assertEqual(commands[-1].result["phase"], LiveRunDaemonPhase.STOPPED.value)
            self.assertEqual(commands[-1].result["timeout_seconds"], 5.0)
            self.assertEqual(commands[-1].result["force"], False)

    async def test_live_run_foreground_daemon_force_stops_after_timeout_and_records_incident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def stubborn_service() -> None:
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    await asyncio.Future()

            app = _application(Path(directory), runtime_id="live-command-force-stop")
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:stubborn", stubborn_service),),
                run_id="live-run-command-force-stop",
                clock=app.clock,
            )
            task = asyncio.create_task(product_surface._run_live_foreground_daemon(
                daemon,
                "start",
                duration_seconds=None,
                poll_seconds=0.01,
            ))
            await _wait_for_runtime_state(app.store, daemon.state_key, LiveRunDaemonPhase.RUNNING.value)
            submitted = OperatorCommandBus(app.store).submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.STOP,
                payload={"timeout_seconds": 0.01, "force": True},
                actor="cli",
                reason="operator emergency force stop",
                idempotency_key="stop:force",
                at=app.clock.now(),
            )

            result = await asyncio.wait_for(task, timeout=2)
            command = await _wait_for_operator_command_status(
                app.store, daemon.run_id, submitted.command_id, OperatorCommandStatus.SUCCEEDED,
            )
            incidents = app.store.runtime_incidents(daemon.run_id)

            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["force"], True)
            self.assertEqual(result["timeout_seconds"], 0.01)
            self.assertEqual(command.result["force"], True)
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0].incident_id, f"runtime-force-stop:{daemon.run_id}")
            self.assertEqual(incidents[0].severity, "critical")

    async def test_live_run_foreground_daemon_consumes_kill_switch_and_reset_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            app = _application(Path(directory), runtime_id="live-command-kill")
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-command-kill",
                clock=app.clock,
            )
            task = asyncio.create_task(product_surface._run_live_foreground_daemon(
                daemon,
                "start",
                duration_seconds=None,
                poll_seconds=0.01,
            ))
            await _wait_for_runtime_state(app.store, daemon.state_key, LiveRunDaemonPhase.RUNNING.value)
            bus = OperatorCommandBus(app.store)
            killed = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.KILL_SWITCH,
                payload={},
                actor="cli",
                reason="risk breach",
                idempotency_key="kill:risk-breach",
                at=app.clock.now(),
            )

            killed = await _wait_for_operator_command_status(app.store, daemon.run_id, killed.command_id, OperatorCommandStatus.SUCCEEDED)
            kill_state = app.store.runtime_state("kill_switch")
            daemon_state = app.store.runtime_state(daemon.state_key)
            app_state = app.store.runtime_state("kairospy_application")
            assert isinstance(kill_state, dict)
            assert isinstance(daemon_state, dict)
            assert isinstance(app_state, dict)
            self.assertTrue(kill_state["triggered"])
            self.assertEqual(daemon_state["phase"], LiveRunDaemonPhase.REDUCE_ONLY.value)
            self.assertEqual(app_state["status"], "reduce_only")
            self.assertEqual(killed.result["desired_state"], "reduce_only")

            reset = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.RESET_KILL_SWITCH,
                payload={"reconciliation_evidence": "reconciliation:matched"},
                actor="cli",
                reason="reconciled",
                idempotency_key="reset:reconciled",
                at=app.clock.now(),
            )

            reset = await _wait_for_operator_command_status(app.store, daemon.run_id, reset.command_id, OperatorCommandStatus.SUCCEEDED)
            reset_state = app.store.runtime_state("kill_switch")
            daemon_state = app.store.runtime_state(daemon.state_key)
            app_state = app.store.runtime_state("kairospy_application")
            assert isinstance(reset_state, dict)
            assert isinstance(daemon_state, dict)
            assert isinstance(app_state, dict)
            self.assertFalse(reset_state["triggered"])
            self.assertEqual(reset_state["reset_evidence"]["reconciliation_evidence"], "reconciliation:matched")
            self.assertEqual(daemon_state["phase"], LiveRunDaemonPhase.RUNNING.value)
            self.assertEqual(app_state["status"], "running")

            stop = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.STOP,
                payload={},
                actor="cli",
                reason="done",
                idempotency_key="stop:done",
                at=app.clock.now(),
            )
            result = await asyncio.wait_for(task, timeout=2)
            stop = await _wait_for_operator_command_status(app.store, daemon.run_id, stop.command_id, OperatorCommandStatus.SUCCEEDED)
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(stop.result["phase"], LiveRunDaemonPhase.STOPPED.value)

    async def test_live_run_foreground_daemon_consumes_operational_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            app = _application(Path(directory), runtime_id="live-command-ops")
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-command-ops",
                clock=app.clock,
            )
            task = asyncio.create_task(product_surface._run_live_foreground_daemon(
                daemon,
                "start",
                duration_seconds=None,
                poll_seconds=0.01,
            ))
            await _wait_for_runtime_state(app.store, daemon.state_key, LiveRunDaemonPhase.RUNNING.value)
            bus = OperatorCommandBus(app.store)

            status_snapshot = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.REQUEST_STATUS_SNAPSHOT,
                payload={},
                actor="cli",
                reason="fresh status",
                idempotency_key="status:fresh",
                at=app.clock.now(),
            )
            status_snapshot = await _wait_for_operator_command_status(
                app.store, daemon.run_id, status_snapshot.command_id, OperatorCommandStatus.SUCCEEDED,
            )
            snapshot_state = app.store.runtime_state(f"status_snapshot:{daemon.run_id}:last")
            assert isinstance(snapshot_state, dict)
            self.assertEqual(snapshot_state["phase"], LiveRunDaemonPhase.RUNNING.value)
            self.assertEqual(status_snapshot.result["phase"], LiveRunDaemonPhase.RUNNING.value)

            pause = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.PAUSE_NEW_ORDERS,
                payload={},
                actor="cli",
                reason="feed stale",
                idempotency_key="pause:feed-stale",
                at=app.clock.now(),
            )
            pause = await _wait_for_operator_command_status(app.store, daemon.run_id, pause.command_id, OperatorCommandStatus.SUCCEEDED)
            risk_state = app.store.runtime_state("risk_runtime:last")
            assert isinstance(risk_state, dict)
            self.assertEqual(risk_state["status"], "paused")
            self.assertEqual(pause.result["desired_state"], "paused")

            resume = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.RESUME,
                payload={},
                actor="cli",
                reason="feed recovered",
                idempotency_key="resume:feed-recovered",
                at=app.clock.now(),
            )
            resume = await _wait_for_operator_command_status(app.store, daemon.run_id, resume.command_id, OperatorCommandStatus.SUCCEEDED)
            risk_state = app.store.runtime_state("risk_runtime:last")
            assert isinstance(risk_state, dict)
            self.assertEqual(risk_state["status"], "ok")
            self.assertEqual(resume.result["desired_state"], "running")

            reduce_only = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.SET_REDUCE_ONLY,
                payload={},
                actor="cli",
                reason="risk review",
                idempotency_key="reduce:risk-review",
                at=app.clock.now(),
            )
            reduce_only = await _wait_for_operator_command_status(app.store, daemon.run_id, reduce_only.command_id, OperatorCommandStatus.SUCCEEDED)
            app_state = app.store.runtime_state("kairospy_application")
            assert isinstance(app_state, dict)
            self.assertEqual(app_state["status"], "reduce_only")
            self.assertEqual(reduce_only.result["desired_state"], "reduce_only")

            clear = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.CLEAR_REDUCE_ONLY,
                payload={},
                actor="cli",
                reason="approved",
                idempotency_key="clear-reduce:approved",
                at=app.clock.now(),
            )
            clear = await _wait_for_operator_command_status(app.store, daemon.run_id, clear.command_id, OperatorCommandStatus.SUCCEEDED)
            app_state = app.store.runtime_state("kairospy_application")
            assert isinstance(app_state, dict)
            self.assertEqual(app_state["status"], "running")
            self.assertEqual(clear.result["desired_state"], "running")

            cancel_all = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.CANCEL_ALL,
                payload={},
                actor="cli",
                reason="venue incident",
                idempotency_key="cancel-all:incident",
                at=app.clock.now(),
            )
            cancel_all = await _wait_for_operator_command_status(app.store, daemon.run_id, cancel_all.command_id, OperatorCommandStatus.SUCCEEDED)
            cancel_state = app.store.runtime_state(f"cancel_all:{daemon.run_id}:last")
            assert isinstance(cancel_state, dict)
            self.assertEqual(cancel_state["status"], "requested")
            self.assertEqual(cancel_all.result["desired_state"], "reduce_only")

            reconciliation = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.REQUEST_RECONCILIATION,
                payload={},
                actor="cli",
                reason="fresh reconcile",
                idempotency_key="reconcile:fresh",
                at=app.clock.now(),
            )
            reconciliation = await _wait_for_operator_command_status(app.store, daemon.run_id, reconciliation.command_id, OperatorCommandStatus.SUCCEEDED)
            reconciliation_state = app.store.runtime_state(f"reconciliation_request:{daemon.run_id}:last")
            assert isinstance(reconciliation_state, dict)
            self.assertEqual(reconciliation_state["status"], "requested")
            self.assertTrue(reconciliation.result["reconciliation_requested"])

            target = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.TARGET_POSITION,
                payload={
                    "intent_id": "manual-pair-1",
                    "legs": (
                        {
                            "venue": "binance",
                            "product": "spot",
                            "instrument": "BTCUSDT",
                            "side": "long",
                            "quantity": "0.001",
                        },
                        {
                            "venue": "hyperliquid",
                            "product": "perpetual",
                            "instrument": "BTC",
                            "side": "short",
                            "quantity": "0.001",
                        },
                    ),
                },
                actor="cli",
                reason="manual pair target",
                idempotency_key="target-position:manual-pair-1",
                at=app.clock.now(),
            )
            target = await _wait_for_operator_command_status(
                app.store,
                daemon.run_id,
                target.command_id,
                OperatorCommandStatus.SUCCEEDED,
            )
            target_state = app.store.runtime_state(f"target_position:{daemon.run_id}:last")
            assert isinstance(target_state, dict)
            self.assertEqual(target_state["status"], "accepted")
            self.assertEqual(target_state["intent_id"], "manual-pair-1")
            self.assertEqual(len(target_state["legs"]), 2)
            self.assertEqual(target.result["execution_status"], "not_submitted")

            reload_risk = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.RELOAD_RISK_LIMITS,
                payload={"risk_limits_hash": "risk-limits-hash"},
                actor="cli",
                reason="limits approved",
                idempotency_key="risk:reload",
                at=app.clock.now(),
            )

            reload_risk = await _wait_for_operator_command_status(
                app.store,
                daemon.run_id,
                reload_risk.command_id,
                OperatorCommandStatus.SUCCEEDED,
            )
            risk_state = app.store.runtime_state("risk_runtime:last")
            assert isinstance(risk_state, dict)
            self.assertEqual(risk_state["status"], "ok")
            self.assertEqual(risk_state["limits_hash"], "risk-limits-hash")
            self.assertEqual(reload_risk.result["risk_limits_hash"], "risk-limits-hash")

            stop = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.STOP,
                payload={},
                actor="cli",
                reason="done",
                idempotency_key="stop:ops-done",
                at=app.clock.now(),
            )
            result = await asyncio.wait_for(task, timeout=2)
            stop = await _wait_for_operator_command_status(app.store, daemon.run_id, stop.command_id, OperatorCommandStatus.SUCCEEDED)
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(stop.result["phase"], LiveRunDaemonPhase.STOPPED.value)

    async def test_live_run_foreground_daemon_uses_operator_handler_for_cancel_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            app = _application(Path(directory), runtime_id="live-command-cancel-handler")

            def operator_handler(command):
                if OperatorCommandType(command.command_type) is not OperatorCommandType.CANCEL_ALL:
                    return None
                app.store.set_runtime_state(
                    "cancel_all:live-run-command-cancel-handler:last",
                    {
                        "run_id": "live-run-command-cancel-handler",
                        "status": "succeeded",
                        "cancelled_client_order_ids": ("client-1", "client-2"),
                        "failures": (),
                    },
                    app.clock.now(),
                )
                snapshot = daemon.mark_reduce_only(str(command.reason))
                return {
                    "phase": snapshot.phase.value,
                    "desired_state": "reduce_only",
                    "cancel_all_requested": True,
                    "cancelled_orders": ("client-1", "client-2"),
                    "failures": (),
                }

            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-command-cancel-handler",
                operator_command_handler=operator_handler,
                clock=app.clock,
            )
            task = asyncio.create_task(product_surface._run_live_foreground_daemon(
                daemon,
                "start",
                duration_seconds=None,
                poll_seconds=0.01,
            ))
            await _wait_for_runtime_state(app.store, daemon.state_key, LiveRunDaemonPhase.RUNNING.value)
            bus = OperatorCommandBus(app.store)
            cancel_all = bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.CANCEL_ALL,
                payload={},
                actor="cli",
                reason="venue incident",
                idempotency_key="cancel-all:handler",
                at=app.clock.now(),
            )

            cancel_all = await _wait_for_operator_command_status(
                app.store, daemon.run_id, cancel_all.command_id, OperatorCommandStatus.SUCCEEDED,
            )
            cancel_state = app.store.runtime_state(f"cancel_all:{daemon.run_id}:last")
            assert isinstance(cancel_state, dict)
            self.assertEqual(cancel_state["status"], "succeeded")
            self.assertEqual(cancel_all.result["cancelled_orders"], ["client-1", "client-2"])

            bus.submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.STOP,
                payload={},
                actor="cli",
                reason="done",
                idempotency_key="stop:cancel-handler",
                at=app.clock.now(),
            )
            result = await asyncio.wait_for(task, timeout=2)
            self.assertEqual(result["status"], "stopped")

    async def test_live_run_reconciliation_command_persists_mismatch_and_enters_reduce_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            account = AccountRef(InstitutionId("binance"), "spot-main", AccountType.CRYPTO_SPOT)
            app = _application(Path(directory), runtime_id="live-command-reconcile-handler")
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-command-reconcile-handler",
                clock=app.clock,
            )
            await daemon.start()
            command = OperatorCommandBus(app.store).submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.REQUEST_RECONCILIATION,
                payload={},
                actor="cli",
                reason="operator fresh reconcile",
                idempotency_key="reconcile:handler",
                at=app.clock.now(),
            )

            result = product_surface._run_live_apply_reconciliation_command(
                daemon,
                command,
                {account: _MismatchedReconciliationService(app.clock.now())},
            )

            request_state = app.store.runtime_state(f"reconciliation_request:{daemon.run_id}:last")
            account_state = app.store.runtime_state(f"reconciliation:{daemon.run_id}:{account.value}")
            app_state = app.store.runtime_state("kairospy_application")
            daemon_state = app.store.runtime_state(daemon.state_key)
            assert isinstance(request_state, dict)
            assert isinstance(account_state, dict)
            assert isinstance(app_state, dict)
            assert isinstance(daemon_state, dict)
            self.assertEqual(result["desired_state"], "reduce_only")
            self.assertEqual(result["matched"], False)
            self.assertEqual(result["mismatched_accounts"], (account.value,))
            self.assertEqual(request_state["status"], "mismatched")
            self.assertEqual(account_state["phase"], "mismatched")
            self.assertEqual(app_state["status"], "reduce_only")
            self.assertEqual(daemon_state["phase"], LiveRunDaemonPhase.REDUCE_ONLY.value)

            await daemon.stop()

    async def test_live_run_reconciliation_command_surfaces_unknown_external_open_orders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            account = AccountRef(InstitutionId("binance"), "spot-main", AccountType.CRYPTO_SPOT)
            app = _application(Path(directory), runtime_id="live-command-external-open-order")
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-command-external-open-order",
                clock=app.clock,
            )
            await daemon.start()
            command = OperatorCommandBus(app.store).submit(
                run_id=daemon.run_id,
                command_type=OperatorCommandType.REQUEST_RECONCILIATION,
                payload={},
                actor="cli",
                reason="operator fresh reconcile",
                idempotency_key="reconcile:external-open-order",
                at=app.clock.now(),
            )

            result = product_surface._run_live_apply_reconciliation_command(
                daemon,
                command,
                {account: _ExternalOpenOrderReconciliationService(app.clock.now())},
            )

            request_state = app.store.runtime_state(f"reconciliation_request:{daemon.run_id}:last")
            account_state = app.store.runtime_state(f"reconciliation:{daemon.run_id}:{account.value}")
            assert isinstance(request_state, dict)
            assert isinstance(account_state, dict)
            self.assertEqual(result["desired_state"], "reduce_only")
            self.assertEqual(result["unknown_external_open_order_ids"], ("venue-order-external-1",))
            self.assertEqual(request_state["unknown_external_open_order_count"], 1)
            self.assertEqual(tuple(account_state["unknown_external_open_order_ids"]), ("venue-order-external-1",))

            await daemon.stop()

    async def test_live_run_daemon_records_critical_fault_as_reduce_only_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trip = asyncio.Event()

            async def feed_service() -> None:
                await trip.wait()
                raise RuntimeError("feed disconnected")

            app = _application(Path(directory), runtime_id="live-fault")
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-fault",
                clock=app.clock,
            )
            await daemon.start()

            trip.set()
            fault, snapshot = await daemon.wait_for_critical_fault()

            self.assertEqual(fault.task_name, "feed:live")
            self.assertEqual(snapshot.phase, LiveRunDaemonPhase.REDUCE_ONLY)
            self.assertEqual(snapshot.application_status, RuntimeStatus.REDUCE_ONLY)
            self.assertEqual(snapshot.services[0].status, ManagedServiceStatus.FAILED)
            persisted = app.store.runtime_state(daemon.state_key)
            assert isinstance(persisted, dict)
            self.assertEqual(persisted["phase"], LiveRunDaemonPhase.REDUCE_ONLY.value)
            self.assertIn("feed disconnected", persisted["reason"])
            await daemon.stop()

    async def test_live_run_foreground_daemon_fails_closed_when_account_lock_is_lost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            root = Path(directory)
            paths = RuntimePaths.under(root)
            store = SQLiteRuntimeStore(paths.runtime_database)
            clock = FixedClock(datetime(2026, 7, 22, 12, tzinfo=timezone.utc))
            account = AccountRef(InstitutionId("binance"), "spot-main", AccountType.CRYPTO_SPOT)
            app = KairosApplication(
                ApplicationConfig(Environment.LIVE, paths, account_lock_lease_seconds=1),
                store,
                runtime_id="runtime-a",
                accounts=(account,),
                probes=(FunctionProbe("live-profile", lambda: (True, "ready")),),
                recovery=_ReadyRecovery(),
                clock=clock,
            )
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
                run_id="live-run-lock-conflict",
                clock=clock,
            )
            task = asyncio.create_task(product_surface._run_live_foreground_daemon(
                daemon,
                "start",
                duration_seconds=None,
                poll_seconds=0.01,
            ))
            await _wait_for_runtime_state(store, daemon.state_key, LiveRunDaemonPhase.RUNNING.value)
            clock.set(clock.now() + timedelta(seconds=2))
            store.acquire_account_lock(account, "runtime-b", clock.now(), lease_seconds=30)

            result = await asyncio.wait_for(task, timeout=2)
            persisted = store.runtime_state(daemon.state_key)
            heartbeat = LiveRunRegistry(store).status(
                daemon.run_id,
                at=clock.now(),
                stale_after_seconds=5.0,
            )

            assert isinstance(persisted, dict)
            assert heartbeat is not None
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["phase"], LiveRunDaemonPhase.FAILED.value)
            self.assertEqual(persisted["phase"], LiveRunDaemonPhase.FAILED.value)
            self.assertIn("account lock", persisted["reason"])
            self.assertEqual(heartbeat["status"], LiveRunDaemonPhase.FAILED.value)
            self.assertEqual(heartbeat["desired_state"], "stopping")

    async def test_live_run_daemon_keeps_multiple_live_runs_in_distinct_state_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            async def feed_service() -> None:
                await asyncio.Future()

            root = Path(directory)
            app_a = _application(root / "a", runtime_id="runtime-a")
            app_b = _application(root / "b", runtime_id="runtime-b")
            run_a = LiveRunDaemon(
                app_a,
                (ManagedServiceSpec("feed:a", feed_service),),
                run_id="live-run-a",
                clock=app_a.clock,
            )
            run_b = LiveRunDaemon(
                app_b,
                (ManagedServiceSpec("feed:b", feed_service),),
                run_id="live-run-b",
                clock=app_b.clock,
            )

            await run_a.start()
            await run_b.start()

            self.assertNotEqual(run_a.state_key, run_b.state_key)
            persisted_a = app_a.store.runtime_state(run_a.state_key)
            persisted_b = app_b.store.runtime_state(run_b.state_key)
            assert isinstance(persisted_a, dict)
            assert isinstance(persisted_b, dict)
            self.assertEqual(persisted_a["run_id"], "live-run-a")
            self.assertEqual(persisted_b["run_id"], "live-run-b")

            await run_a.stop()
            await run_b.stop()

    async def test_live_run_kernel_service_runs_kernel_and_persists_artifact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = _application(root, runtime_id="live-kernel-service")
            profile = _LiveMemoryProfile()
            request = _live_run_request("live-strategy-run", profile.profile_id)
            strategy_calls: list[str] = []

            def strategy_runner(prepared: PreparedRun) -> StrategyRunResult:
                strategy_calls.append(prepared.request.run_id)
                return _empty_strategy_result()

            def artifact_writer(
                prepared: PreparedRun,
                _strategy_result: StrategyRunResult,
                _profile_result: ProfileResult,
            ) -> RunArtifactLink:
                return RunArtifactLink(
                    "governance-live-artifact",
                    (f"governance/{prepared.request.run_id}/manifest.json",),
                )

            service = LiveRunKernelService(
                app.store,
                RunKernel(profile),
                request,
                strategy_runner,
                artifact_writer=artifact_writer,
                clock=app.clock,
            )
            daemon = LiveRunDaemon(
                app,
                (service.managed_service(),),
                run_id=request.run_id,
                clock=app.clock,
            )

            await daemon.start()
            state = await _wait_for_runtime_state(app.store, service.state_key, "completed")

            self.assertEqual(strategy_calls, ["live-strategy-run"])
            self.assertEqual(state["run_id"], "live-strategy-run")
            self.assertEqual(state["phase"], "completed")
            self.assertEqual(state["run_result"]["status"], RunStatus.SUCCEEDED.value)
            self.assertEqual(state["artifact_hash"], "governance-live-artifact")
            self.assertEqual(
                state["artifact_refs"],
                ["profile:live-strategy-run", "governance/live-strategy-run/manifest.json"],
            )
            self.assertEqual(len(state["result_hash"]), 64)
            self.assertEqual(len(state["state_hash"]), 64)

            await daemon.stop()
            stopped = app.store.runtime_state(service.state_key)
            assert isinstance(stopped, dict)
            self.assertEqual(stopped["phase"], "stopped")
            self.assertEqual(stopped["artifact_hash"], "governance-live-artifact")

    async def test_live_run_kernel_service_rejects_non_live_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = _application(Path(directory), runtime_id="live-kernel-service")
            profile = _LiveMemoryProfile()
            request = RunRequest(
                "paper-run",
                RunMode.PAPER_TRADING,
                profile.profile_id,
                "workspace-hash",
                "data-binding-hash",
                "strategy",
                "v1",
                "strategy-hash",
                "config-hash",
                datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
            )

            with self.assertRaisesRegex(ValueError, "live RunRequest"):
                LiveRunKernelService(app.store, RunKernel(profile), request, _empty_strategy_result)


class LiveRunSurfaceTests(unittest.TestCase):
    def test_run_live_surface_starts_configured_live_kernel_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_live_project_config(root)
            (root / "strategies.py").write_text(
                "\n".join([
                    "def workspace(context, params=None):",
                    "    return context.project()",
                    "",
                    "class Strategy:",
                    "    strategy_id = 'noop-live-kernel-test'",
                    "",
                    "    def on_start(self, context):",
                    "        return ()",
                    "",
                    "    def on_market(self, context):",
                    "        return ()",
                    "",
                    "    def on_fill(self, fill, context):",
                    "        return ()",
                    "",
                    "    def on_end(self, context):",
                    "        return ()",
                    "",
                    "def build(context, params=None):",
                    "    return Strategy()",
                ]) + "\n",
                encoding="utf-8",
            )
            from kairospy.workspace import WorkspaceRepository

            WorkspaceRepository(root).create("strategies:workspace")
            config_path = root / "configs" / "runs" / "live.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                "\n".join([
                    "schema_version = 1",
                    "",
                    "[run]",
                    'name = "configured-live-daemon"',
                    'mode = "live"',
                    'workspace = "strategies:workspace"',
                    'strategy = "strategies:build"',
                    "",
                    "[params]",
                    'symbol = "BTC-USDT"',
                    "",
                    "[bindings]",
                    'account = "binance_live_spot"',
                    "",
                    "[live]",
                    'provider = "binance"',
                    "",
                    "[evidence]",
                    'readiness = "readiness:live"',
                    'promotion = "promotion:live"',
                ]) + "\n",
                encoding="utf-8",
            )

            with _cwd(root):
                result = product_surface.run_live(SimpleNamespace(
                    live_action="start",
                    run_id="live-config",
                    config=config_path,
                    param=("leverage=1",),
                    confirm_live=True,
                    duration_seconds=0.2,
                    poll_seconds=0.01,
                ))

            runtime_db = RuntimePaths.under(root / ".kairos" / "runtime" / "live" / "live-config").runtime_database
            store = SQLiteRuntimeStore(runtime_db)
            kernel_state = store.runtime_state("live_run_kernel:live-config")
            registry_status = LiveRunRegistry(store).status(
                "live-config",
                at=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
                stale_after_seconds=5.0,
            )
            assert isinstance(kernel_state, dict)
            assert registry_status is not None

            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["run_id"], "live-config")
            self.assertNotEqual(result["run_config"]["config_hash"], "unknown")
            self.assertEqual(registry_status["config_hash"], result["run_config"]["config_hash"])
            self.assertEqual(result["started"]["services"][0]["name"], "strategy-run:live-config")
            self.assertEqual(kernel_state["phase"], "stopped")
            self.assertEqual(kernel_state["run_result"]["run_id"], "live-config")
            self.assertEqual(kernel_state["run_result"]["mode"], "live")

    def test_run_live_surface_supervises_configured_market_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_live_project_config(root)
            (root / "strategies.py").write_text(
                "\n".join([
                    "def workspace(context, params=None):",
                    "    return context.project()",
                    "",
                    "class Strategy:",
                    "    strategy_id = 'noop-live-market-test'",
                    "",
                    "    def on_start(self, context):",
                    "        return ()",
                    "",
                    "    def on_market(self, context):",
                    "        return ()",
                    "",
                    "    def on_fill(self, fill, context):",
                    "        return ()",
                    "",
                    "    def on_end(self, context):",
                    "        return ()",
                    "",
                    "def build(context, params=None):",
                    "    return Strategy()",
                ]) + "\n",
                encoding="utf-8",
            )
            from kairospy.workspace import WorkspaceRepository

            WorkspaceRepository(root).create("strategies:workspace")
            config_path = root / "configs" / "runs" / "live.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                "\n".join([
                    "schema_version = 1",
                    "",
                    "[run]",
                    'name = "configured-live-market-daemon"',
                    'mode = "live"',
                    'workspace = "strategies:workspace"',
                    'strategy = "strategies:build"',
                    "",
                    "[bindings]",
                    'account = "binance_live_spot"',
                    'market = ["ticks"]',
                    "",
                    "[bindings.live_views.ticks]",
                    'dataset = "market.binance.btcusdt.orderbook"',
                    'live_view_id = "live:binance:btcusdt-book"',
                    "supervise_services = true",
                    "",
                    "[live]",
                    'provider = "binance"',
                    "",
                    "[evidence]",
                    'readiness = "readiness:live"',
                    'promotion = "promotion:live"',
                ]) + "\n",
                encoding="utf-8",
            )

            async def feed_service() -> None:
                await asyncio.Future()

            market_binding = SimpleNamespace(
                event_source=_EmptyEventSource(),
                managed_services=(
                    ManagedServiceSpec("feed:ticks:live:binance:btcusdt-book", feed_service),
                    ManagedServiceSpec("feed-monitor:ticks:live:binance:btcusdt-book", feed_service),
                ),
            )

            from unittest.mock import patch

            with patch("kairospy.integrations.live_ports.build_live_market_event_source", return_value=market_binding):
                with _cwd(root):
                    result = product_surface.run_live(SimpleNamespace(
                        live_action="start",
                        run_id="live-market-config",
                        config=config_path,
                        param=(),
                        confirm_live=True,
                        duration_seconds=0.05,
                        poll_seconds=0.01,
                    ))

            service_names = [item["name"] for item in result["started"]["services"]]

            self.assertEqual(result["status"], "stopped")
            self.assertCountEqual(service_names, [
                "feed:ticks:live:binance:btcusdt-book",
                "feed-monitor:ticks:live:binance:btcusdt-book",
                "strategy-run:live-market-config",
            ])

    def test_run_live_surface_supervises_provider_risk_monitor_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from unittest.mock import patch

            from kairospy.identity import VenueId
            from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway

            root = Path(directory)
            _write_live_project_config(root)
            config_toml = root / "kairos.toml"
            config_toml.write_text(
                config_toml.read_text(encoding="utf-8")
                + "\n[accounts.binance_live_spot]\n"
                + 'provider = "binance"\n'
                + 'account_ref = "binance:crypto_spot:main"\n'
                + 'allowed_products = ["crypto_spot"]\n',
                encoding="utf-8",
            )
            (root / "strategies.py").write_text(
                "\n".join([
                    "def workspace(context, params=None):",
                    "    return context.project()",
                    "",
                    "class Strategy:",
                    "    strategy_id = 'noop-live-provider-test'",
                    "",
                    "    def on_start(self, context):",
                    "        return ()",
                    "",
                    "    def on_market(self, context):",
                    "        return ()",
                    "",
                    "    def on_fill(self, fill, context):",
                    "        return ()",
                    "",
                    "    def on_end(self, context):",
                    "        return ()",
                    "",
                    "def build(context, params=None):",
                    "    return Strategy()",
                ]) + "\n",
                encoding="utf-8",
            )
            from kairospy.workspace import WorkspaceRepository

            WorkspaceRepository(root).create("strategies:workspace")
            config_path = root / "configs" / "runs" / "live.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                "\n".join([
                    "schema_version = 1",
                    "",
                    "[run]",
                    'name = "configured-live-provider-daemon"',
                    'mode = "live"',
                    'workspace = "strategies:workspace"',
                    'strategy = "strategies:build"',
                    "",
                    "[bindings]",
                    'account = "binance_live_spot"',
                    "",
                    "[live]",
                    'provider = "binance"',
                    'bind_provider = true',
                    'execution_driver = "binance-live"',
                    "",
                    "[evidence]",
                    'readiness = "readiness:live"',
                    'promotion = "promotion:live"',
                ]) + "\n",
                encoding="utf-8",
            )
            account = AccountRef(InstitutionId("binance"), "main", AccountType.CRYPTO_SPOT)
            gateway = SimulatedExecutionAccountGateway(
                VenueId("binance"),
                account,
                environment=Environment.LIVE,
                clock=FixedClock(datetime(2026, 7, 22, 12, tzinfo=timezone.utc)),
            )
            ports = SimpleNamespace(
                provider="binance",
                execution_driver="binance-live",
                account=account,
                execution_gateway=gateway,
                account_gateway=gateway,
                order_recovery_gateway=gateway,
                market_event_source=None,
                user_fill_event_source=_NeverEventSource(),
            )

            with patch("kairospy.surface.product._live_reference_catalog", return_value=object()):
                with patch("kairospy.integrations.live_ports.build_live_provider_ports", return_value=ports):
                    with _cwd(root):
                        result = product_surface.run_live(SimpleNamespace(
                            live_action="start",
                            run_id="live-provider-config",
                            config=config_path,
                            param=(),
                            confirm_live=True,
                            duration_seconds=0.05,
                            poll_seconds=0.01,
                        ))

            service_names = [item["name"] for item in result["started"]["services"]]

            self.assertEqual(result["status"], "stopped")
            self.assertIn("outbox-dispatcher:live-provider-config", service_names)
            self.assertIn("risk-monitor:live-provider-config", service_names)
            self.assertIn("fill-ingestion:live-provider-config", service_names)
            self.assertIn("account-reconciliation:binance:crypto_spot:main", service_names)

    def test_run_live_surface_uses_run_id_for_independent_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_live_project_config(root)

            async def feed_service() -> None:
                await asyncio.Future()

            service_a = ManagedServiceSpec("feed:a", feed_service)
            service_b = ManagedServiceSpec("feed:b", feed_service)
            with _cwd(root):
                first = product_surface.run_live(SimpleNamespace(
                    live_action="start",
                    run_id="live-a",
                    confirm_live=True,
                    duration_seconds=0,
                    poll_seconds=0.01,
                    _managed_services=(service_a,),
                ))
                second = product_surface.run_live(SimpleNamespace(
                    live_action="start",
                    run_id="live-b",
                    confirm_live=True,
                    duration_seconds=0,
                    poll_seconds=0.01,
                    _managed_services=(service_b,),
                ))
                stop = product_surface.run_live(SimpleNamespace(
                    live_action="stop",
                    run_id="live-a",
                    reason="operator maintenance",
                ))
                status_a = product_surface.run_live(SimpleNamespace(
                    live_action="status",
                    run_id="live-a",
                ))
                status_b = product_surface.run_live(SimpleNamespace(
                    live_action="status",
                    run_id="live-b",
                ))

            self.assertEqual(first["run_id"], "live-a")
            self.assertEqual(second["run_id"], "live-b")
            self.assertNotEqual(first["runtime_database"], second["runtime_database"])
            self.assertIn("/live/live-a/", first["runtime_database"])
            self.assertIn("/live/live-b/", second["runtime_database"])
            self.assertEqual(stop["status"], "stop_requested")
            self.assertEqual(status_a["stop_requested"], True)
            self.assertEqual(status_b["stop_requested"], False)
            self.assertEqual(status_a["run_id"], "live-a")
            self.assertEqual(status_b["run_id"], "live-b")

    def test_run_live_surface_returns_stop_report_when_stop_controller_is_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_live_project_config(root)

            async def feed_service() -> None:
                await asyncio.Future()

            def stop_handler_factory(application, _store, _run_id):
                return lambda: RuntimeStopController(
                    application,
                    _NoopStopCoordinator(),
                    _surface_strategy_spec(),
                    clock=application.clock,
                ).execute(StopReason.RISK_BREACH)

            with _cwd(root):
                result = product_surface.run_live(SimpleNamespace(
                    live_action="start",
                    run_id="live-stop-report",
                    confirm_live=True,
                    duration_seconds=0,
                    poll_seconds=0.01,
                    _managed_services=(ManagedServiceSpec("feed:stop-report", feed_service),),
                    _stop_handler_factory=stop_handler_factory,
                ))
                status = product_surface.run_live(SimpleNamespace(
                    live_action="status",
                    run_id="live-stop-report",
                ))

            self.assertEqual(result["status"], "stopped")
            self.assertEqual(result["stop_report"]["strategy_id"], "surface-live-strategy")
            self.assertEqual(result["stop_report"]["action"], "reduce_only")
            self.assertTrue(result["stop_report"]["reduce_only_applied"])
            self.assertEqual(status["stop_report"]["reason"], "risk_breach")


class _ReadyRecovery:
    def recover(self, at: datetime):
        return SimpleNamespace(ready=True, reason="ready", recovered_at=at)


class _LiveMemoryProfile:
    profile_id = "live-memory"
    mode = RunMode.LIVE
    profile_hash = "live-memory-profile-hash"

    def manifest(self):
        return {"profile_id": self.profile_id, "mode": self.mode.value}

    def prepare(self, request: RunRequest) -> PreparedRun:
        return PreparedRun(
            request,
            self.profile_id,
            self.mode,
            "live-market",
            "live-execution",
            "durable-store",
            "readiness-hash",
            "live-recovery",
            "governance-artifact",
            self.profile_hash,
        )

    def market_events(self, prepared: PreparedRun):
        return ()

    def execution_events(self, prepared: PreparedRun):
        return ()

    def submit(self, commands) -> SubmitResult:
        return SubmitResult()

    def recover(self, prepared: PreparedRun) -> RecoveryResult:
        return RecoveryResult(False, True)

    def finalize(self, prepared: PreparedRun) -> ProfileResult:
        return ProfileResult(
            RunStatus.SUCCEEDED,
            artifact_refs=(f"profile:{prepared.request.run_id}",),
            artifact_hash="profile-artifact-hash",
        )


class _NoopStopCoordinator:
    def cancel_strategy_orders(self, strategy_id, account, reason):
        return SimpleNamespace(strategy_id=strategy_id, cancelled_client_order_ids=(), failures=())


class _EmptyEventSource:
    async def events(self):
        if False:
            yield None


class _NeverEventSource:
    async def events(self):
        await asyncio.Future()
        if False:
            yield None


def _surface_strategy_spec() -> StrategySpec:
    return StrategySpec(
        "surface-live-strategy",
        "1.0.0",
        StrategyLifecycle.DRAFT,
        (ProductType.CRYPTO_SPOT,),
        ("live",),
        ("momentum",),
        ("price",),
        (("instrument", "BTC"),),
        ("price",),
        (("threshold", "0"),),
        (("target", "position"),),
        ("enter",),
        ("exit",),
        ("manual",),
        Decimal("0.01"),
        ("live_market",),
        ("limit_orders",),
        "evidence-hash",
    )


def _application(
    root: Path,
    *,
    runtime_id: str,
    recovery: object | None = None,
) -> KairosApplication:
    at = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    paths = RuntimePaths.under(root)
    return KairosApplication(
        ApplicationConfig(Environment.LIVE, paths),
        SQLiteRuntimeStore(paths.runtime_database),
        runtime_id=runtime_id,
        probes=(FunctionProbe("live-profile", lambda: (True, "ready")),),
        recovery=recovery,
        clock=FixedClock(at),
    )


def _live_run_request(run_id: str, profile_id: str) -> RunRequest:
    return RunRequest(
        run_id,
        RunMode.LIVE,
        profile_id,
        "workspace-hash",
        "data-binding-hash",
        "strategy",
        "v1",
        "strategy-hash",
        "config-hash",
        datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
    )


def _empty_strategy_result() -> StrategyRunResult:
    return StrategyRunResult((), (), (), (), "factor-hash", "decision-hash", "intent-hash", "audit-hash")


class _MismatchedReconciliationService:
    def __init__(self, checked_at: datetime) -> None:
        self.checked_at = checked_at

    def reconcile(self, account: AccountRef) -> ReconciliationReport:
        return ReconciliationReport(
            account,
            self.checked_at,
            (ReconciliationDifference("balance", "USDT", Decimal("1"), Decimal("2")),),
        )


class _ExternalOpenOrderReconciliationService:
    def __init__(self, checked_at: datetime) -> None:
        self.checked_at = checked_at

    def reconcile(self, account: AccountRef) -> ReconciliationReport:
        return ReconciliationReport(
            account,
            self.checked_at,
            (ReconciliationDifference("open_order", "venue-order-external-1", Decimal("0"), Decimal("1")),),
        )


async def _wait_for_runtime_state(store: object, key: str, phase: str) -> dict[str, object]:
    for _ in range(100):
        state = store.runtime_state(key)
        if isinstance(state, dict) and state.get("phase") == phase:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"runtime state {key!r} did not reach phase {phase!r}")


async def _wait_for_operator_command_status(
    store: object,
    run_id: str,
    command_id: str,
    status: OperatorCommandStatus,
):
    for _ in range(100):
        for command in OperatorCommandBus(store).commands(run_id):
            if command.command_id == command_id and command.status is status:
                return command
        await asyncio.sleep(0.01)
    raise AssertionError(f"operator command {command_id!r} did not reach {status.value!r}")


def _write_live_project_config(root: Path) -> None:
    (root / "kairos.toml").write_text(
        "\n".join([
            "[project]",
            'name = "live-daemon-surface"',
            "",
            "[execution]",
            "live_trading_enabled = true",
        ]) + "\n",
        encoding="utf-8",
    )


class _cwd:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.previous = Path.cwd()

    def __enter__(self):
        import os

        os.chdir(self.path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        import os

        os.chdir(self.previous)


if __name__ == "__main__":
    unittest.main()
