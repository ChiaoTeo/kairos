from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic, sleep
from typing import Callable, Mapping, Protocol

from kairospy.identity import AccountRef
from kairospy.governance.kill_switch import KillSwitch
from kairospy.governance.observability import OperationalMonitor
from kairospy.governance.reconciliation import ReconciliationReport, ReconciliationService
from kairospy.infrastructure.storage.codec import to_primitive

from .clock import Clock, SystemClock
from .application import RuntimeStatus, KairosApplication


class RuntimeBackgroundService(Protocol):
    def start(self): ...
    def backfill(self): ...
    def stop(self) -> None: ...


class RecoveryBackgroundService:
    """Adapt VenueOrderRecoveryService to the supervisor background lifecycle."""

    def __init__(self, recovery, *, clock: Clock | None = None) -> None:
        self.recovery = recovery
        self.clock = clock or SystemClock()

    def start(self):
        return self.backfill()

    def backfill(self):
        return self.recovery.recover(self.clock.now())

    def stop(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class SupervisorCycle:
    sequence: int
    checked_at: datetime
    recovery_complete: bool
    reconciliations: tuple[ReconciliationReport, ...]
    healthy: bool
    reason: str


class RuntimeSupervisor:
    """Long-running safety loop around one KairosApplication instance."""

    STATE_KEY = "runtime_supervisor"

    def __init__(
        self,
        application: KairosApplication,
        reconciliation: Mapping[AccountRef, ReconciliationService],
        kill_switch: KillSwitch,
        monitor: OperationalMonitor,
        *,
        background_services: tuple[RuntimeBackgroundService, ...] = (),
        activate: Callable[[], None] | None = None,
        clock: Clock | None = None,
    ) -> None:
        missing = set(application.accounts) - set(reconciliation)
        if missing:
            raise ValueError("supervisor reconciliation is missing runtime accounts")
        self.application = application
        self.reconciliation = dict(reconciliation)
        self.kill_switch = kill_switch
        self.monitor = monitor
        self.background_services = background_services
        self.activate = activate
        self.clock = clock or SystemClock()
        self.cycles: list[SupervisorCycle] = []
        self.started = False

    def start(self) -> None:
        if self.started:
            raise RuntimeError("runtime supervisor is already started")
        self.application.start()
        started_services = []
        try:
            for service in self.background_services:
                service.start()
                started_services.append(service)
            if self.activate is not None:
                self.activate()
            self.application.run()
            self.started = True
            self._persist("started")
        except Exception:
            for service in reversed(started_services):
                service.stop()
            self.application.stop()
            raise

    def run_cycle(self) -> SupervisorCycle:
        if not self.started or self.application.status not in {
            RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED, RuntimeStatus.REDUCE_ONLY,
        }:
            raise RuntimeError("runtime supervisor must be running")
        sequence = len(self.cycles) + 1
        at = self.clock.now()
        recovery_complete = True
        reasons = []
        for service in self.background_services:
            try:
                report = service.backfill()
                if hasattr(report, "complete") and not bool(report.complete):
                    recovery_complete = False
                    reasons.append("background recovery incomplete")
            except Exception as error:
                recovery_complete = False
                reasons.append(f"background recovery failed: {error}")
                self.monitor.disconnected("runtime-background", str(error))
        reports = tuple(
            self.reconciliation[account].reconcile(account)
            for account in sorted(self.application.accounts, key=lambda item: item.value)
        )
        mismatches = tuple(report for report in reports if not report.matched)
        if mismatches:
            reasons.append("reconciliation mismatch: " + ",".join(
                f"{report.account.value}={len(report.differences)}" for report in mismatches
            ))
        healthy = recovery_complete and not mismatches
        reason = "runtime cycle healthy" if healthy else "; ".join(reasons)
        if not healthy:
            if not self.kill_switch.triggered:
                self.kill_switch.trigger(self.application.accounts, reason)
            if self.application.status is not RuntimeStatus.REDUCE_ONLY:
                self.application.degrade(reason, reduce_only=True)
        self.application.heartbeat()
        cycle = SupervisorCycle(sequence, at, recovery_complete, reports, healthy, reason)
        self.cycles.append(cycle)
        self._persist("cycle", cycle)
        return cycle

    def run_cycles(self, count: int) -> tuple[SupervisorCycle, ...]:
        if count < 1:
            raise ValueError("supervisor cycle count must be positive")
        return tuple(self.run_cycle() for _ in range(count))

    def run_for(self, duration_seconds: float, *, interval_seconds: float = 5.0) -> tuple[SupervisorCycle, ...]:
        if duration_seconds <= 0 or interval_seconds <= 0:
            raise ValueError("supervisor duration and interval must be positive")
        deadline = monotonic() + duration_seconds
        values = []
        while monotonic() < deadline:
            values.append(self.run_cycle())
            remaining = deadline - monotonic()
            if remaining > 0:
                sleep(min(interval_seconds, remaining))
        return tuple(values)

    def stop(self) -> None:
        if not self.started:
            return
        errors = []
        for service in reversed(self.background_services):
            try:
                service.stop()
            except Exception as error:
                errors.append(str(error))
        try:
            self.application.stop()
        except Exception as error:
            errors.append(str(error))
        self.started = False
        self._persist("stopped", errors=errors)
        if errors:
            raise RuntimeError("runtime supervisor stopped with errors: " + "; ".join(errors))

    def _persist(self, event: str, cycle: SupervisorCycle | None = None, *, errors=()) -> None:
        at = self.clock.now()
        self.application.store.set_runtime_state(self.STATE_KEY, {
            "runtime_id": self.application.runtime_id,
            "event": event,
            "status": self.application.status.value,
            "cycle_count": len(self.cycles),
            "last_cycle": to_primitive(cycle) if cycle is not None else None,
            "kill_switch_triggered": self.kill_switch.triggered,
            "critical_alert_count": sum(item.severity.value == "critical" for item in self.monitor.alerts),
            "errors": list(errors),
            "updated_at": at.isoformat(),
        }, at)


def write_soak_artifact(
    supervisor: RuntimeSupervisor,
    target: str | Path,
    *,
    started_at: datetime,
    ended_at: datetime,
    target_duration_seconds: int,
    environment: str,
    restart_drill_passed: bool,
    kill_switch_drill_passed: bool,
) -> dict[str, object]:
    if started_at.tzinfo is None or ended_at.tzinfo is None or ended_at < started_at:
        raise ValueError("soak timestamps must be ordered and timezone-aware")
    if target_duration_seconds <= 0:
        raise ValueError("soak target duration must be positive")
    actual_duration = int((ended_at - started_at).total_seconds())
    critical_alerts = tuple(item for item in supervisor.monitor.alerts if item.severity.value == "critical")
    unhealthy = tuple(item for item in supervisor.cycles if not item.healthy)
    acceptance = {
        "duration_met": actual_duration >= target_duration_seconds,
        "all_cycles_healthy": not unhealthy and bool(supervisor.cycles),
        "no_critical_alerts": not critical_alerts,
        "restart_drill_passed": bool(restart_drill_passed),
        "kill_switch_drill_passed": bool(kill_switch_drill_passed),
    }
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": "runtime_l4_soak",
        "runtime_id": supervisor.application.runtime_id,
        "environment": environment,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "target_duration_seconds": target_duration_seconds,
        "actual_duration_seconds": actual_duration,
        "cycle_count": len(supervisor.cycles),
        "healthy_cycle_count": len(supervisor.cycles) - len(unhealthy),
        "critical_alerts": to_primitive(critical_alerts),
        "acceptance": acceptance,
        "passed": all(acceptance.values()),
    }
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["audit_hash"] = sha256(material.encode()).hexdigest()
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    payload["artifact"] = str(path)
    return payload
