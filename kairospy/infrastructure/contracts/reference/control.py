"""Low-frequency Reference control contract."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping
from urllib.parse import urlencode

from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class ReferenceControlClient:
    """JSON-over-Unix control client matching the Reference process API."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout: float = 5.0,
        transport: Callable[..., tuple[int, dict[str, Any]]] | None = None,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._timeout = timeout
        self._transport = transport
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self.request("GET", "/v1/health")

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None = None,
        *,
        params: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> Mapping[str, Any]:
        query = urlencode(
            {
                key: str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in (params or {}).items()
                if value is not None
            }
        )
        target = f"{path}?{query}" if query else path
        client = self._client
        if timeout is not None and timeout != self._timeout:
            client = UnixJsonCommandClient(self._socket_path, timeout=timeout)
        if self._transport is not None:
            if body is None:
                status, value = self._transport(
                    self._socket_path, method, target, timeout=client.timeout
                )
            else:
                status, value = self._transport(
                    self._socket_path,
                    method,
                    target,
                    body,
                    timeout=client.timeout,
                )
        else:
            status, value = client.request(method, target, body)
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", f"Reference request failed: HTTP {status}"))
            )
        return value


__all__ = ["ReferenceControlClient"]
