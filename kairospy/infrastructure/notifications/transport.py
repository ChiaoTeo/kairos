from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class NotificationTransportError(RuntimeError):
    """An external notification request was rejected or could not complete."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class HttpNotificationTransport:
    """Small injectable HTTP boundary shared by notification adapters."""

    def __init__(self, client: httpx.AsyncClient | None = None, *, timeout: float = 10.0) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def post_json(self, url: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not url.strip():
            raise ValueError("notification endpoint URL is required")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        client = self._client
        try:
            response = await client.post(url, json=dict(payload))
            if response.status_code >= 500:
                raise NotificationTransportError(
                    f"notification endpoint returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            if response.status_code >= 400:
                raise NotificationTransportError(
                    f"notification endpoint rejected request with HTTP {response.status_code}",
                    status_code=response.status_code,
                    retryable=False,
                )
            try:
                data = response.json()
            except ValueError as error:
                raise NotificationTransportError("notification endpoint returned invalid JSON", status_code=response.status_code, retryable=False) from error
            if not isinstance(data, Mapping):
                raise NotificationTransportError("notification endpoint returned a non-object JSON response", status_code=response.status_code, retryable=False)
            return data
        except httpx.HTTPError as error:
            raise NotificationTransportError(f"notification HTTP request failed: {type(error).__name__}") from error

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["HttpNotificationTransport", "NotificationTransportError"]
