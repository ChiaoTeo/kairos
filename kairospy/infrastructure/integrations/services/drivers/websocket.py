from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class WebSocketDriver:
    ping_interval_seconds: float = 20.0
    max_connection_seconds: float = 24 * 60 * 60
    connector: Callable[[str], Awaitable[Any]] | None = None

    def __post_init__(self) -> None:
        if self.ping_interval_seconds <= 0 or self.max_connection_seconds <= 0:
            raise ValueError("WebSocket timing values must be positive")

    async def connect(self, url: str, *, headers: Mapping[str, str] | None = None) -> Any:
        if self.connector is not None:
            return await self.connector(url)
        try:
            from websockets.asyncio.client import connect
        except ImportError as error:
            raise RuntimeError(
                "Binance WebSocket support requires the crypto-realtime optional dependency"
            ) from error
        return await connect(
            url,
            additional_headers=dict(headers or {}),
            ping_interval=self.ping_interval_seconds,
            open_timeout= self.ping_interval_seconds,
            close_timeout= self.ping_interval_seconds,
        )


__all__ = ["WebSocketDriver"]
