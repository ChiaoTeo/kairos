"""Launch-owned controller for the optional Python Strategy process."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from kairospy.system.apps.components.application import UnixRestClient
from kairospy.system.apps.components.application.event_routes import (
    ensure_instance_event_route,
)
from kairospy.system.apps.workspace.application import Workspace


@dataclass(frozen=True, slots=True)
class StrategyProcessController:
    workspace: Workspace
    ready_timeout: float = 15.0

    def socket(self, launch_id: str, instance_id: str, mode: str = "paper") -> Path:
        return self.workspace.paths.launch_socket(mode, launch_id, instance_id)

    def ensure_running(
        self,
        strategy_ref: str,
        *,
        launch_id: str,
        instance_id: str,
        mode: str = "paper",
        params: dict[str, object] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> Path:
        socket = self.socket(launch_id, instance_id, mode)
        client = UnixRestClient(socket)
        try:
            health = asyncio.run(client.request("GET", "/v1/health"))
        except Exception:
            pass
        else:
            if health.get("status") == "ready":
                if health.get("strategy_state") == "failed":
                    raise RuntimeError(
                        "strategy server is already failed; stop and recreate the instance"
                    )
                return socket

        instance_workspace = self.workspace.instance(mode, launch_id, instance_id)
        log_path = instance_workspace.log("strategy", "process.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("ab")
        command = [
            sys.executable,
            "-m",
            "kairospy.bin.strategy",
            "--workspace",
            str(self.workspace.paths.root),
            "--launch-id",
            launch_id,
            "--instance-id",
            instance_id,
            "--mode",
            mode,
            "--strategy",
            strategy_ref,
        ]
        if params:
            command.extend(("--params", json.dumps(params, separators=(",", ":"))))
        process_environment = os.environ.copy()
        process_environment["AERON_DIR"] = str(
            ensure_instance_event_route(instance_workspace).aeron_dir
        )
        if environment is not None:
            process_environment.update(environment)
        try:
            subprocess.Popen(
                command,
                cwd=str(self.workspace.paths.root),
                env=process_environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log.close()

        deadline = time.monotonic() + self.ready_timeout
        while True:
            try:
                health = asyncio.run(client.request("GET", "/v1/health"))
                if health.get("status") == "ready":
                    return socket
            except Exception:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"strategy server did not become ready; inspect {log_path}"
                )
            time.sleep(0.05)

    def stop(
        self, launch_id: str, instance_id: str, mode: str = "paper"
    ) -> dict[str, Any]:
        socket = self.socket(launch_id, instance_id, mode)
        try:
            result = asyncio.run(UnixRestClient(socket).request("POST", "/v1/stop"))
        except (FileNotFoundError, OSError, RuntimeError, TimeoutError) as error:
            return {
                "launch_id": launch_id,
                "instance_id": instance_id,
                "status": "not_running",
                "control_error": str(error),
            }
        deadline = time.monotonic() + self.ready_timeout
        while socket.exists():
            if time.monotonic() >= deadline:
                return {
                    **result,
                    "status": "stop_failed",
                    "error": "strategy control socket remained after stop timeout",
                }
            time.sleep(0.05)
        result["status"] = "stopped"
        return result
