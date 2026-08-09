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
from typing import Any, Mapping

from .supervisor import ProcessSpec, ProcessState, ProcessSupervisor, UnixRestClient
from .clients import (
    AccountSystemClient,
    ExecutionSystemClient,
    MarketSystemClient,
    ReferenceSystemClient,
    RiskSystemClient,
    SystemRestClient,
)
from .reference import ReferenceProcessConfig
from .binaries import reject_owned_options, resolve_binary
from .risk import RiskProcessConfig


SYSTEM_COMPONENTS = ("reference", "market", "account", "risk", "execution")


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


def _runtime_lock_path(workspace: Any, component: str, instance_workspace: Any | None) -> Path:
    if instance_workspace is not None:
        return instance_workspace.lock(component)
    return workspace.paths.process_lock(component)


def _pid_is_alive(value: object) -> bool:
    try:
        pid = int(value)
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
        pid = int(value)
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
class ComponentControlApplication(SystemRestClient):
    """Generic facade for system component control.

    Typed ``*SystemClient`` classes own business endpoints. This generic
    facade is only for system-level component inspection and control commands.
    """

    def command(self, component: str, command: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(command, separators=(",", ":")).encode("utf-8")
        return self.request("POST", f"/v1/components/{component}/commands", payload)


@dataclass(frozen=True, slots=True)
class ComponentProcessApplication:
    """Start a component's own Rust CLI before using its control facade."""

    workspace: Any
    binaries: Mapping[str, str] = field(default_factory=dict)
    # Reference performs a full-universe refresh before creating its control
    # socket. A cold SQLite/catalog refresh can exceed fifteen seconds.
    ready_timeout: float = 60.0
    control_timeout: float = 3.0

    def ensure_running(
        self,
        component: str,
        *,
        account_id: str | None = None,
        socket_name: str | None = None,
        reference_config: ReferenceProcessConfig | None = None,
        market_provider: str | None = None,
        market_credential_id: str | None = None,
        market_replay_file: Path | None = None,
        provider: str | None = None,
        product: str | None = None,
        confirm_live: bool = False,
        instance_workspace: Any | None = None,
        stream_startup_logs: bool = False,
    ) -> SystemRestClient:
        if component == "reference" or (component == "market" and market_provider in {None, "workspace"}):
            self._ensure_aeron_driver()
        runtime = instance_workspace
        runtime_name = socket_name or component
        socket = runtime.socket(runtime_name) if runtime is not None else self.workspace.paths.process_socket(runtime_name)
        client_component = "account" if component == "account" else component
        control = self.client(client_component, socket, timeout=self.control_timeout)
        try:
            health = control.status()
            if health.get("status") in {"ok", "ready", "running"}:
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
            market_provider=market_provider,
            market_credential_id=market_credential_id,
            market_replay_file=market_replay_file,
            provider=provider,
            product=product,
            confirm_live=confirm_live,
            instance_workspace=runtime,
        )
        log_dir = runtime.log("processes") if runtime is not None else self.workspace.paths.logs / "processes"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = (log_dir / f"{component}.log").open("ab")
        startup_log_offset = log.tell()
        try:
            subprocess.Popen(
                command,
                cwd=str(self.workspace.paths.root),
                env={**os.environ, **extra_environment},
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log.close()
        return self._wait_ready(
            component,
            control,
            log_path=log_dir / f"{component}.log",
            initial_log_offset=startup_log_offset,
            stream_logs=stream_startup_logs and component == "reference",
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
        log_dir = self.workspace.paths.logs / "processes"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = (log_dir / "aeron.log").open("ab")
        try:
            subprocess.Popen(
                [binary, "--health-file", str(health_file)],
                cwd=str(self.workspace.paths.root),
                env=os.environ.copy(),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log.close()

        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            try:
                value = json.loads(health_file.read_text(encoding="utf-8"))
                pid = int(value.get("pid", 0))
                os.kill(pid, 0)
                if value.get("status") == "ready":
                    return
            except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
            time.sleep(0.05)
        raise TimeoutError("Aeron media driver did not become ready; inspect workspace logs")

    @staticmethod
    def client(component: str, socket: Path, *, timeout: float = 3.0) -> SystemRestClient:
        clients = {
            "account": AccountSystemClient,
            "execution": ExecutionSystemClient,
            "market": MarketSystemClient,
            "reference": ReferenceSystemClient,
            "risk": RiskSystemClient,
        }
        return clients.get(component, ComponentControlApplication)(socket, timeout=timeout)

    def stop(self, component: str, *, instance_workspace: Any | None = None, socket_name: str | None = None) -> dict[str, Any]:
        runtime_name = socket_name or component
        socket = instance_workspace.socket(runtime_name) if instance_workspace is not None else self.workspace.paths.process_socket(runtime_name)
        if not socket.exists():
            return {"component": component, "status": "not_running", "control_socket": str(socket)}
        return self.client("account" if component == "account" else component, socket).stop()

    def status(self, component: str, *, instance_workspace: Any | None = None, socket_name: str | None = None) -> dict[str, Any]:
        runtime_name = socket_name or component
        socket = instance_workspace.socket(runtime_name) if instance_workspace is not None else self.workspace.paths.process_socket(runtime_name)
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
            "log_file": str(self.workspace.paths.logs / "processes" / f"{component}.log"),
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
            status.update({key: value for key, value in metadata.items() if key not in status})
            if status.get("pid") is not None:
                process = _process_details(status["pid"])
                status["pid_alive"] = process["alive"]
                status["process_state"] = process["state"]
                status["process_command"] = process["command"]
                if status.get("status") == "not_running":
                    status["status"] = "unhealthy" if process["alive"] else "stale"
        return result

    def doctor(self) -> dict[str, Any]:
        """Inspect runtime resources without mutating the workspace."""
        report: dict[str, Any] = {"workspace": str(self.workspace.paths.root), "components": {}}
        statuses = self.list_status()
        for component in SYSTEM_COMPONENTS:
            socket = self.workspace.paths.process_socket(component)
            health = self.workspace.paths.health_file(component)
            lock = self.workspace.paths.process_lock(component)
            socket_kind = "missing"
            if socket.exists():
                socket_kind = "socket" if stat.S_ISSOCK(socket.stat().st_mode) else "other"
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
                    and statuses[component].get("status") in {"stale", "not_running", "unresponsive"}
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
        market_provider: str | None = None,
        market_credential_id: str | None = None,
        market_replay_file: Path | None = None,
        provider: str | None = None,
        product: str | None = None,
        confirm_live: bool = False,
        instance_workspace: Any | None = None,
    ) -> tuple[list[str], Mapping[str, str]]:
        if component == "reference":
            config = reference_config or ReferenceProcessConfig(self.workspace, provider="default")
            configured = self.binaries.get("reference")
            if configured is not None or config.binary == "kairos-reference-server":
                config = replace(
                    config,
                    binary=resolve_binary(
                        "kairos-reference-server", override=configured
                    ),
                )
            spec = config.process_spec()
            return list(spec.command), spec.environment
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
            command.extend(("--launch-mode", instance_workspace.mode, "--launch-id", instance_workspace.launch_id, "--instance-id", instance_workspace.instance_id))
        child_environment: dict[str, str] = {}
        if component == "market":
            command.extend(("--provider", market_provider or "workspace"))
            if market_credential_id is not None:
                command.extend(("--credential-id", market_credential_id))
            if market_replay_file is not None:
                command.extend(("--replay-file", str(market_replay_file)))
        if component == "execution":
            if provider is not None:
                command.extend(("--provider", provider))
            if product is not None:
                command.extend(("--product", product))
            if confirm_live:
                command.append("--confirm-live")
        if component == "account":
            resolved_account = account_id or os.environ.get("KAIROS_ACCOUNT_ID")
            if not resolved_account:
                raise RuntimeError("account process requires --account-id or KAIROS_ACCOUNT_ID")
            command.extend(("--account-id", resolved_account))
            if socket_name and socket_name != "account":
                command.extend(("--socket-name", socket_name))
            if provider is not None:
                command.extend(("--provider", provider))
        return command, child_environment

    def _wait_ready(
        self,
        component: str,
        control: SystemRestClient,
        *,
        log_path: Path | None = None,
        initial_log_offset: int | None = None,
        stream_logs: bool = False,
    ) -> SystemRestClient:
        deadline = time.monotonic() + self.ready_timeout
        log_offset = (
            initial_log_offset
            if initial_log_offset is not None
            else (log_path.stat().st_size if log_path and log_path.exists() else 0)
        )

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
                    sys.stdout.buffer.write(payload)
                    sys.stdout.flush()
            except OSError:
                pass

        while True:
            stream_new_logs()
            try:
                health = control.status()
                if health.get("status") in {"ok", "ready", "running"}:
                    stream_new_logs()
                    return control
            except Exception:
                pass
            if time.monotonic() >= deadline:
                stream_new_logs()
                raise TimeoutError(
                    f"{component} process did not become ready within "
                    f"{self.ready_timeout:g}s; inspect workspace logs"
                )
            time.sleep(0.05)


@dataclass(frozen=True, slots=True)
class NativeCliApplication:
    """Run a module's independent one-shot CLI and return its JSON result."""

    workspace: Any
    binaries: Mapping[str, str] = field(default_factory=dict)

    def command(self, component: str, arguments: list[str], *, output: str | None = "json") -> list[str]:
        if component != "execution":
            raise ValueError(f"unsupported native CLI component: {component}")
        binary_name = "kairos-execution-cli"
        reject_owned_options(arguments, {"--workspace"})
        command = [
            self.binaries.get(component) or resolve_binary(binary_name),
            "--workspace",
            str(self.workspace.paths.root),
        ]
        if output is not None:
            command.extend(("--output", output))
        command.extend(arguments)
        return command

    def invoke(self, component: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
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
    "SystemRestClient",
    "AccountSystemClient",
    "ExecutionSystemClient",
    "MarketSystemClient",
    "ReferenceSystemClient",
    "RiskSystemClient",
    "ReferenceProcessConfig",
    "RiskProcessConfig",
    "ComponentControlApplication",
    "ComponentProcessApplication",
    "NativeCliApplication",
    "SYSTEM_COMPONENTS",
    "resolve_binary",
]

from .runtime import DEFAULT_RESTART_POLICIES, RestartPolicy, SystemRuntimeSupervisor

__all__ += ["DEFAULT_RESTART_POLICIES", "RestartPolicy", "SystemRuntimeSupervisor"]
