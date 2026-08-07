"""Python application boundary for process lifecycle and control."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from .supervisor import ProcessSpec, ProcessState, ProcessSupervisor, UnixRestClient
from .reference import ReferenceProcessConfig
from .binaries import resolve_binary
from .risk import RiskProcessConfig


@dataclass(frozen=True, slots=True)
class ComponentControlApplication:
    """Application facade for a component-owned Unix REST control socket."""

    socket_path: Path

    def request(self, method: str, path: str, body: bytes | None = None) -> dict[str, Any]:
        return asyncio.run(UnixRestClient(self.socket_path).request(method, path, body))

    def status(self) -> dict[str, Any]:
        return self.request("GET", "/v1/health")

    def snapshot(self) -> dict[str, Any]:
        return self.request("GET", "/v1/snapshot")

    def refresh(self) -> dict[str, Any]:
        return self.request("POST", "/v1/refresh")

    def stop(self) -> dict[str, Any]:
        return self.request("POST", "/v1/stop")

    def command(self, component: str, command: dict[str, Any]) -> dict[str, Any]:
        import json
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
        reference_config: ReferenceProcessConfig | None = None,
        market_provider: str | None = None,
        market_replay_file: Path | None = None,
        instance_workspace: Any | None = None,
    ) -> ComponentControlApplication:
        runtime = instance_workspace
        socket = runtime.socket(component) if runtime is not None else self.workspace.paths.process_socket(component)
        control = ComponentControlApplication(socket)
        try:
            health = control.status()
            if health.get("status") in {"ok", "ready", "running"}:
                return control
        except (OSError, RuntimeError, ValueError):
            pass

        command, extra_environment = self._command(
            component,
            account_id=account_id,
            reference_config=reference_config,
            market_provider=market_provider,
            market_replay_file=market_replay_file,
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
        return self._wait_ready(component, control)

    def stop(self, component: str, *, instance_workspace: Any | None = None) -> dict[str, Any]:
        socket = instance_workspace.socket(component) if instance_workspace is not None else self.workspace.paths.process_socket(component)
        if not socket.exists():
            return {"component": component, "status": "not_running", "control_socket": str(socket)}
        return ComponentControlApplication(socket).stop()

    def status(self, component: str, *, instance_workspace: Any | None = None) -> dict[str, Any]:
        socket = instance_workspace.socket(component) if instance_workspace is not None else self.workspace.paths.process_socket(component)
        if not socket.exists():
            return {
                "component": component,
                "status": "not_running",
                "control_socket": str(socket),
            }
        return ComponentControlApplication(socket).status()

    def _command(
        self,
        component: str,
        *,
        account_id: str | None,
        reference_config: ReferenceProcessConfig | None = None,
        market_provider: str | None = None,
        market_replay_file: Path | None = None,
        instance_workspace: Any | None = None,
    ) -> tuple[list[str], Mapping[str, str]]:
        if component == "reference":
            config = reference_config or ReferenceProcessConfig(self.workspace)
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
        if component == "market" and market_provider is not None:
            command.extend(("--provider", market_provider))
            if market_replay_file is not None:
                command.extend(("--replay-file", str(market_replay_file)))
        if component == "account":
            resolved_account = account_id or os.environ.get("KAIROS_ACCOUNT_ID")
            if not resolved_account:
                raise RuntimeError("account process requires --account-id or KAIROS_ACCOUNT_ID")
            command.extend(("--account-id", resolved_account))
        return command, child_environment

    def _wait_ready(self, component: str, control: ComponentControlApplication) -> ComponentControlApplication:
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

    def run(self, component: str, arguments: list[str]) -> dict[str, Any]:
        if component != "execution":
            raise ValueError(f"unsupported native CLI component: {component}")
        binary_name = "kairos-execution-cli"
        global_flags = {"--confirm-live"}
        global_flags |= {"--provider", "--product", "--credential-id"}
        prefix: list[str] = []
        command_arguments: list[str] = []
        index = 0
        while index < len(arguments):
            item = arguments[index]
            if item in global_flags:
                prefix.append(item)
                if item != "--confirm-live":
                    index += 1
                    if index >= len(arguments):
                        raise ValueError(f"missing value for {item}")
                    prefix.append(arguments[index])
            else:
                command_arguments.append(item)
            index += 1
        command = [
            self.binaries.get(component) or resolve_binary(binary_name),
            "--workspace",
            str(self.workspace.paths.root),
            "--output",
            "json",
            *prefix,
            *command_arguments,
        ]
        result = subprocess.run(
            command,
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
    "ReferenceProcessConfig",
    "RiskProcessConfig",
    "ComponentControlApplication",
    "ComponentProcessApplication",
    "NativeCliApplication",
    "resolve_binary",
]
