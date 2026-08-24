"""Python application boundary for process lifecycle and control."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from .supervisor import ProcessSpec, ProcessState, ProcessSupervisor, UnixRestClient
from .clients import (
    AccountSystemClient,
    CapitalSystemClient,
    ExecutionSystemClient,
    InstanceSystemClients,
    MarketSystemClient,
    ReferenceSystemClient,
    RiskSystemClient,
    SystemRpcClient,
    system_client,
)
from .reference import ReferenceProcessConfig
from .binaries import reject_owned_options, resolve_binary
from .risk import RiskProcessConfig
from .process_logging import start_logged_process


# Only these runtimes have workspace-scoped identity. Account, Risk, Capital,
# and Execution are owned by a launch instance and are inspected through that
# instance rather than the workspace inventory.
SYSTEM_COMPONENTS = ("reference", "market")


def _lock_is_held(path: Path) -> bool:
    """Check the advisory process lock without changing its ownership."""
    if not path.exists():
        return False
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value.isdigit() and _process_details(value)["alive"]:
            return True
        import fcntl

        with path.open("a+") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                import errno

                if error.errno in {errno.EACCES, errno.EAGAIN}:
                    return True
                raise
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            return False
    except (ImportError, OSError):
        return False


def _runtime_lock_path(
    workspace: Any, component: str, instance_workspace: Any | None
) -> Path:
    if instance_workspace is not None:
        return instance_workspace.lock(component)
    return workspace.paths.process_lock(component)


def _pid_is_alive(value: object) -> bool:
    try:
        pid = int(str(value))
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False
    except (ImportError, OSError):
        return False


def _process_details(value: object) -> dict[str, Any]:
    """Return portable-enough process details, including zombie detection.

    ``os.kill(pid, 0)`` reports zombies as alive on macOS and Linux.  That is
    exactly the situation a stale health file commonly produces, so status
    inspection also asks ``ps`` for the process state when available.
    """
    try:
        pid = int(str(value))
        if pid <= 0:
            return {"alive": False, "state": None, "command": None}
    except (TypeError, ValueError):
        return {"alive": False, "state": None, "command": None}
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat=,command="],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
        line = result.stdout.strip()
        if not line:
            return {"alive": False, "state": None, "command": None}
        state, _, command = line.partition(" ")
        return {
            "alive": not state.startswith("Z"),
            "state": state,
            "command": command.strip() or None,
        }
    except (OSError, subprocess.SubprocessError):
        return {"alive": _pid_is_alive(pid), "state": None, "command": None}


@dataclass(frozen=True, slots=True)
class ComponentControlApplication(SystemRpcClient):
    """Generic facade for system component control.

    Typed ``*SystemClient`` classes own business connections. This generic
    facade is only for system-level component inspection and control commands.
    """

    def command(self, component: str, command: dict[str, Any]) -> dict[str, Any]:
        return self.call(f"{component}_command", [command])


@dataclass(frozen=True, slots=True)
class ComponentProcessApplication:
    """Start a component's own Rust CLI before using its control facade."""

    workspace: Any
    binaries: Mapping[str, str] = field(default_factory=dict)
    # Reference exposes its control socket before the initial provider refresh
    # completes; this covers process readiness and the first health check,
    # rather than waiting for a full-universe download.
    ready_timeout: float = 60.0
    control_timeout: float = 3.0
    stop_timeout: float = 15.0

    def ensure_running(
        self,
        component: str,
        *,
        account_id: str | None = None,
        socket_name: str | None = None,
        reference_config: ReferenceProcessConfig | None = None,
        market_runtime_profile: str | None = None,
        confirm_live: bool = False,
        instance_workspace: Any | None = None,
        stream_startup_logs: bool = False,
    ) -> SystemRpcClient:
        # A static replay owns its observations locally and does not construct
        # a provider/reference Aeron source.  Keeping the driver out of this
        # path makes offline backtests independent from a live workspace
        # driver (and allows multiple replay instances to run safely).
        if component in {"reference", "account", "risk", "execution"} or (
            component == "market" and market_runtime_profile != "replay"
        ):
            self._ensure_aeron_driver()
        runtime = instance_workspace
        runtime_name = socket_name or component
        socket = (
            runtime.socket(runtime_name)
            if runtime is not None
            else self.workspace.paths.process_socket(runtime_name)
        )
        client_component = "account" if component == "account" else component
        control = self.client(client_component, socket, timeout=self.control_timeout)
        if socket.exists():
            try:
                health = control.status()
                if health.get("status") in {"ok", "ready", "running", "degraded"}:
                    return control
            except Exception:
                pass

        if socket.exists():
            lock = _runtime_lock_path(self.workspace, component, runtime)
            health_file = (
                runtime.health(runtime_name)
                if runtime is not None
                else self.workspace.paths.health_file(component)
            )
            health_value: Mapping[str, Any] = {}
            try:
                value = json.loads(health_file.read_text(encoding="utf-8"))
                if isinstance(value, Mapping):
                    health_value = value
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            if _lock_is_held(lock):
                raise RuntimeError(
                    f"{component} is unresponsive but its process lock is still held; "
                    "automatic restart was refused"
                )
            if not lock.exists() and _pid_is_alive(health_value.get("pid")):
                raise RuntimeError(
                    f"{component} is unresponsive and has no verifiable process lock; "
                    "automatic restart was refused"
                )
            socket.unlink(missing_ok=True)
            health_file.unlink(missing_ok=True)

        command, extra_environment = self._command(
            component,
            account_id=account_id,
            socket_name=socket_name,
            reference_config=reference_config,
            market_runtime_profile=market_runtime_profile,
            confirm_live=confirm_live,
            instance_workspace=runtime,
        )
        log_dir = (
            runtime.log(component)
            if runtime is not None
            else self.workspace.paths.logs / component
        )
        log_path = log_dir / "process.log"
        # Each process run gets a fresh active JSONL file; previous runs are
        # retained by the rotating sink as numbered backups.
        startup_log_offset = 0
        process = start_logged_process(
            command,
            component=component,
            log_path=log_path,
            cwd=str(self.workspace.paths.root),
            environment={**os.environ, **extra_environment},
        )
        recovery_command = (
            f"kairos launch artifacts {runtime.launch_id} "
            f"--instance {runtime.instance_id} --workspace {self.workspace.paths.project_root}"
            if runtime is not None
            else f"kairos system logs --component {component} --workspace {self.workspace.paths.project_root}"
        )
        return self._wait_ready(
            component,
            control,
            process=process,
            log_path=log_path,
            initial_log_offset=startup_log_offset,
            stream_logs=stream_startup_logs and component == "reference",
            recovery_command=recovery_command,
        )

    def _ensure_aeron_driver(self) -> None:
        health_file = self.workspace.paths.health_file("aeron")
        if health_file.is_file():
            try:
                value = json.loads(health_file.read_text(encoding="utf-8"))
                pid = int(value.get("pid", 0))
                os.kill(pid, 0)
                if value.get("status") == "ready":
                    return
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
            health_file.unlink(missing_ok=True)

        binary = self.binaries.get("aeron") or resolve_binary("kairos-aeron-driver")
        aeron_dir = self.workspace.paths.aeron_dir()
        aeron_dir.mkdir(parents=True, exist_ok=True)
        log_dir = self.workspace.paths.logs / "aeron"
        start_logged_process(
            [
                binary,
                "--aeron-dir",
                str(aeron_dir),
                "--health-file",
                str(health_file),
            ],
            component="aeron",
            log_path=log_dir / "process.log",
            cwd=str(self.workspace.paths.root),
            environment=os.environ.copy(),
        )

        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            try:
                value = json.loads(health_file.read_text(encoding="utf-8"))
                pid = int(value.get("pid", 0))
                os.kill(pid, 0)
                if value.get("status") == "ready":
                    return
            except (
                FileNotFoundError,
                OSError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
            ):
                pass
            time.sleep(0.05)
        raise TimeoutError(
            "Aeron media driver did not become ready; inspect workspace logs"
        )

    @staticmethod
    def client(
        component: str, socket: Path, *, timeout: float = 3.0
    ) -> SystemRpcClient:
        clients = {
            "account": AccountSystemClient,
            "execution": ExecutionSystemClient,
            "market": MarketSystemClient,
            "reference": ReferenceSystemClient,
            "risk": RiskSystemClient,
            "capital": CapitalSystemClient,
        }
        return clients.get(component, ComponentControlApplication)(
            socket, timeout=timeout
        )

    def stop(
        self,
        component: str,
        *,
        instance_workspace: Any | None = None,
        socket_name: str | None = None,
    ) -> dict[str, Any]:
        runtime_name = socket_name or component
        socket = (
            instance_workspace.socket(runtime_name)
            if instance_workspace is not None
            else self.workspace.paths.process_socket(runtime_name)
        )
        if not socket.exists():
            return {
                "component": component,
                "status": "not_running",
                "control_socket": str(socket),
            }
        return self.client(
            "account" if component == "account" else component, socket
        ).stop()

    def restart(
        self,
        component: str,
        *,
        account_id: str | None = None,
        stream_startup_logs: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> SystemRpcClient:
        """Stop a workspace component completely before starting its replacement."""
        report = progress or (lambda _message: None)
        report(f"Stopping {component}...")
        stop_error: Exception | None = None
        try:
            self.stop(component)
        except (OSError, RuntimeError, ValueError) as error:
            # A component can exit between socket discovery and the stop
            # request. Waiting on its process ownership distinguishes that
            # harmless race from a component which is still running.
            stop_error = error
        report(
            f"Waiting for {component} to release its process lock "
            f"(timeout: {self.stop_timeout:g}s)..."
        )
        try:
            self._wait_stopped(component, progress=progress)
        except TimeoutError as error:
            if stop_error is not None:
                raise TimeoutError(
                    f"{component} stop request failed and the process did not "
                    f"exit within {self.stop_timeout:g}s: {stop_error}"
                ) from stop_error
            raise
        report(f"{component} stopped; starting replacement...")
        control = self.ensure_running(
            component,
            account_id=account_id,
            stream_startup_logs=stream_startup_logs,
        )
        report(f"{component} restarted.")
        return control

    def _wait_stopped(
        self,
        component: str,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        """Wait until no live process can still own the component runtime."""
        lock = self.workspace.paths.process_lock(component)
        health_file = self.workspace.paths.health_file(component)
        started = time.monotonic()
        deadline = started + self.stop_timeout
        next_progress = started + 1.0
        while True:
            health_pid: object = None
            try:
                value = json.loads(health_file.read_text(encoding="utf-8"))
                if isinstance(value, Mapping):
                    health_pid = value.get("pid")
            except (OSError, ValueError, json.JSONDecodeError):
                pass

            lock_held = _lock_is_held(lock)
            pid_alive_without_lock = not lock.exists() and _pid_is_alive(health_pid)
            if not lock_held and not pid_alive_without_lock:
                return
            if time.monotonic() >= deadline:
                details = _process_details(health_pid)
                raise TimeoutError(
                    f"{component} did not stop within {self.stop_timeout:g}s; "
                    f"pid={health_pid}, pid_alive={details['alive']}, "
                    f"lock_held={lock_held}, lock={lock}"
                )
            now = time.monotonic()
            if progress is not None and now >= next_progress:
                elapsed = int(now - started)
                progress(f"Still waiting for {component} to stop... {elapsed}s")
                next_progress = now + 1.0
            time.sleep(0.05)

    def status(
        self,
        component: str,
        *,
        instance_workspace: Any | None = None,
        socket_name: str | None = None,
    ) -> dict[str, Any]:
        runtime_name = socket_name or component
        socket = (
            instance_workspace.socket(runtime_name)
            if instance_workspace is not None
            else self.workspace.paths.process_socket(runtime_name)
        )
        if not socket.exists():
            return {
                "component": component,
                "status": "not_running",
                "control_socket": str(socket),
            }
        try:
            return self.client(
                "account" if component == "account" else component,
                socket,
                timeout=self.control_timeout,
            ).status()
        except TimeoutError:
            return {
                "component": component,
                "status": "unresponsive",
                "control_socket": str(socket),
                "error": f"health check timed out after {self.control_timeout:g}s",
            }
        except Exception as error:
            return {
                "component": component,
                "status": "not_running",
                "control_socket": str(socket),
                "error": str(error),
            }

    def _runtime_metadata(self, component: str) -> dict[str, Any]:
        """Read process-owned runtime metadata without making a process call."""
        health_file = self.workspace.paths.health_file(component)
        value: dict[str, Any] = {}
        if health_file.is_file():
            try:
                parsed = json.loads(health_file.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    value = parsed
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        pid = value.get("pid")
        process = _process_details(pid)
        return {
            "pid": pid,
            "pid_alive": process["alive"],
            "process_state": process["state"],
            "process_command": process["command"],
            "health_file": str(health_file),
            "log_file": str(self.workspace.paths.logs / component / "process.log"),
            "process_lock": str(self.workspace.paths.process_lock(component)),
        }

    def list_status(self) -> dict[str, dict[str, Any]]:
        """Return the status of every workspace-scoped system component.

        Listing is read-only and must not start a missing component.  A stale
        control socket is reported as not running so the inventory remains
        useful after an unclean process exit.
        """
        with ThreadPoolExecutor(max_workers=len(SYSTEM_COMPONENTS)) as pool:
            values = pool.map(self.status, SYSTEM_COMPONENTS)
        result = dict(zip(SYSTEM_COMPONENTS, values))
        for component, status in result.items():
            metadata = self._runtime_metadata(component)
            # The control-plane health response is authoritative when the
            # process is reachable; the health file fills in diagnostics for
            # stopped/unresponsive processes and older component versions.
            if status.get("pid") is None and metadata.get("pid") is not None:
                status["pid"] = metadata["pid"]
            status.update(
                {key: value for key, value in metadata.items() if key not in status}
            )
            if status.get("pid") is not None:
                process = _process_details(status["pid"])
                status["pid_alive"] = process["alive"]
                status["process_state"] = process["state"]
                status["process_command"] = process["command"]
                if status.get("status") == "not_running":
                    status["status"] = "unhealthy" if process["alive"] else "stale"
        return cast(dict[str, dict[str, Any]], result)

    def logs(self, component: str, *, limit: int = 200) -> tuple[str, ...]:
        """Read recent workspace-component logs for operator surfaces."""

        if component not in SYSTEM_COMPONENTS:
            raise ValueError(f"unsupported workspace component: {component}")
        if limit <= 0:
            raise ValueError("log line limit must be positive")
        path = self.workspace.paths.logs / component / "process.log"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return ()
        return tuple(lines[-limit:])

    def doctor(self) -> dict[str, Any]:
        """Inspect runtime resources without mutating the workspace."""
        report: dict[str, Any] = {
            "workspace": str(self.workspace.paths.root),
            "components": {},
        }
        statuses = self.list_status()
        for component in SYSTEM_COMPONENTS:
            socket = self.workspace.paths.process_socket(component)
            health = self.workspace.paths.health_file(component)
            lock = self.workspace.paths.process_lock(component)
            socket_kind = "missing"
            if socket.exists():
                socket_kind = (
                    "socket" if stat.S_ISSOCK(socket.stat().st_mode) else "other"
                )
            health_value: dict[str, Any] | None = None
            if health.is_file():
                try:
                    value = json.loads(health.read_text(encoding="utf-8"))
                    health_value = value if isinstance(value, dict) else None
                except (OSError, ValueError, json.JSONDecodeError):
                    health_value = None
            report["components"][component] = {
                "status": statuses[component].get("status", "unknown"),
                "socket": str(socket),
                "socket_kind": socket_kind,
                "health_file": str(health),
                "health": health_value,
                "pid": statuses[component].get("pid"),
                "pid_alive": statuses[component].get("pid_alive", False),
                "process_state": statuses[component].get("process_state"),
                "process_command": statuses[component].get("process_command"),
                "lock": str(lock),
                "lock_held": _lock_is_held(lock),
                "repairable": (
                    socket_kind == "socket"
                    and statuses[component].get("status")
                    in {"stale", "not_running", "unresponsive"}
                    and not _lock_is_held(lock)
                ),
            }
        return report

    def repair(self) -> dict[str, Any]:
        """Remove only resources proven to be stale and not lock-owned."""
        repaired: list[str] = []
        skipped: dict[str, str] = {}
        report = self.doctor()
        for component, value in report["components"].items():
            socket = self.workspace.paths.process_socket(component)
            if value["repairable"]:
                socket.unlink(missing_ok=True)
                health = self.workspace.paths.health_file(component)
                if health.exists() and not value["lock_held"]:
                    health.unlink(missing_ok=True)
                repaired.append(component)
            elif value["socket_kind"] != "missing":
                skipped[component] = "active, healthy, or lock-owned"
        return {"repaired": repaired, "skipped": skipped}

    def _command(
        self,
        component: str,
        *,
        account_id: str | None,
        socket_name: str | None = None,
        reference_config: ReferenceProcessConfig | None = None,
        market_runtime_profile: str | None = None,
        confirm_live: bool = False,
        instance_workspace: Any | None = None,
    ) -> tuple[list[str], Mapping[str, str]]:
        if component == "reference":
            config = reference_config or ReferenceProcessConfig(self.workspace)
            if config.aeron_dir is None:
                config = replace(config, aeron_dir=self.workspace.paths.aeron_dir())
            configured = self.binaries.get("reference")
            if configured is not None or config.binary == "kairos-reference-server":
                config = replace(
                    config,
                    binary=resolve_binary(
                        "kairos-reference-server", override=configured
                    ),
                )
            spec = config.process_spec()
            return list(spec.command), {
                **spec.environment,
                "KAIROS_WORKSPACE_ID": self.workspace.workspace_id,
                **(
                    {
                        "KAIROS_INSTANCE_ID": instance_workspace.instance_id,
                        "KAIROS_LAUNCH_ID": instance_workspace.launch_id,
                        "KAIROS_LAUNCH_MODE": instance_workspace.mode,
                    }
                    if instance_workspace is not None
                    else {}
                ),
            }
        binary_name = {
            "account": "kairos-account-server",
            "control": "kairos-control-server",
            "execution": "kairos-execution-server",
            "market": "kairos-market-server",
            "risk": "kairos-risk-server",
        }.get(component, f"kairos-{component}-server")
        binary = self.binaries.get(component) or resolve_binary(binary_name)
        command = [binary, "--workspace", str(self.workspace.paths.root)]
        if instance_workspace is not None:
            command.extend(
                (
                    "--launch-mode",
                    instance_workspace.mode,
                    "--launch-id",
                    instance_workspace.launch_id,
                    "--instance-id",
                    instance_workspace.instance_id,
                )
            )
        child_environment: dict[str, str] = {
            "KAIROS_WORKSPACE_ID": self.workspace.workspace_id,
            "AERON_DIR": str(self.workspace.paths.aeron_dir()),
        }
        if instance_workspace is not None:
            child_environment.update(
                {
                    "KAIROS_INSTANCE_ID": instance_workspace.instance_id,
                    "KAIROS_LAUNCH_ID": instance_workspace.launch_id,
                    "KAIROS_LAUNCH_MODE": instance_workspace.mode,
                }
            )
        if component == "market":
            if market_runtime_profile is not None:
                command.extend(("--runtime-profile", market_runtime_profile))
        if component == "execution":
            # Execution reads the authoritative route collection from the
            # instance's normalized launch configuration.  Do not create a
            # second command-line configuration source.
            if confirm_live:
                command.append("--confirm-live")
        if component == "account":
            resolved_account = account_id or os.environ.get("KAIROS_ACCOUNT_ID")
            if not resolved_account:
                raise RuntimeError(
                    "account process requires --account-id or KAIROS_ACCOUNT_ID"
                )
            command.extend(("--account-id", resolved_account))
            if socket_name and socket_name != "account":
                command.extend(("--socket-name", socket_name))
        return command, child_environment

    def _wait_ready(
        self,
        component: str,
        control: SystemRpcClient,
        *,
        process: Any | None = None,
        log_path: Path | None = None,
        initial_log_offset: int | None = None,
        stream_logs: bool = False,
        recovery_command: str | None = None,
    ) -> SystemRpcClient:
        deadline = time.monotonic() + self.ready_timeout
        # Readiness polling must remain responsive to an early child exit.
        # The returned client keeps its normal control timeout once ready.
        readiness_control = replace(
            control,
            timeout=min(control.timeout, 0.1),
        )
        log_offset = (
            initial_log_offset
            if initial_log_offset is not None
            else (log_path.stat().st_size if log_path and log_path.exists() else 0)
        )
        startup_log_offset = log_offset

        def stream_new_logs() -> None:
            nonlocal log_offset
            if not stream_logs or log_path is None:
                return
            try:
                with log_path.open("rb") as stream:
                    stream.seek(log_offset)
                    payload = stream.read()
                    log_offset = stream.tell()
                if payload:
                    sys.stdout.write(payload.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
            except OSError:
                pass

        while True:
            stream_new_logs()
            return_code = process.poll() if process is not None else None
            if return_code is not None:
                stream_new_logs()
                # The detached JSONL sink can finish a few milliseconds after
                # the child closes its inherited pipe. Retry the non-blocking
                # read briefly without coupling startup failure reporting to
                # the sink process lifetime.
                detail = None
                for _ in range(5):
                    detail = _startup_log_detail(log_path, startup_log_offset)
                    if detail:
                        break
                    time.sleep(0.01)
                raise RuntimeError(
                    f"{component} process exited during startup with code {return_code}; "
                    f"log={log_path}"
                    + (f"; last_error={detail}" if detail else "")
                    + (f"; next: {recovery_command}" if recovery_command else "")
                )
            # Avoid spending a transport timeout on a socket the child has
            # not created yet. This also gives the loop a prompt opportunity
            # to observe process termination under a busy test/runtime host.
            if control.socket_path.exists():
                try:
                    health = readiness_control.status()
                    if health.get("status") in {
                        "ok",
                        "ready",
                        "running",
                        "degraded",
                    }:
                        stream_new_logs()
                        return control
                except Exception:
                    pass
            if time.monotonic() >= deadline:
                stream_new_logs()
                raise TimeoutError(
                    f"{component} process did not become ready within "
                    f"{self.ready_timeout:g}s; log={log_path}"
                    + (f"; next: {recovery_command}" if recovery_command else "")
                )
            time.sleep(0.05)


def _startup_log_detail(path: Path | None, offset: int) -> str | None:
    if path is None:
        return None
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            payload = stream.read(16 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = [line.strip() for line in payload.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, Mapping):
            fields = value.get("fields")
            if isinstance(fields, Mapping):
                detail = fields.get("error") or fields.get("message")
                if detail:
                    return str(detail)[:800]
        lowered = line.lower()
        if any(token in lowered for token in ("error", "failed", "panic")):
            return line[:800]
    return lines[-1][:800] if lines else None


@dataclass(frozen=True, slots=True)
class NativeCliApplication:
    """Run a module's independent one-shot CLI and return its JSON result."""

    workspace: Any
    binaries: Mapping[str, str] = field(default_factory=dict)

    def command(
        self, component: str, arguments: list[str], *, output: str | None = "json"
    ) -> list[str]:
        binary_names = {
            "account": "kairos-account-cli",
            "capital": "kairos-capital-cli",
            "execution": "kairos-execution-cli",
            "market": "kairos-market-cli",
            "risk": "kairos-risk-cli",
        }
        if component not in binary_names:
            raise ValueError(f"unsupported native CLI component: {component}")
        reject_owned_options(arguments, {"--workspace"})
        command = [
            self.binaries.get(component) or resolve_binary(binary_names[component]),
            "--workspace",
            str(self.workspace.paths.root),
        ]
        if output is not None:
            command.extend(("--output", output))
        command.extend(arguments)
        return command

    def invoke(
        self, component: str, arguments: list[str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(component, arguments, output=None),
            cwd=str(self.workspace.paths.root),
            capture_output=True,
            text=True,
            check=False,
        )

    def run(self, component: str, arguments: list[str]) -> dict[str, Any]:
        reject_owned_options(arguments, {"--output", "--format"})
        result = subprocess.run(
            self.command(component, arguments),
            cwd=str(self.workspace.paths.root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"{component} CLI failed")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{component} CLI returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError(f"{component} CLI must return a JSON object")
        return value


__all__ = [
    "ProcessSpec",
    "ProcessState",
    "ProcessSupervisor",
    "UnixRestClient",
    "SystemRpcClient",
    "AccountSystemClient",
    "ExecutionSystemClient",
    "InstanceSystemClients",
    "MarketSystemClient",
    "CapitalSystemClient",
    "ReferenceSystemClient",
    "RiskSystemClient",
    "system_client",
    "ReferenceProcessConfig",
    "RiskProcessConfig",
    "ComponentControlApplication",
    "ComponentProcessApplication",
    "NativeCliApplication",
    "SYSTEM_COMPONENTS",
    "resolve_binary",
]

from .runtime import (  # noqa: E402
    DEFAULT_RESTART_POLICIES,
    RestartPolicy,
    SystemRuntimeSupervisor,
)

__all__ += ["DEFAULT_RESTART_POLICIES", "RestartPolicy", "SystemRuntimeSupervisor"]
