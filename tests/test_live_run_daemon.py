from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from kairospy.integrations.ports import Environment
from kairospy.runtime import LiveRunDaemon, LiveRunDaemonPhase, ManagedServiceSpec, ManagedServiceStatus
from kairospy.runtime.application import FunctionProbe, KairosApplication, RuntimeStatus
from kairospy.runtime.clock import FixedClock
from kairospy.runtime.config import ApplicationConfig, RuntimePaths
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore


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
            daemon = LiveRunDaemon(
                app,
                (ManagedServiceSpec("feed:live", feed_service),),
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

    async def test_live_run_daemon_recover_restarts_from_stopped_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            starts: list[str] = []

            async def feed_service() -> None:
                starts.append("started")
                await asyncio.Future()

            app = _application(Path(directory), runtime_id="live-recover", recovery=_ReadyRecovery())
            daemon = LiveRunDaemon(app, (ManagedServiceSpec("feed:live", feed_service),), clock=app.clock)

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


class _ReadyRecovery:
    def recover(self, at: datetime):
        return SimpleNamespace(ready=True, reason="ready", recovered_at=at)


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


if __name__ == "__main__":
    unittest.main()
