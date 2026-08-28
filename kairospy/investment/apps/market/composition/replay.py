"""Replay-process transport adapter for length-prefixed Market events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import struct

from kairospy.contracts.market.events import MarketEventVariant, decode_event


class UnixMarketEventStream:
    replayable = True

    def __init__(self, socket_path: str | Path, *, reconnect_delay: float = 0.25) -> None:
        self.socket_path = Path(socket_path)
        if reconnect_delay < 0:
            raise ValueError("reconnect_delay cannot be negative")
        self.reconnect_delay = reconnect_delay

    async def replay_from(
        self, after_sequence: int = 0
    ) -> AsyncIterator[MarketEventVariant]:
        cursor = max(0, after_sequence)
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(self.socket_path)
            except (FileNotFoundError, ConnectionError, OSError):
                await asyncio.sleep(self.reconnect_delay)
                continue
            try:
                while True:
                    (length,) = struct.unpack(">I", await reader.readexactly(4))
                    if length == 0 or length > 4 * 1024 * 1024:
                        raise ValueError("invalid market event frame length")
                    event = decode_event(await reader.readexactly(length))
                    if event.metadata.sequence > cursor:
                        cursor = int(event.metadata.sequence)
                        yield event
            except asyncio.IncompleteReadError:
                return
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass


__all__ = ["UnixMarketEventStream"]
