"""Workspace-wide, read-only System observation use case."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Any, Mapping

from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.launch.application import (
    LaunchRegistryApplication,
    WorkspaceComponentDependencyApplication,
)
from kairospy.system.domain.workspace import Workspace


ACTIVE_INSTANCE_STATES = frozenset(
    {"starting", "ready", "running", "degraded", "unresponsive", "stopping"}
)


@dataclass(frozen=True, slots=True)
class ObserveSnapshot:
    """Read-only current runtime topology for one project."""

    workspace_id: str
    shared_services: Mapping[str, Mapping[str, Any]]
    active_instances: tuple[Mapping[str, Any], ...] = ()
    support_processes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    error: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def overall_status(self) -> str:
        instance_states = {
            str(value.get("state", "unknown")) for value in self.active_instances
        }
        if instance_states.intersection({"unresponsive", "degraded"}):
            return "degraded"
        if not self.shared_services:
            return "degraded" if self.error else "partial"
        statuses = {
            str(value.get("status", "unknown"))
            for value in self.shared_services.values()
        }
        if statuses.intersection({"failed", "unresponsive", "unhealthy", "stale"}):
            return "degraded"
        if statuses.intersection({"recovering", "not_running", "unknown"}):
            return "partial"
        return "healthy"


@dataclass(frozen=True, slots=True)
class SystemObserveApplication:
    """Aggregate System-owned process facts and Launch registry facts."""

    workspace: Workspace

    def read(self) -> ObserveSnapshot:
        shared_services = ComponentProcessApplication(self.workspace).list_status()
        desired = _read_mapping(
            self.workspace.paths.run / "supervisor" / "desired.json"
        )
        dependency_application = WorkspaceComponentDependencyApplication(self.workspace)
        enriched: dict[str, Mapping[str, Any]] = {}
        for name, value in shared_services.items():
            try:
                dependents = dependency_application.active(name)
            except Exception:
                dependents = ()
            enriched[name] = {
                **value,
                "desired": name in desired,
                "operating_mode": _operating_mode(value, name in desired),
                "dependents": dependents,
            }
        return ObserveSnapshot(
            workspace_id=self.workspace.workspace_id,
            shared_services=enriched,
            active_instances=tuple(
                entry
                for entry in LaunchRegistryApplication(self.workspace).list()
                if str(entry.get("state") or "unknown") in ACTIVE_INSTANCE_STATES
            ),
            support_processes={
                name: _support_process_status(self.workspace, name)
                for name in ("system-supervisor", "aeron")
            },
        )


def _read_mapping(path: Any) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _pid_is_alive(value: object) -> bool:
    try:
        pid = int(str(value))
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _support_process_status(workspace: Workspace, name: str) -> Mapping[str, Any]:
    health_file = workspace.paths.health_file(name)
    health = _read_mapping(health_file)
    pid = health.get("pid")
    if pid is None:
        lock = workspace.paths.process_lock(name)
        try:
            pid = int(lock.read_text(encoding="utf-8").strip())
        except (OSError, TypeError, ValueError):
            pid = None
    alive = _pid_is_alive(pid)
    return {
        "component": name,
        "status": "running" if alive else "not_running",
        "pid": pid,
        "pid_alive": alive,
        "health_file": str(health_file),
        "logs_available": (workspace.paths.logs / name / "process.log").is_file(),
    }


def _operating_mode(value: Mapping[str, Any], desired: bool) -> str:
    status = str(value.get("status") or "unknown")
    if status == "recovering":
        return "recovering"
    if status in {"failed", "manual_reconcile_required"} and desired:
        return "recovery_paused"
    if status in {"ok", "ready", "running", "degraded"}:
        return "continuous" if desired else "on_demand"
    return "stopped"


__all__ = ["ACTIVE_INSTANCE_STATES", "ObserveSnapshot", "SystemObserveApplication"]
