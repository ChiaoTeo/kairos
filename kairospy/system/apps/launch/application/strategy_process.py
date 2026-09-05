"""Launch-owned controller for the optional Python Strategy process."""

from __future__ import annotations

import asyncio
import json
import os
import signal
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
from kairospy.system.apps.workspace.application import InstanceWorkspace, Workspace


_PROCESS_METADATA_VERSION = 1


def _process_metadata_path(instance_workspace: InstanceWorkspace) -> Path:
    return instance_workspace.paths.process_dir("strategy") / "process.json"


def _write_process_metadata(
    path: Path,
    *,
    pid: int,
    workspace: Workspace,
    launch_id: str,
    instance_id: str,
    mode: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": _PROCESS_METADATA_VERSION,
                "pid": pid,
                "workspace": str(workspace.paths.root),
                "launch_id": launch_id,
                "instance_id": instance_id,
                "mode": mode,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _owned_process_pid(
    path: Path,
    *,
    workspace: Workspace,
    launch_id: str,
    instance_id: str,
    mode: str,
) -> int | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("process metadata must be an object")
        if value.get("schema_version") != _PROCESS_METADATA_VERSION:
            raise ValueError("unsupported process metadata version")
        if (
            value.get("workspace") != str(workspace.paths.root)
            or value.get("launch_id") != launch_id
            or value.get("instance_id") != instance_id
            or value.get("mode") != mode
        ):
            raise ValueError("process metadata identity does not match")
        pid = value.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("process metadata pid must be positive")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None

    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat=,command="],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = completed.stdout.strip()
    if not line:
        path.unlink(missing_ok=True)
        return None
    state, _, command = line.partition(" ")
    expected = (
        "-m kairospy.bin.strategy",
        f"--workspace {workspace.paths.root}",
        f"--launch-id {launch_id}",
        f"--instance-id {instance_id}",
        f"--mode {mode}",
    )
    if state.startswith("Z") or any(token not in command for token in expected):
        path.unlink(missing_ok=True)
        return None
    return pid


def _wait_process_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            completed = subprocess.run(
                ["ps", "-p", str(pid), "-o", "stat="],
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
        else:
            state = completed.stdout.strip()
            if not state or state.startswith("Z"):
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _terminate_process(pid: int, timeout: float) -> None:
    try:
        process_group = os.getpgid(pid)
        if process_group == pid:
            os.killpg(process_group, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if _wait_process_exit(pid, timeout):
        return
    try:
        process_group = os.getpgid(pid)
        if process_group == pid:
            os.killpg(process_group, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    _wait_process_exit(pid, timeout)


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
        metadata_path = _process_metadata_path(instance_workspace)
        existing_pid = _owned_process_pid(
            metadata_path,
            workspace=self.workspace,
            launch_id=launch_id,
            instance_id=instance_id,
            mode=mode,
        )
        if existing_pid is not None:
            raise RuntimeError(
                "strategy server process is running but its control plane is unavailable; "
                "stop the launch before retrying"
            )
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
            process = subprocess.Popen(
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
        _write_process_metadata(
            metadata_path,
            pid=process.pid,
            workspace=self.workspace,
            launch_id=launch_id,
            instance_id=instance_id,
            mode=mode,
        )

        deadline = time.monotonic() + self.ready_timeout
        while True:
            returncode = process.poll()
            if returncode is not None:
                metadata_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"strategy server exited before ready with code {returncode}; "
                    f"inspect {log_path}"
                )
            try:
                health = asyncio.run(client.request("GET", "/v1/health"))
                if health.get("status") == "ready":
                    return socket
            except Exception:
                pass
            if time.monotonic() >= deadline:
                _terminate_process(process.pid, self.ready_timeout)
                metadata_path.unlink(missing_ok=True)
                socket.unlink(missing_ok=True)
                raise TimeoutError(
                    f"strategy server did not become ready; inspect {log_path}"
                )
            time.sleep(0.05)

    def stop(
        self, launch_id: str, instance_id: str, mode: str = "paper"
    ) -> dict[str, Any]:
        socket = self.socket(launch_id, instance_id, mode)
        instance_workspace = self.workspace.instance(mode, launch_id, instance_id)
        metadata_path = _process_metadata_path(instance_workspace)
        pid = _owned_process_pid(
            metadata_path,
            workspace=self.workspace,
            launch_id=launch_id,
            instance_id=instance_id,
            mode=mode,
        )
        control_error: str | None = None
        try:
            result = asyncio.run(UnixRestClient(socket).request("POST", "/v1/stop"))
        except (FileNotFoundError, OSError, RuntimeError, TimeoutError) as error:
            control_error = str(error)
            result = {
                "launch_id": launch_id,
                "instance_id": instance_id,
            }
        if control_error is not None and pid is not None:
            _terminate_process(pid, self.ready_timeout)
        deadline = time.monotonic() + self.ready_timeout
        while socket.exists() or (
            pid is not None and not _wait_process_exit(pid, 0.05)
        ):
            if time.monotonic() < deadline:
                time.sleep(0.05)
                continue
            if pid is not None:
                _terminate_process(pid, self.ready_timeout)
                break
            return {
                **result,
                "status": "stop_failed",
                "error": "strategy control socket remained after stop timeout",
                **({"control_error": control_error} if control_error else {}),
            }
        if pid is not None and not _wait_process_exit(pid, self.ready_timeout):
            return {
                **result,
                "status": "stop_failed",
                "error": "strategy process remained after stop timeout",
                **({"control_error": control_error} if control_error else {}),
            }
        if pid is None and control_error is not None:
            metadata_path.unlink(missing_ok=True)
            return {
                **result,
                "status": "not_running",
                "control_error": control_error,
            }
        socket.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        result["status"] = "stopped"
        if control_error is not None:
            result["control_error"] = control_error
        return result
