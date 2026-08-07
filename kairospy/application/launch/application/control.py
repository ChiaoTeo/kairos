from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...system import UnixRestClient
from ...workspace import Workspace


@dataclass(frozen=True, slots=True)
class InstanceControlTarget:
    launch_id: str
    instance_id: str
    socket_path: Path


class LaunchControlApplication:
    """CLI-facing launch control facade.

    It only talks to the instance-owned control contract. It never reads or
    mutates strategy, market, account, or execution state directly.
    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def target(
        self,
        launch_id: str,
        instance_id: str,
        *,
        mode: str = "paper",
    ) -> InstanceControlTarget:
        if not launch_id.strip() or not instance_id.strip():
            raise ValueError("launch_id and instance_id are required")
        socket = self.workspace.paths.launch_socket(mode, launch_id, instance_id)
        return InstanceControlTarget(launch_id, instance_id, socket)

    def request(self, target: InstanceControlTarget, method: str, path: str) -> dict[str, Any]:
        return asyncio.run(UnixRestClient(target.socket_path).request(method, path))

    def status(self, target: InstanceControlTarget) -> dict[str, Any]:
        if not target.socket_path.exists():
            return {
                "launch_id": target.launch_id,
                "instance_id": target.instance_id,
                "status": "not_running",
                "control_socket": str(target.socket_path),
            }
        return self.request(target, "GET", "/v1/status")

    def start(self, target: InstanceControlTarget) -> dict[str, Any]:
        return self.request(target, "POST", "/v1/start")

    def stop(self, target: InstanceControlTarget) -> dict[str, Any]:
        if not target.socket_path.exists():
            return {
                "launch_id": target.launch_id,
                "instance_id": target.instance_id,
                "status": "not_running",
            }
        return self.request(target, "POST", "/v1/stop")

    def strategy_control(self, target: InstanceControlTarget, action: str) -> dict[str, Any]:
        allowed = {"enable", "pause", "resume", "refresh"}
        if action not in allowed:
            raise ValueError(f"unsupported strategy control: {action}")
        return self.request(target, "POST", f"/v1/{action}")
