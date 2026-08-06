"""Native Binance Options market-data stream transport."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from kairospy.infrastructure.integrations.services.drivers.websocket import WebSocketDriver


_CLOSED = object()


@dataclass(slots=True)
class BinanceOptionsMarketStream:
    """One multiplexed Binance Options stream with reference-counted channels."""

    endpoint: str = field(
        default_factory=lambda: os.getenv(
            "BINANCE_OPTIONS_WS_URL", "wss://nbstream.binance.com/eoptions/stream"
        )
    )
    driver: WebSocketDriver = field(default_factory=WebSocketDriver)
    _session: Any | None = field(default=None, init=False, repr=False)
    _reader: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _queues: dict[str, tuple[str, set[str], asyncio.Queue[object]]] = field(default_factory=dict, init=False, repr=False)
    _stream_references: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _request_id: int = field(default=0, init=False, repr=False)

    async def start(self) -> None:
        return None

    async def subscribe(self, symbol: str, channels: set[str]) -> str:
        normalized = symbol.strip().upper()
        normalized_channels = {channel.strip() for channel in channels if channel.strip()}
        if not normalized or not normalized_channels:
            raise ValueError("Binance Options subscription requires a symbol and channel")
        await self._ensure_connected()
        subscription_id = f"binance-options-{uuid4().hex}"
        self._queues[subscription_id] = (normalized, normalized_channels, asyncio.Queue())
        new_streams: list[str] = []
        for channel in normalized_channels:
            stream = f"{normalized.lower()}@{channel}"
            if self._stream_references.get(stream, 0) == 0:
                new_streams.append(stream)
            self._stream_references[stream] = self._stream_references.get(stream, 0) + 1
        if new_streams:
            await self._send_control("SUBSCRIBE", sorted(new_streams))
        return subscription_id

    async def events(self, subscription_id: str):
        entry = self._queues.get(subscription_id)
        if entry is None:
            raise KeyError(f"Binance Options subscription not found: {subscription_id}")
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
        _symbol, channels, queue = entry
        queue.put_nowait(_CLOSED)
        released: list[str] = []
        for channel in channels:
            stream = f"{_symbol.lower()}@{channel}"
            count = self._stream_references.get(stream, 0) - 1
            if count <= 0:
                self._stream_references.pop(stream, None)
                released.append(stream)
            else:
                self._stream_references[stream] = count
        if self._session is not None and released:
            await self._send_control("UNSUBSCRIBE", sorted(released))
        if not self._queues:
            await self.stop()

    async def reconnect(self) -> None:
        active_streams = tuple(sorted(self._stream_references))
        await self._close_session()
        self._reader = None
        if not active_streams:
            return
        await self._ensure_connected()
        await self._send_control("SUBSCRIBE", list(active_streams))

    async def stop(self) -> None:
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        await self._close_session()
        for _symbol, _channels, queue in self._queues.values():
            queue.put_nowait(_CLOSED)
        self._queues.clear()
        self._stream_references.clear()

    async def _ensure_connected(self) -> None:
        if self._session is not None:
            return
        self._session = await self.driver.connect(self.endpoint)
        self._reader = asyncio.create_task(self._read_messages(), name="binance-options-stream-reader")

    async def _send_control(self, method: str, streams: list[str]) -> None:
        if self._session is None:
            raise RuntimeError("Binance Options WebSocket is not connected")
        self._request_id += 1
        message = {"method": method, "params": streams, "id": self._request_id}
        send = getattr(self._session, "send", None)
        if not callable(send):
            raise TypeError("Binance Options WebSocket session does not support send()")
        result = send(json.dumps(message))
        if inspect.isawaitable(result):
            await result

    async def _close_session(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        close = getattr(session, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    async def _read_messages(self) -> None:
        session = self._session
        if session is None:
            return
        try:
            async for message in session:
                value = json.loads(message) if isinstance(message, str) else message
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
        # Combined streams wrap the actual event in {stream, data}; raw
        # streams carry the event directly.
        stream_name = str(value.get("stream") or "")
        payload = value.get("data") if stream_name else value
        if not isinstance(payload, dict):
            return
        if not stream_name:
            symbol = str(payload.get("s") or "").upper()
            event = str(payload.get("e") or "")
            stream_name = f"{symbol.lower()}@{event}" if symbol and event else ""
        if not stream_name or "@" not in stream_name:
            return
        symbol, channel = stream_name.split("@", 1)
        for wanted_symbol, channels, queue in self._queues.values():
            if wanted_symbol.casefold() == symbol.casefold() and channel in channels:
                queue.put_nowait(payload)


__all__ = ["BinanceOptionsMarketStream"]
