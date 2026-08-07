from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence


class ProcessState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    EXITED = "exited"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    name: str
    command: tuple[str, ...] | Sequence[str]
    cwd: Path | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    health_file: Path | None = None
    control_socket: Path | None = None
    stop_timeout: float = 10.0

    def __post_init__(self) -> None:
        command = tuple(self.command)
        if not self.name.strip():
            raise ValueError("process name is required")
        if not command:
            raise ValueError("process command is required")
        if self.stop_timeout <= 0:
            raise ValueError("stop_timeout must be positive")
        object.__setattr__(self, "command", command)
        if self.cwd is not None and not isinstance(self.cwd, Path):
            object.__setattr__(self, "cwd", Path(self.cwd))
        if self.health_file is not None and not isinstance(self.health_file, Path):
            object.__setattr__(self, "health_file", Path(self.health_file))
        if self.control_socket is not None and not isinstance(self.control_socket, Path):
            object.__setattr__(self, "control_socket", Path(self.control_socket))


@dataclass(slots=True)
class _ManagedProcess:
    spec: ProcessSpec
    process: asyncio.subprocess.Process
    state: ProcessState


class ProcessSupervisor:
    """Owns child process lifecycle, never their business state."""

    def __init__(self) -> None:
        self._processes: dict[str, _ManagedProcess] = {}
        self._lock = asyncio.Lock()

    async def start(self, spec: ProcessSpec) -> None:
        async with self._lock:
            current = self._processes.get(spec.name)
            if current is not None and current.process.returncode is None:
                raise RuntimeError(f"process already running: {spec.name}")
            environment = os.environ.copy()
            environment.update(spec.environment)
            process = await asyncio.create_subprocess_exec(
                *spec.command,
                cwd=str(spec.cwd) if spec.cwd is not None else None,
                env=environment,
                start_new_session=True,
            )
            managed = _ManagedProcess(spec, process, ProcessState.RUNNING)
            self._processes[spec.name] = managed
            asyncio.create_task(self._observe(spec.name, managed))

    async def start_ready(self, spec: ProcessSpec, *, timeout: float = 30.0) -> Mapping[str, Any]:
        """Start a process and wait for its process-owned health contract."""
        await self.start(spec)
        return await self.wait_ready(spec.name, timeout=timeout)

    async def wait_ready(self, name: str, *, timeout: float = 30.0) -> Mapping[str, Any]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            managed = self._processes.get(name)
            if managed is None:
                raise KeyError(name)
            if managed.process.returncode is not None:
                raise RuntimeError(f"process exited before ready: {name}")
            try:
                health = await self.remote_health(name) if managed.spec.control_socket else self.health(name)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                health = None
            if isinstance(health, Mapping) and health.get("status") in {"ok", "ready", "running"}:
                return health
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"process did not become ready: {name}")
            await asyncio.sleep(0.05)

    async def stop(self, name: str) -> None:
        async with self._lock:
            managed = self._processes.get(name)
            if managed is None:
                raise KeyError(name)
            if managed.process.returncode is not None:
                managed.state = ProcessState.EXITED
                return
            managed.state = ProcessState.STOPPING

        # A business process with a control socket must receive its graceful
        # stop command through the process-owned REST boundary first. Signals
        # remain the recovery path for a dead/unresponsive control plane.
        if managed.spec.control_socket is not None:
            try:
                await asyncio.wait_for(
                    self.control(name).stop(),
                    timeout=min(managed.spec.stop_timeout, 5.0),
                )
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass

        if managed.process.returncode is not None:
            managed.state = ProcessState.EXITED
            return

        if managed.spec.control_socket is not None:
            # The REST stop is asynchronous. Give the process a short grace
            # period before using the OS-level fallback.
            try:
                await asyncio.wait_for(
                    asyncio.shield(managed.process.wait()),
                    timeout=min(managed.spec.stop_timeout, 2.0),
                )
                managed.state = ProcessState.EXITED
                return
            except asyncio.TimeoutError:
                pass

        async with self._lock:
            if managed.process.returncode is not None:
                managed.state = ProcessState.EXITED
                return
            try:
                os.killpg(managed.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(managed.process.wait(), timeout=managed.spec.stop_timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(managed.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await managed.process.wait()
        managed.state = ProcessState.EXITED

    def control(self, name: str) -> "UnixRestClient":
        managed = self._processes.get(name)
        if managed is None:
            raise KeyError(name)
        if managed.spec.control_socket is None:
            raise ValueError(f"process has no control socket: {name}")
        return UnixRestClient(managed.spec.control_socket)

    async def request(
        self,
        name: str,
        method: str,
        path: str,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        return await self.control(name).request(method, path, body)

    async def remote_health(self, name: str) -> dict[str, Any]:
        """Read health through the process control plane, not its data store."""
        return await self.control(name).health()

    def statuses(self) -> dict[str, ProcessState]:
        for managed in self._processes.values():
            if managed.process.returncode is not None and managed.state not in {ProcessState.STOPPING, ProcessState.EXITED}:
                managed.state = ProcessState.FAILED if managed.process.returncode else ProcessState.EXITED
        return {name: managed.state for name, managed in self._processes.items()}

    def health(self, name: str) -> Mapping[str, Any] | None:
        """Read the process-owned health contract, without inspecting business state."""
        managed = self._processes.get(name)
        if managed is None or managed.spec.health_file is None:
            return None
        try:
            import json

            value = json.loads(managed.spec.health_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def shutdown(self) -> None:
        for name in tuple(self._processes):
            managed = self._processes[name]
            if managed.process.returncode is None:
                await self.stop(name)

    async def _observe(self, name: str, managed: _ManagedProcess) -> None:
        returncode = await managed.process.wait()
        if managed.state is not ProcessState.STOPPING:
            managed.state = ProcessState.EXITED if returncode == 0 else ProcessState.FAILED


class UnixRestClient:
    """Minimal HTTP/1.1 client over a Unix domain socket."""

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = Path(socket_path)

    async def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        if not method or not path.startswith("/"):
            raise ValueError("invalid Unix REST request")
        payload = body or b""
        request = (
            f"{method.upper()} {path} HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Connection: close\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n"
        ).encode("ascii") + payload
        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        try:
            writer.write(request)
            await writer.drain()
            response = await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: bytes) -> dict[str, Any]:
        header, separator, body = response.partition(b"\r\n\r\n")
        if not separator:
            raise ValueError("invalid Unix REST response")
        status_line = header.split(b"\r\n", 1)[0].decode("ascii")
        parts = status_line.split()
        if len(parts) < 2 or not parts[1].isdigit():
            raise ValueError("invalid Unix REST status")
        status = int(parts[1])
        import json

        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Unix REST response must be an object")
        if status < 200 or status >= 300:
            raise RuntimeError(f"reference control request failed ({status}): {value}")
        return value

    async def health(self) -> dict[str, Any]:
        return await self.request("GET", "/v1/health")

    async def refresh(self) -> dict[str, Any]:
        return await self.request("POST", "/v1/refresh")

    async def publish(self) -> dict[str, Any]:
        return await self.request("POST", "/v1/publish")

    async def stop(self) -> dict[str, Any]:
        return await self.request("POST", "/v1/stop")
