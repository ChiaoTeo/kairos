from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json

from kairospy.infrastructure.storage.codec import to_primitive

from .application import KairosApplication, RuntimeStatus
from .async_runtime import AsyncKairosRuntime
from .clock import Clock, SystemClock
from .service_supervisor import AsyncServiceSupervisor, ManagedServiceSnapshot, ManagedServiceSpec, ServiceFault


class LiveRunDaemonPhase(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RECOVERING = "recovering"
    RUNNING = "running"
    REDUCE_ONLY = "reduce_only"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LiveRunDaemonSnapshot:
    daemon_id: str
    phase: LiveRunDaemonPhase | str
    runtime_id: str
    application_status: RuntimeStatus | str
    services: tuple[ManagedServiceSnapshot, ...]
    reason: str
    recovery_ready: bool | None
    order_recovery_complete: bool | None
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase", LiveRunDaemonPhase(self.phase))
        object.__setattr__(self, "application_status", RuntimeStatus(self.application_status))

    @property
    def snapshot_hash(self) -> str:
        return _hash(self.manifest())

    def manifest(self) -> dict[str, object]:
        return {
            "daemon_id": self.daemon_id,
            "phase": self.phase.value,
            "runtime_id": self.runtime_id,
            "application_status": self.application_status.value,
            "services": self.services,
            "reason": self.reason,
            "recovery_ready": self.recovery_ready,
            "order_recovery_complete": self.order_recovery_complete,
            "updated_at": self.updated_at,
        }


class LiveRunDaemon:
    """Long-lived live run session boundary around application gates and services."""

    STATE_KEY_PREFIX = "live_run_daemon"

    def __init__(
        self,
        application: KairosApplication,
        managed_services: tuple[ManagedServiceSpec, ...],
        *,
        daemon_id: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not managed_services:
            raise ValueError("live run daemon requires at least one managed service")
        self.application = application
        self.managed_services = tuple(managed_services)
        self.daemon_id = daemon_id or application.runtime_id
        if not self.daemon_id.strip():
            raise ValueError("live run daemon requires daemon_id")
        self.clock = clock or getattr(application, "clock", SystemClock())
        self._runtime: AsyncKairosRuntime | None = None

    @property
    def state_key(self) -> str:
        return f"{self.STATE_KEY_PREFIX}:{self.daemon_id}"

    @property
    def running(self) -> bool:
        return self._runtime is not None and self._runtime.started

    async def start(self) -> LiveRunDaemonSnapshot:
        return await self._start(LiveRunDaemonPhase.STARTING, "started")

    async def recover(self) -> LiveRunDaemonSnapshot:
        return await self._start(LiveRunDaemonPhase.RECOVERING, "recovered")

    async def _start(self, phase: LiveRunDaemonPhase, success_reason: str) -> LiveRunDaemonSnapshot:
        if self.running:
            raise RuntimeError("live run daemon is already running")
        self._persist(phase, phase.value)
        runtime = AsyncKairosRuntime(
            self.application,
            self.managed_services,
            supervisor=AsyncServiceSupervisor(),
        )
        self._runtime = runtime
        try:
            await runtime.start()
        except Exception as error:
            self._runtime = None
            self._persist(LiveRunDaemonPhase.FAILED, str(error))
            raise
        return self._persist(LiveRunDaemonPhase.RUNNING, success_reason)

    async def stop(self, *, timeout_seconds: float = 5.0) -> LiveRunDaemonSnapshot:
        self._persist(LiveRunDaemonPhase.STOPPING, "stopping")
        runtime = self._runtime
        try:
            if runtime is not None and runtime.started:
                await runtime.stop(timeout_seconds=timeout_seconds)
                snapshot = self._persist(LiveRunDaemonPhase.STOPPED, "stopped")
            else:
                if self.application.status is not RuntimeStatus.STOPPED:
                    self.application.stop()
                snapshot = self._persist(LiveRunDaemonPhase.STOPPED, "stopped")
        finally:
            self._runtime = None
        return snapshot

    async def wait_for_critical_fault(self) -> tuple[ServiceFault, LiveRunDaemonSnapshot]:
        runtime = self._runtime
        if runtime is None or not runtime.started:
            raise RuntimeError("live run daemon is not running")
        fault = await runtime.wait_for_critical_fault()
        return fault, self._persist(
            LiveRunDaemonPhase.REDUCE_ONLY,
            f"managed service {fault.task_name} failed: {fault.error_type}: {fault.message}",
        )

    def status(self) -> LiveRunDaemonSnapshot:
        state = self.application.store.runtime_state(self.state_key)
        phase = LiveRunDaemonPhase.CREATED
        reason = "created"
        if isinstance(state, dict):
            phase = LiveRunDaemonPhase(str(state.get("phase", phase.value)))
            reason = str(state.get("reason", reason))
        if self.running:
            phase = LiveRunDaemonPhase.RUNNING
        return self._snapshot(phase, reason)

    def _persist(self, phase: LiveRunDaemonPhase, reason: str) -> LiveRunDaemonSnapshot:
        snapshot = self._snapshot(phase, reason)
        self.application.store.set_runtime_state(
            self.state_key,
            {**snapshot.manifest(), "snapshot_hash": snapshot.snapshot_hash},
            snapshot.updated_at,
        )
        return snapshot

    def _snapshot(self, phase: LiveRunDaemonPhase, reason: str) -> LiveRunDaemonSnapshot:
        return LiveRunDaemonSnapshot(
            self.daemon_id,
            phase,
            self.application.runtime_id,
            self.application.status,
            self._service_snapshots(),
            reason,
            _recovery_ready(self.application.recovery_result),
            _order_recovery_complete(self.application.order_recovery_report),
            self.clock.now(),
        )

    def _service_snapshots(self) -> tuple[ManagedServiceSnapshot, ...]:
        if self._runtime is None:
            return ()
        return self._runtime.service_snapshots()


def _recovery_ready(value: object | None) -> bool | None:
    if value is None:
        return None
    return bool(getattr(value, "ready", False))


def _order_recovery_complete(value: object | None) -> bool | None:
    if value is None:
        return None
    return bool(getattr(value, "complete", False))


def _hash(value: object) -> str:
    return sha256(json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
