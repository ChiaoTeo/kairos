"""Generic JSON and JSON-RPC transports over Unix domain sockets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol

from kairospy.infrastructure.unix_http import request_sync


class UnixJsonCommandClient:
    def __init__(self, socket_path: str | Path, *, timeout: float = 30.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout

    def request(
        self, method: str, path: str, body: Mapping[str, object] | None = None
    ) -> tuple[int, dict[str, Any]]:
        return request_sync(self.socket_path, method, path, body, timeout=self.timeout)

    def request_with_headers(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None = None,
        *,
        headers: Mapping[str, str],
    ) -> tuple[int, dict[str, Any]]:
        return request_sync(
            self.socket_path,
            method,
            path,
            body,
            timeout=self.timeout,
            headers=headers,
        )


class UnixJsonRpcClient:
    def __init__(self, socket_path: str | Path, *, timeout: float = 30.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self._next_id = 1

    def call(self, method: str, params: list[object] | None = None) -> dict[str, Any]:
        if not method or "/" in method:
            raise ValueError("JSON-RPC method name must be non-empty and path-free")
        request_id = self._next_id
        self._next_id += 1
        status, value = request_sync(
            self.socket_path,
            "POST",
            "/",
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": [] if params is None else params,
            },
            timeout=self.timeout,
        )
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", f"JSON-RPC request failed: HTTP {status}"))
            )
        if "error" in value:
            raise RuntimeError(str(value["error"]))
        result = value.get("result", {})
        return result if isinstance(result, dict) else {"result": result}


class JsonRpcCaller(Protocol):
    def call(
        self, method: str, params: list[object] | None = None
    ) -> Mapping[str, Any]: ...


__all__ = ["JsonRpcCaller", "UnixJsonCommandClient", "UnixJsonRpcClient"]
