from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import AsyncIterator, Mapping, Protocol


@dataclass(slots=True)
class StreamSubscription:
    stream: str
    _feed: "InMemoryStreamFeed"
    _queue: asyncio.Queue[dict[str, object] | None]
    _closed: bool = False

    def __aiter__(self) -> "StreamSubscription":
        return self

    async def __anext__(self) -> dict[str, object]:
        event = await self._queue.get()
        if event is None:
            self.close()
            raise StopAsyncIteration
        return event

    def close(self) -> None:
        if not self._closed:
            self._feed._remove(self.stream, self._queue)
            self._closed = True


class StreamFeed(Protocol):
    def subscribe(self, stream: str) -> AsyncIterator[dict[str, object]]:
        ...


class InMemoryStreamFeed:
    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[dict[str, object] | None]]] = defaultdict(list)

    def subscribe(self, stream: str) -> StreamSubscription:
        stream_name = _clean_stream(stream)
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        self._queues[stream_name].append(queue)
        return StreamSubscription(stream_name, self, queue)

    async def publish(self, stream: str, event: Mapping[str, object]) -> None:
        queues = tuple(self._queues.get(_clean_stream(stream), ()))
        for queue in queues:
            await queue.put(dict(event))

    async def close(self, stream: str | None = None) -> None:
        streams = tuple(self._queues) if stream is None else (_clean_stream(stream),)
        for item in streams:
            queues = tuple(self._queues.get(item, ()))
            for queue in queues:
                await queue.put(None)

    def _remove(self, stream: str, queue: asyncio.Queue[dict[str, object] | None]) -> None:
        queues = self._queues.get(stream)
        if queues is None:
            return
        if queue in queues:
            queues.remove(queue)
        if not queues:
            del self._queues[stream]


def _clean_stream(stream: str) -> str:
    value = str(stream).strip()
    if not value:
        raise ValueError("data stream cannot be empty")
    return value


__all__ = [
    "InMemoryStreamFeed",
    "StreamFeed",
    "StreamSubscription",
]
