"""Generic JSON and JSON-RPC transports over Unix domain sockets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol

from kairospy.infrastructure.unix_http import request_sync


class JsonRpcCallError(RuntimeError):
    """Structured remote failure returned by a JSON-RPC endpoint."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        data: object = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
        self.http_status = http_status


class JsonRpcProtocolError(ValueError):
    """The peer returned a response that is not a valid object result."""


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
            raise JsonRpcCallError(
                f"JSON-RPC request failed: HTTP {status}",
                data=value.get("error"),
                http_status=status,
            )
        if value.get("jsonrpc") != "2.0":
            raise JsonRpcProtocolError("JSON-RPC response is missing version 2.0")
        if value.get("id") != request_id:
            raise JsonRpcProtocolError(
                "JSON-RPC response id does not match the request"
            )
        error = value.get("error")
        if error is not None:
            if isinstance(error, Mapping):
                message = error.get("message")
                code = error.get("code")
                raise JsonRpcCallError(
                    str(message or "JSON-RPC call was rejected"),
                    code=code if isinstance(code, int) else None,
                    data=error.get("data"),
                )
            raise JsonRpcCallError(str(error))
        result = value.get("result", {})
        if not isinstance(result, dict):
            raise JsonRpcProtocolError("JSON-RPC result must be an object")
        return result


class JsonRpcCaller(Protocol):
    def call(
        self, method: str, params: list[object] | None = None
    ) -> Mapping[str, Any]: ...


__all__ = [
    "JsonRpcCallError",
    "JsonRpcCaller",
    "JsonRpcProtocolError",
    "UnixJsonCommandClient",
    "UnixJsonRpcClient",
]
