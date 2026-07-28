from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Protocol

from .events import RuntimeEnvelope


class RuntimeEventLine(Protocol):
    def events(self) -> AsyncIterator[RuntimeEnvelope]:
        ...


class RuntimeLine:
    def __init__(self, items: Iterable[RuntimeEnvelope]) -> None:
        self._items = tuple(items)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        for item in self._items:
            yield item


async def close_event_line(iterator: AsyncIterator[object]) -> None:
    close = getattr(iterator, "aclose", None)
    if close is not None:
        await close()


__all__ = [
    "RuntimeEventLine",
    "RuntimeLine",
    "close_event_line",
]
