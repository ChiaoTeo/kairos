"""Launch dependencies on workspace-owned runtime components."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any

from kairospy.system.apps.workspace.application import Workspace
from .control import LaunchControlApplication
from .registry import LaunchRegistryApplication


@dataclass(frozen=True, slots=True)
class WorkspaceComponentDependencyApplication:
    """Protect a workspace component used by active Launch instances."""

    workspace: Workspace

    def active(self, component: str) -> tuple[dict[str, Any], ...]:
        control = LaunchControlApplication(self.workspace)
        workspace_socket = str(self.workspace.paths.process_socket(component))
        dependents: list[dict[str, Any]] = []
        for entry in LaunchRegistryApplication(self.workspace).instances():
            launch_id = str(entry.get("launch_id") or "")
            mode = str(entry.get("mode") or "paper")
            instance_id = str(entry.get("instance_id") or "")
            if not launch_id or not instance_id:
                continue
            instance = self.workspace.instance(mode, launch_id, instance_id)
            try:
                manifest = json.loads(
                    instance.component_manifest().read_text(encoding="utf-8")
                )
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                continue
            components = manifest.get("components", {})
            connection = (
                components.get(component) if isinstance(components, Mapping) else None
            )
            if not isinstance(connection, Mapping):
                continue
            if str(connection.get("socket") or "") != workspace_socket:
                continue
            status = control.status(control.target(launch_id, instance_id, mode=mode))
            if status.get("status") in {
                "not_running",
                "stopped",
                "failed",
                "completed",
            }:
                continue
            dependents.append(
                {
                    "launch_id": launch_id,
                    "mode": mode,
                    "instance_id": instance_id,
                    "status": status.get("status", "unknown"),
                    "component": component,
                    "socket": workspace_socket,
                }
            )
        return tuple(dependents)

    def require_clear(self, component: str, action: str) -> None:
        dependents = self.active(component)
        if not dependents:
            return
        details = "; ".join(
            f"{item['launch_id']} / {item['mode']} / {item['instance_id']}"
            f" ({item['status']})"
            for item in dependents
        )
        raise RuntimeError(
            f"{action} refused: {component} is used by running launches: {details}. "
            "Stop dependent launches first."
        )


__all__ = ["WorkspaceComponentDependencyApplication"]
