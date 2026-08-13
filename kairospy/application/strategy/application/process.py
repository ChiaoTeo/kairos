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

        # Strategy is an instance-owned process. Keep its stdout beside the
        # other instance runtime logs so all instance resources use one
        # canonical layout.
        instance_workspace = self.workspace.instance(mode, launch_id, instance_id)
        log_path = instance_workspace.log("strategy.log")
        log_dir = log_path.parent
        log_dir.mkdir(parents=True, exist_ok=True)
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
        control_result: dict[str, Any]
        try:
            control_result = asyncio.run(
                UnixRestClient(self.socket(launch_id, instance_id, mode)).request(
                    "POST", "/v1/stop"
                )
            )
        except (FileNotFoundError, OSError, RuntimeError, TimeoutError) as error:
            control_result = {
                "launch_id": launch_id,
                "instance_id": instance_id,
                "status": "not_running",
                "control_error": str(error),
            }
        cleanup = self._release_orphaned_subscriptions(launch_id, instance_id, mode)
        if cleanup is not None:
            control_result["subscription_cleanup"] = cleanup
        return control_result

    def _release_orphaned_subscriptions(
        self, launch_id: str, instance_id: str, mode: str
    ) -> dict[str, Any] | None:
        """Reconcile Market ownership even when the Strategy process is dead."""
        instance = self.workspace.instance(mode, launch_id, instance_id)
        strategy_id = self._journal_strategy_id(instance.root / "lifecycle.jsonl")
        if strategy_id is None:
            return None
        try:
            manifest = json.loads(
                instance.component_manifest().read_text(encoding="utf-8")
            )
            market = manifest.get("components", {}).get("market", {})
            socket_value = market.get("socket") if isinstance(market, Mapping) else None
            market_socket = (
                Path(str(socket_value))
                if socket_value
                else self.workspace.paths.process_socket("market")
            )
            from kairospy.infrastructure.transport.commands import (
                MarketCommandClient,
                UnixJsonCommandClient,
            )

            request_id = (
                f"{strategy_id}:{instance_id}:market.release_owner:external-stop"
            )
            handle = MarketCommandClient(
                UnixJsonCommandClient(market_socket), launch_id=launch_id
            ).release_owner(
                strategy_id=strategy_id,
                instance_id=instance_id,
                request_id=request_id,
            )
            return {
                "status": handle.status,
                "request_id": handle.request_id,
                "result": dict(handle.result),
                "error": handle.error,
            }
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            return {"status": "failed", "error": str(error)}

    @staticmethod
    def _journal_strategy_id(path: Path) -> str | None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            strategy_id = (
                value.get("strategy_id") if isinstance(value, Mapping) else None
            )
            if isinstance(strategy_id, str) and strategy_id.strip():
                return strategy_id
        return None
