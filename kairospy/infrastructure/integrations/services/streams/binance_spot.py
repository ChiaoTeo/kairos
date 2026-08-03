from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import AsyncIterator
import json
import inspect

from kairospy.infrastructure.integrations.services.drivers.websocket import WebSocketDriver
from kairospy.infrastructure.integrations.services.endpoints.binance_spot import (
    BinanceSpotEndpoint,
    BinanceSpotEndpointKind,
)


@dataclass(slots=True)
class BinanceSpotMarketStream:
    endpoint: BinanceSpotEndpoint = field(
        default_factory=lambda: BinanceSpotEndpoint(
            BinanceSpotEndpointKind.MARKET_STREAM,
            "wss://stream.binance.com:9443/ws",
        )
    )
    driver: WebSocketDriver = field(default_factory=WebSocketDriver)
    _sessions: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    async def start(self) -> None:
        return None

    async def reconnect(self) -> None:
        await self.stop()

    async def events(self, symbol: str, channel: str = "trade") -> AsyncIterator[dict[str, object]]:
        stream = f"{symbol.lower()}@{channel}"
        session = self._sessions.get(stream)
        if session is None:
            session = await self.driver.connect(f"{self.endpoint.base_url.rstrip('/')}/{stream}")
            self._sessions[stream] = session
        try:
            async for message in session:
                value = json.loads(message) if isinstance(message, str) else message
                if isinstance(value, dict):
                    yield value
        finally:
            await _close(session)
            self._sessions.pop(stream, None)

    async def stop(self) -> None:
        for session in tuple(self._sessions.values()):
            await _close(session)
        self._sessions.clear()


@dataclass(slots=True)
class BinanceSpotUserStream:
    endpoint: BinanceSpotEndpoint = field(
        default_factory=lambda: BinanceSpotEndpoint(
            BinanceSpotEndpointKind.USER_STREAM,
            "wss://stream.binance.com:9443/ws",
        )
    )
    driver: WebSocketDriver = field(default_factory=WebSocketDriver)
    _session: object | None = field(default=None, init=False, repr=False)

    async def start(self) -> None:
        return None

    async def reconnect(self) -> None:
        await self.stop()

    async def events(self, listen_key: str) -> AsyncIterator[dict[str, object]]:
        if self._session is None:
            self._session = await self.driver.connect(
                f"{self.endpoint.base_url.rstrip('/')}/{listen_key}"
            )
        try:
            async for message in self._session:
                value = json.loads(message) if isinstance(message, str) else message
                if isinstance(value, dict):
                    yield value
        finally:
            await _close(self._session)
            self._session = None

    async def stop(self) -> None:
        await _close(self._session)
        self._session = None


async def _close(session: object | None) -> None:
    if session is None:
        return
    close = getattr(session, "close", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result


__all__ = ["BinanceSpotMarketStream", "BinanceSpotUserStream"]
