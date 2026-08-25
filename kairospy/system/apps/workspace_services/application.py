"""One lifecycle boundary shared by CLI and human operator surfaces."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from kairospy.system.apps.components.application import (
    ComponentProcessApplication,
    SystemRuntimeSupervisor,
)
from kairospy.system.apps.launch.application import (
    WorkspaceComponentDependencyApplication,
)
from kairospy.system.domain.workspace import Workspace


@dataclass(frozen=True, slots=True)
class WorkspaceServiceApplication:
    """Control Reference and shared Market with one desired-state policy."""

    workspace: Workspace

    def start_and_keep_running(
        self,
        component: str,
        *,
        account_id: str | None = None,
        stream_startup_logs: bool = False,
    ) -> Mapping[str, Any]:
        self._require_component(component)
        processes = ComponentProcessApplication(self.workspace)
        control = processes.ensure_running(
            component,
            account_id=account_id,
            stream_startup_logs=stream_startup_logs,
        )
        supervisor = SystemRuntimeSupervisor(processes)
        supervisor.register(component, {"account_id": account_id} if account_id else {})
        supervisor.start_background()
        return control.status()

    def stop(self, component: str) -> Mapping[str, Any]:
        self._require_component(component)
        WorkspaceComponentDependencyApplication(self.workspace).require_clear(
            component, "stop"
        )
        processes = ComponentProcessApplication(self.workspace)
        SystemRuntimeSupervisor(processes).unregister(component)
        return processes.stop(component)

    def restart(
        self,
        component: str,
        *,
        account_id: str | None = None,
        stream_startup_logs: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> Mapping[str, Any]:
        self._require_component(component)
        WorkspaceComponentDependencyApplication(self.workspace).require_clear(
            component, "restart"
        )
        processes = ComponentProcessApplication(self.workspace)
        supervisor = SystemRuntimeSupervisor(processes)
        keep_running = self._is_desired(supervisor, component)
        control = processes.restart(
            component,
            account_id=account_id,
            stream_startup_logs=stream_startup_logs,
            progress=progress,
        )
        if keep_running:
            supervisor.register(
                component, {"account_id": account_id} if account_id else {}
            )
            supervisor.start_background()
        else:
            supervisor.unregister(component)
        return control.status()

    def repair(self, component: str, *, start: bool) -> Mapping[str, Any]:
        self._require_component(component)
        processes = ComponentProcessApplication(self.workspace)
        repaired = processes.repair_component(component)
        if repaired.get("status") != "repaired":
            current = processes.list_status()[component]
            if current.get("status") not in {"not_running", "stopped"} or current.get(
                "pid_alive"
            ):
                raise RuntimeError(
                    str(repaired.get("reason") or "运行资源不可安全清理")
                )
        if start:
            return self.start_and_keep_running(component)
        return processes.list_status()[component]

    @staticmethod
    def _require_component(component: str) -> None:
        if component not in {"reference", "market"}:
            raise ValueError(
                "workspace service must be reference or market; "
                "Launch owns instance components"
            )

    @staticmethod
    def _is_desired(supervisor: SystemRuntimeSupervisor, component: str) -> bool:
        try:
            value = json.loads(supervisor.desired_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(value, dict) and component in value


__all__ = ["WorkspaceServiceApplication"]
