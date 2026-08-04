from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import dataclass, field
from uuid import uuid4

from kairospy.infrastructure.integrations.services.drivers.websocket import WebSocketDriver


_CLOSED = object()


@dataclass(slots=True)
class MassiveStockMarketStream:
    """Private Massive transport for one Stocks WebSocket connection."""

    endpoint: str = field(
        default_factory=lambda: os.getenv(
            "MASSIVE_WS_URL", "wss://socket.massiveprivateserver.site/stocks"
        )
    )
    api_key: str | None = None
    driver: WebSocketDriver = field(default_factory=WebSocketDriver)
    _session: object | None = field(default=None, init=False, repr=False)
    _reader: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _queues: dict[str, tuple[str, set[str], asyncio.Queue[object]]] = field(default_factory=dict, init=False, repr=False)

    async def start(self) -> None:
        return None

    async def subscribe(self, symbol: str, channels: set[str]) -> str:
        symbol = symbol.strip().upper()
        channels = {channel.strip().upper() for channel in channels if channel.strip()}
        if not symbol or not channels:
            raise ValueError("Massive subscription requires a symbol and at least one channel")
        await self._ensure_connected()
        subscription_id = f"massive-{uuid4().hex}"
        self._queues[subscription_id] = (symbol, channels, asyncio.Queue())
        await self._send({"action": "subscribe", "params": ",".join(f"{channel}.{symbol}" for channel in sorted(channels))})
        return subscription_id

    async def events(self, subscription_id: str):
        entry = self._queues.get(subscription_id)
        if entry is None:
            raise KeyError(f"Massive subscription not found: {subscription_id}")
        queue = entry[2]
        while True:
            value = await queue.get()
            if value is _CLOSED:
                return
            yield value

    async def unsubscribe(self, subscription_id: str) -> None:
        entry = self._queues.pop(subscription_id, None)
        if entry is None:
            return
        symbol, channels, queue = entry
        queue.put_nowait(_CLOSED)
        still_needed = any(
            wanted_symbol == symbol and bool(wanted_channels & channels)
            for wanted_symbol, wanted_channels, _wanted_queue in self._queues.values()
        )
        if self._session is not None and not still_needed:
            await self._send({"action": "unsubscribe", "params": ",".join(f"{channel}.{symbol}" for channel in sorted(channels))})
        if not self._queues:
            await self.stop()

    async def reconnect(self) -> None:
        subscriptions = tuple(self._queues.items())
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        session, self._session = self._session, None
        if session is not None:
            await _close(session)
        if not subscriptions:
            return
        await self._ensure_connected()
        for _subscription_id, (symbol, channels, _queue) in subscriptions:
            await self._send({"action": "subscribe", "params": ",".join(f"{channel}.{symbol}" for channel in sorted(channels))})

    async def stop(self) -> None:
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        session, self._session = self._session, None
        if session is not None:
            await _close(session)
        for _symbol, _channels, queue in self._queues.values():
            queue.put_nowait(_CLOSED)
        self._queues.clear()

    async def _ensure_connected(self) -> None:
        if self._session is not None:
            return
        if not self.api_key or not self.api_key.strip():
            raise RuntimeError("Massive API key is required for live market data")
        self._session = await self.driver.connect(self.endpoint)
        await self._send({"action": "auth", "params": self.api_key})
        self._reader = asyncio.create_task(self._read_messages())

    async def _send(self, message: dict[str, object]) -> None:
        if self._session is None:
            raise RuntimeError("Massive WebSocket is not connected")
        send = getattr(self._session, "send", None)
        if not callable(send):
            raise TypeError("Massive WebSocket session does not support send()")
        result = send(json.dumps(message))
        if inspect.isawaitable(result):
            await result

    async def _read_messages(self) -> None:
        session = self._session
        if session is None:
            return
        try:
            async for message in session:
                values = json.loads(message) if isinstance(message, str) else message
                if isinstance(values, dict):
                    values = [values]
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, dict):
                        self._publish(value)
        except asyncio.CancelledError:
            raise
        finally:
            if self._session is session:
                self._session = None
            if self._reader is asyncio.current_task():
                self._reader = None
            for _symbol, _channels, queue in self._queues.values():
                queue.put_nowait(_CLOSED)

    def _publish(self, value: dict[str, object]) -> None:
        event = str(value.get("ev") or "").strip().upper()
        symbol = str(value.get("sym") or "").strip().upper()
        if not event or not symbol:
            return
        for wanted_symbol, channels, queue in self._queues.values():
            if wanted_symbol == symbol and event in channels:
                queue.put_nowait(value)


async def _close(session: object) -> None:
    close = getattr(session, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


@dataclass(slots=True)
class MassiveOptionsMarketStream(MassiveStockMarketStream):
    """Private Massive transport for one Options WebSocket connection."""

    endpoint: str = field(
        default_factory=lambda: os.getenv(
            "MASSIVE_OPTIONS_WS_URL", "wss://socket.massiveprivateserver.site/options"
        )
    )


__all__ = ["MassiveOptionsMarketStream", "MassiveStockMarketStream"]
