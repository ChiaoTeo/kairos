from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .host import StrategyHost


class StrategyControlServer:
    """Small HTTP/1.1 control plane over an instance-owned Unix socket."""

    def __init__(self, host: StrategyHost, socket_path: str | Path) -> None:
        self.host = host
        self.socket_path = Path(socket_path)
        self._server: asyncio.AbstractServer | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        self._stopped.clear()
        self.socket_path.unlink(missing_ok=True)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))

    async def serve_until_stopped(self) -> None:
        await self._stopped.wait()

    async def close(self) -> None:
        if self._event_task is not None and not self._event_task.done():
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
        self._event_task = None
        self._stopped.set()
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self.socket_path.unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            header, _, body = request.partition(b"\r\n\r\n")
            lines = header.decode("ascii").splitlines()
            method, path, _ = lines[0].split(" ", 2)
            length = next((int(line.split(":", 1)[1].strip()) for line in lines[1:] if line.lower().startswith("content-length:")), 0)
            if length:
                body += await reader.readexactly(length - len(body))
            result = self._dispatch(method, path, body)
            self._write(writer, 200, result)
        except Exception as error:
            self._write(writer, 400, {"error": str(error)})
        finally:
            await writer.drain()
            writer.close()
            await writer.wait_closed()

    def _dispatch(self, method: str, path: str, body: bytes) -> dict[str, Any]:
        if method == "GET" and path == "/v1/health":
            status = self.host.status
            return {"status": "ready", "strategy_state": status.state.value, "launch_id": status.launch_id, "instance_id": status.instance_id, "strategy_id": status.strategy_id, "event_sequence": status.event_sequence, "reason": status.reason}
        if method == "GET" and path == "/v1/status":
            return self._status(self.host.status)
        if method == "POST" and path == "/v1/start":
            return self._status(self.host.start())
        if method == "POST" and path == "/v1/enable":
            result = self.host.enable()
            self._event_task = asyncio.create_task(self.host.run())
            return self._status(result)
        if method == "POST" and path == "/v1/pause":
            return self._status(self.host.pause())
        if method == "POST" and path == "/v1/resume":
            return self._status(self.host.resume())
        if method == "POST" and path == "/v1/refresh":
            return self._status(self.host.refresh())
        if method == "POST" and path == "/v1/stop":
            result = self.host.stop()
            if self._event_task is not None and not self._event_task.done():
                self._event_task.cancel()
            self._stopped.set()
            return self._status(result)
        raise ValueError(f"unsupported strategy control request: {method} {path}")

    @staticmethod
    def _status(status: object) -> dict[str, Any]:
        return {"status": getattr(status.state, "value", str(status.state)), "launch_id": status.launch_id, "instance_id": status.instance_id, "strategy_id": status.strategy_id, "event_sequence": status.event_sequence, "reason": status.reason}

    @staticmethod
    def _write(writer: asyncio.StreamWriter, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, default=str).encode("utf-8")
        reason = "OK" if status == 200 else "Bad Request"
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii") + body)
