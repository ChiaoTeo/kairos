from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from inspect import isawaitable, signature
import json

from kairospy.data.contracts import RunMode
from kairospy.infrastructure.storage.codec import to_primitive

from .application import KairosApplication, RuntimeStatus
from .async_runtime import AsyncKairosRuntime
from .clock import Clock, SystemClock
from .kernel import (
    PreparedRun,
    RunArtifactWriter,
    RunKernel,
    RunRequest,
    RunResult,
    StrategyRunResult,
)
from .control import OperatorCommandBus, OperatorCommandRecord, OperatorCommandType
from .live_lock import LiveRunFileLock
from .live_registry import LiveRunProcessIdentity, LiveRunRegistry
from .service_supervisor import AsyncServiceSupervisor, ManagedServiceSnapshot, ManagedServiceSpec, ServiceFault
from .structured_log import StructuredRuntimeLog


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
    run_id: str
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
            "run_id": self.run_id,
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
        run_id: str,
        stop_handler: Callable[..., object] | None = None,
        operator_command_handler: Callable[[OperatorCommandRecord], dict[str, object] | None] | None = None,
        process_config_hash: str = "unknown",
        process_version: str | None = None,
        run_lock_path: str | None = None,
        structured_log_path: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.application = application
        self.managed_services = tuple(managed_services)
        self.run_id = run_id
        if not self.run_id.strip():
            raise ValueError("live run daemon requires run_id")
        self.clock = clock or getattr(application, "clock", SystemClock())
        self.stop_handler = stop_handler
        self.operator_command_handler = operator_command_handler
        self.process_config_hash = str(process_config_hash or "unknown")
        self.process_version = process_version
        self.run_lock = LiveRunFileLock(run_lock_path) if run_lock_path is not None else None
        self.structured_log = StructuredRuntimeLog(structured_log_path) if structured_log_path is not None else None
        self._runtime: AsyncKairosRuntime | None = None
        self._process_identity: LiveRunProcessIdentity | None = None

    @property
    def state_key(self) -> str:
        return f"{self.STATE_KEY_PREFIX}:{self.run_id}"

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
        if not self.managed_services:
            raise ValueError("live run daemon start requires at least one managed service")
        identity = self._ensure_process_identity()
        self._acquire_run_lock(identity)
        self._log("daemon_start_requested", {"phase": phase.value, "service_count": len(self.managed_services)})
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
            self._release_run_lock()
            self._log("daemon_start_failed", {"error_type": type(error).__name__, "message": str(error)}, level="error")
            raise
        snapshot = self._persist(LiveRunDaemonPhase.RUNNING, success_reason)
        self._log("daemon_started", snapshot.manifest())
        return snapshot

    async def stop(self, *, timeout_seconds: float = 5.0, reason: str = "manual", force: bool = False) -> LiveRunDaemonSnapshot:
        self._log("daemon_stop_requested", {"reason": reason, "timeout_seconds": timeout_seconds, "force": force})
        self._persist(LiveRunDaemonPhase.STOPPING, "stopping", stop_requested=True)
        try:
            await self._run_stop_handler(reason)
        except Exception as error:
            self._persist(LiveRunDaemonPhase.FAILED, str(error), stop_requested=True)
            self._log("daemon_stop_handler_failed", {"error_type": type(error).__name__, "message": str(error)}, level="error")
            raise
        runtime = self._runtime
        try:
            if runtime is not None and runtime.started:
                try:
                    await runtime.stop(timeout_seconds=timeout_seconds)
                except Exception as error:
                    if not force:
                        raise
                    self._record_force_stop_incident(error, timeout_seconds, reason)
                    snapshot = self._persist(
                        LiveRunDaemonPhase.STOPPED,
                        f"force-stopped after stop timeout: {type(error).__name__}: {error}",
                    )
                    self._log("daemon_force_stopped", snapshot.manifest(), level="critical")
                    return snapshot
                snapshot = self._persist(LiveRunDaemonPhase.STOPPED, "stopped")
            else:
                if self.application.status is not RuntimeStatus.STOPPED:
                    self.application.stop()
                snapshot = self._persist(LiveRunDaemonPhase.STOPPED, "stopped")
        finally:
            self._runtime = None
            self._release_run_lock()
        self._log("daemon_stopped", snapshot.manifest())
        return snapshot

    def request_stop(
        self,
        reason: str = "operator stop requested",
        *,
        actor: str = "operator",
        timeout_seconds: float | None = None,
        force: bool = False,
    ) -> LiveRunDaemonSnapshot:
        if not reason.strip():
            raise ValueError("live run daemon stop request requires reason")
        payload: dict[str, object] = {"force": bool(force)}
        if timeout_seconds is not None:
            if timeout_seconds <= 0:
                raise ValueError("live run daemon stop timeout must be positive")
            payload["timeout_seconds"] = float(timeout_seconds)
        command = self.command_bus.submit(
            run_id=self.run_id,
            command_type=OperatorCommandType.STOP,
            payload=payload,
            actor=actor,
            reason=reason,
            idempotency_key=None,
            at=self.clock.now(),
        )
        self._log("daemon_stop_command_submitted", command.manifest())
        return self._persist(
            LiveRunDaemonPhase.STOPPING,
            reason,
            stop_requested=True,
            operator_command=command.manifest(),
        )

    def stop_requested(self) -> bool:
        state = self.application.store.runtime_state(self.state_key)
        legacy_requested = isinstance(state, dict) and bool(state.get("stop_requested"))
        return legacy_requested or bool(self.command_bus.pending(self.run_id, OperatorCommandType.STOP))

    @property
    def command_bus(self) -> OperatorCommandBus:
        return OperatorCommandBus(self.application.store)

    @property
    def live_registry(self) -> LiveRunRegistry:
        return LiveRunRegistry(self.application.store)

    @property
    def process_identity(self) -> LiveRunProcessIdentity:
        return self._ensure_process_identity()

    def heartbeat(
        self,
        *,
        phase: LiveRunDaemonPhase | str | None = None,
        desired_state: str = "running",
        reason: str | None = None,
    ) -> dict[str, object]:
        if self.application.status in {
            RuntimeStatus.READY,
            RuntimeStatus.RUNNING,
            RuntimeStatus.DEGRADED,
            RuntimeStatus.REDUCE_ONLY,
        }:
            try:
                self.application.heartbeat()
            except Exception as error:
                self._log("daemon_heartbeat_failed", {"error_type": type(error).__name__, "message": str(error)}, level="error")
                self._persist(
                    LiveRunDaemonPhase.FAILED,
                    f"runtime heartbeat failed: {type(error).__name__}: {error}",
                    stop_requested=True,
                )
                raise
        observed = str(getattr(phase, "value", phase) or self.status().phase.value)
        state = self.status().manifest()
        if reason is not None:
            state["reason"] = reason
        heartbeat = self.live_registry.heartbeat(
            self.process_identity,
            observed_state=observed,
            desired_state=desired_state,
            state=state,
            at=self.clock.now(),
        )
        self._heartbeat_run_lock(heartbeat.heartbeat_at)
        self._log("daemon_heartbeat", heartbeat.manifest())
        return heartbeat.manifest()

    def mark_reduce_only(self, reason: str) -> LiveRunDaemonSnapshot:
        snapshot = self._persist(LiveRunDaemonPhase.REDUCE_ONLY, reason)
        self._log("daemon_reduce_only", snapshot.manifest(), level="warning")
        return snapshot

    def mark_running(self, reason: str) -> LiveRunDaemonSnapshot:
        snapshot = self._persist(LiveRunDaemonPhase.RUNNING, reason)
        self._log("daemon_running", snapshot.manifest())
        return snapshot

    def reconcile_feed_services(self, reconciliation: dict[str, object]) -> dict[str, object]:
        runtime = self._runtime
        if runtime is None or not runtime.started:
            return {
                "status": "not_running",
                "reason": "live run daemon is not running",
                "services": [item.name for item in self.status().services],
            }
        targets = reconciliation.get("targets") if isinstance(reconciliation, dict) else ()
        specs = tuple(_dynamic_feed_service_spec(item) for item in targets if _dynamic_feed_target_active(item))
        snapshots = runtime.supervisor.reconcile_now((*self.managed_services, *specs))
        at = self.clock.now()
        payload = {
            "run_id": self.run_id,
            "status": "applied",
            "service_names": [item.name for item in snapshots],
            "dynamic_feed_services": [spec.name for spec in specs],
            "updated_at": at.isoformat(),
        }
        self.application.store.set_runtime_state(f"runtime_feed_services:{self.run_id}:last", payload, at)
        self._log("daemon_feed_services_reconciled", payload)
        return payload

    def claim_stop_command(self) -> OperatorCommandRecord | None:
        return self.claim_operator_command(OperatorCommandType.STOP)

    def claim_operator_command(
        self,
        *command_types: OperatorCommandType | str,
    ) -> OperatorCommandRecord | None:
        return self.command_bus.claim_next(
            run_id=self.run_id,
            claimed_by=self.application.runtime_id,
            at=self.clock.now(),
            command_types=tuple(command_types),
        )

    def complete_operator_command(
        self,
        command: OperatorCommandRecord,
        result: dict[str, object] | None = None,
    ) -> OperatorCommandRecord:
        return self.command_bus.complete(command.command_id, result or {}, self.clock.now())

    def fail_operator_command(self, command: OperatorCommandRecord, error: Exception | str) -> OperatorCommandRecord:
        return self.command_bus.fail(command.command_id, error, self.clock.now())

    async def _run_stop_handler(self, reason: str) -> None:
        if self.stop_handler is None:
            return
        try:
            handler_signature = signature(self.stop_handler)
        except (TypeError, ValueError):
            result = self.stop_handler(reason)
        else:
            accepts_positional = any(
                parameter.kind in {
                    parameter.POSITIONAL_ONLY,
                    parameter.POSITIONAL_OR_KEYWORD,
                    parameter.VAR_POSITIONAL,
                }
                for parameter in handler_signature.parameters.values()
            )
            result = self.stop_handler(reason) if accepts_positional else self.stop_handler()
        if isawaitable(result):
            await result

    async def wait_for_critical_fault(self) -> tuple[ServiceFault, LiveRunDaemonSnapshot]:
        runtime = self._runtime
        if runtime is None or not runtime.started:
            raise RuntimeError("live run daemon is not running")
        fault = await runtime.wait_for_critical_fault()
        snapshot = self._persist(
            LiveRunDaemonPhase.REDUCE_ONLY,
            f"managed service {fault.task_name} failed: {fault.error_type}: {fault.message}",
        )
        return fault, snapshot

    async def fail_closed(self, reason: str, *, timeout_seconds: float = 5.0) -> LiveRunDaemonSnapshot:
        self._log("daemon_fail_closed_requested", {"reason": reason, "timeout_seconds": timeout_seconds}, level="error")
        snapshot = self._persist(LiveRunDaemonPhase.FAILED, reason, stop_requested=True)
        runtime = self._runtime
        try:
            if runtime is not None and runtime.started:
                await runtime.supervisor.stop(timeout_seconds=timeout_seconds)
        finally:
            try:
                if self.application.status is not RuntimeStatus.STOPPED:
                    self.application.stop()
            except Exception as error:
                reason = f"{reason}; stop cleanup failed: {type(error).__name__}: {error}"
            self._runtime = None
            self._release_run_lock()
        snapshot = self._persist(LiveRunDaemonPhase.FAILED, reason, stop_requested=True)
        self._log("daemon_failed_closed", snapshot.manifest(), level="error")
        return snapshot

    def status(self) -> LiveRunDaemonSnapshot:
        state = self.application.store.runtime_state(self.state_key)
        phase = LiveRunDaemonPhase.CREATED
        reason = "created"
        if isinstance(state, dict):
            phase = LiveRunDaemonPhase(str(state.get("phase", phase.value)))
            reason = str(state.get("reason", reason))
        if self.running and phase not in {LiveRunDaemonPhase.FAILED, LiveRunDaemonPhase.REDUCE_ONLY, LiveRunDaemonPhase.STOPPING}:
            phase = LiveRunDaemonPhase.RUNNING
        return self._snapshot(phase, reason)

    def _persist(
        self,
        phase: LiveRunDaemonPhase,
        reason: str,
        *,
        stop_requested: bool = False,
        operator_command: dict[str, object] | None = None,
    ) -> LiveRunDaemonSnapshot:
        snapshot = self._snapshot(phase, reason)
        state = {
            **snapshot.manifest(),
            "stop_requested": bool(stop_requested),
            "snapshot_hash": snapshot.snapshot_hash,
        }
        if operator_command is not None:
            state["operator_command"] = operator_command
        self.application.store.set_runtime_state(self.state_key, state, snapshot.updated_at)
        desired = "stopping" if stop_requested or phase is LiveRunDaemonPhase.STOPPING else (
            "stopped" if phase is LiveRunDaemonPhase.STOPPED else "running"
        )
        self.live_registry.heartbeat(
            self.process_identity,
            observed_state=phase.value,
            desired_state=desired,
            state=state,
            at=snapshot.updated_at,
        )
        self._heartbeat_run_lock(snapshot.updated_at)
        return snapshot

    def _snapshot(self, phase: LiveRunDaemonPhase, reason: str) -> LiveRunDaemonSnapshot:
        return LiveRunDaemonSnapshot(
            self.run_id,
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

    def _record_force_stop_incident(self, error: Exception, timeout_seconds: float, reason: str) -> None:
        store = getattr(self.application, "store", None)
        if store is None or not hasattr(store, "record_runtime_incident"):
            return
        store.record_runtime_incident(
            incident_id=f"runtime-force-stop:{self.run_id}",
            run_id=self.run_id,
            severity="critical",
            title="runtime force stop after timeout",
            details={
                "error_type": type(error).__name__,
                "message": str(error),
                "timeout_seconds": timeout_seconds,
                "reason": reason,
            },
            at=self.clock.now(),
        )

    def _log(self, event: str, payload: object, *, level: str = "info") -> None:
        if self.structured_log is None:
            return
        try:
            self.structured_log.append(
                event,
                run_id=self.run_id,
                level=level,
                payload=payload,
                at=self.clock.now(),
            )
        except Exception:
            return

    def _ensure_process_identity(self) -> LiveRunProcessIdentity:
        if self._process_identity is None:
            self._process_identity = LiveRunProcessIdentity.create(
                run_id=self.run_id,
                runtime_id=self.application.runtime_id,
                started_at=self.clock.now(),
                config_hash=self.process_config_hash,
                **({"version": self.process_version} if self.process_version is not None else {}),
            )
        return self._process_identity

    def _acquire_run_lock(self, identity: LiveRunProcessIdentity) -> None:
        if self.run_lock is None:
            return
        self.run_lock.acquire(identity, at=self.clock.now())

    def _heartbeat_run_lock(self, at: datetime) -> None:
        if self.run_lock is None:
            return
        self.run_lock.heartbeat(at=at)

    def _release_run_lock(self) -> None:
        if self.run_lock is None:
            return
        self.run_lock.release()


class LiveRunKernelService:
    """Managed service adapter that runs live strategy scheduling through RunKernel."""

    STATE_KEY_PREFIX = "live_run_kernel"

    def __init__(
        self,
        runtime_store: object,
        kernel: RunKernel,
        request: RunRequest,
        strategy_runner: Callable[[PreparedRun], StrategyRunResult],
        *,
        artifact_writer: RunArtifactWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        if request.mode is not RunMode.LIVE:
            raise ValueError("live run kernel service requires a live RunRequest")
        if not callable(strategy_runner):
            raise ValueError("live run kernel service requires strategy_runner")
        if not hasattr(runtime_store, "set_runtime_state"):
            raise ValueError("live run kernel service requires runtime store")
        self.runtime_store = runtime_store
        self.kernel = kernel
        self.request = request
        self.strategy_runner = strategy_runner
        self.artifact_writer = artifact_writer
        self.clock = clock or SystemClock()
        self._last_result: RunResult | None = None

    @property
    def state_key(self) -> str:
        return f"{self.STATE_KEY_PREFIX}:{self.request.run_id}"

    def managed_service(self, name: str | None = None) -> ManagedServiceSpec:
        return ManagedServiceSpec(name or f"strategy-run:{self.request.run_id}", self.run)

    async def run(self) -> None:
        self._persist("running", {"reason": "started"})
        try:
            result = await asyncio.to_thread(
                self.kernel.run,
                self.request,
                self.strategy_runner,
                artifact_writer=self.artifact_writer,
            )
        except asyncio.CancelledError:
            self._persist("stopped", {"reason": "cancelled before run completed"})
            raise
        except Exception as error:
            self._persist("failed", {
                "error_type": type(error).__name__,
                "message": str(error),
            })
            raise
        self._last_result = result
        self._persist("completed", self._result_evidence(result))
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self._persist("stopped", {
                "reason": "service stopped",
                **self._result_evidence(result),
            })
            raise

    def _result_evidence(self, result: RunResult) -> dict[str, object]:
        return {
            "run_result": result.manifest(),
            "result_hash": result.result_hash,
            "artifact_hash": result.artifact_hash,
            "artifact_refs": result.artifact_refs,
        }

    def _persist(self, phase: str, evidence: dict[str, object]) -> None:
        at = self.clock.now()
        state = {
            "run_id": self.request.run_id,
            "mode": self.request.mode.value,
            "profile_id": self.request.profile_id,
            "phase": phase,
            "updated_at": at,
            **evidence,
        }
        state["state_hash"] = _hash(state)
        self.runtime_store.set_runtime_state(self.state_key, state, at)


def _recovery_ready(value: object | None) -> bool | None:
    if value is None:
        return None
    return bool(getattr(value, "ready", False))


def _order_recovery_complete(value: object | None) -> bool | None:
    if value is None:
        return None
    return bool(getattr(value, "complete", False))


def _dynamic_feed_target_active(value: object) -> bool:
    return isinstance(value, dict) and str(value.get("action") or "") in {"start", "keep"} and bool(value.get("stream"))


def _dynamic_feed_service_spec(target: object) -> ManagedServiceSpec:
    assert isinstance(target, dict)
    name = "feed:dynamic:" + _service_name_segment(str(target.get("stream") or "unknown"))

    async def run() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            raise

    return ManagedServiceSpec(name, run, restart_limit=0, allow_completion=False)


def _service_name_segment(value: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_", "."} else "_" for character in value)


def _hash(value: object) -> str:
    return sha256(json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
