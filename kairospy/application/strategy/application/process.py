"""Application facade for the optional Python strategy server process."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ...system import UnixRestClient
from ...workspace import Workspace


@dataclass(frozen=True, slots=True)
class StrategyProcessApplication:
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
        strategy_root: Path | None = None,
        params: dict[str, object] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> Path:
        socket = self.socket(launch_id, instance_id, mode)
        client = UnixRestClient(socket)
        try:
            health = asyncio.run(client.request("GET", "/v1/health"))
        except (OSError, RuntimeError, ValueError):
            pass
        else:
            if health.get("status") == "ready":
                if health.get("strategy_state") == "failed":
                    raise RuntimeError("strategy server is already failed; stop and recreate the instance")
                return socket

        log_dir = self.workspace.paths.logs / "launches" / mode / launch_id / instance_id
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "strategy.log"
        log = log_path.open("ab")
        command = [
            sys.executable, "-m", "kairospy.bin.strategy",
            "--workspace", str(self.workspace.paths.root),
            "--launch-id", launch_id,
            "--instance-id", instance_id,
            "--mode", mode,
            "--strategy", strategy_ref,
        ]
        if strategy_root is not None:
            command.extend(("--strategy-root", str(strategy_root)))
        if params:
            command.extend(("--params", json.dumps(params, separators=(",", ":"))))
        process_environment = os.environ.copy()
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
            except (OSError, RuntimeError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"strategy server did not become ready; inspect {log_path}")
            time.sleep(0.05)

    def stop(self, launch_id: str, instance_id: str, mode: str = "paper") -> dict[str, Any]:
        return asyncio.run(UnixRestClient(self.socket(launch_id, instance_id, mode)).request("POST", "/v1/stop"))
