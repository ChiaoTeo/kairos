"""Python application boundary for process lifecycle and control."""

from __future__ import annotations

import json
import os
import subprocess
import time
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
    ready_timeout: float = 15.0

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
    ) -> SystemRestClient:
        if component == "reference" or (component == "market" and market_provider in {None, "workspace"}):
            self._ensure_aeron_driver()
        runtime = instance_workspace
        runtime_name = socket_name or component
        socket = runtime.socket(runtime_name) if runtime is not None else self.workspace.paths.process_socket(runtime_name)
        client_component = "account" if component == "account" else component
        control = self.client(client_component, socket)
        try:
            health = control.status()
            if health.get("status") in {"ok", "ready", "running"}:
                return control
        except (OSError, RuntimeError, ValueError):
            pass

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
        return self._wait_ready(runtime_name, control)

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
    def client(component: str, socket: Path) -> SystemRestClient:
        clients = {
            "account": AccountSystemClient,
            "execution": ExecutionSystemClient,
            "market": MarketSystemClient,
            "reference": ReferenceSystemClient,
            "risk": RiskSystemClient,
        }
        return clients.get(component, ComponentControlApplication)(socket)

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
        return self.client("account" if component == "account" else component, socket).status()

    def list_status(self) -> dict[str, dict[str, Any]]:
        """Return the status of every workspace-scoped system component.

        Listing is read-only and must not start a missing component.  A stale
        control socket is reported as not running so the inventory remains
        useful after an unclean process exit.
        """
        result: dict[str, dict[str, Any]] = {}
        for component in SYSTEM_COMPONENTS:
            try:
                result[component] = self.status(component)
            except (OSError, RuntimeError, ValueError):
                result[component] = {
                    "component": component,
                    "status": "not_running",
                    "control_socket": str(self.workspace.paths.process_socket(component)),
                }
        return result

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

    def _wait_ready(self, component: str, control: SystemRestClient) -> SystemRestClient:
        deadline = time.monotonic() + self.ready_timeout
        while True:
            try:
                health = control.status()
                if health.get("status") in {"ok", "ready", "running"}:
                    return control
            except (OSError, RuntimeError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{component} process did not become ready; inspect workspace logs")
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
