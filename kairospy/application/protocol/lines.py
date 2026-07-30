from __future__ import annotations

import asyncio
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


class MergedRuntimeEventLine:
    def __init__(self, lines: Iterable[RuntimeEventLine]) -> None:
        self.lines = tuple(lines)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        iterators = [line.events().__aiter__() for line in self.lines]
        tasks = {asyncio.create_task(iterator.__anext__()): iterator for iterator in iterators}
        try:
            while tasks:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    iterator = tasks.pop(task)
                    try:
                        yield task.result()
                    except StopAsyncIteration:
                        continue
                    tasks[asyncio.create_task(iterator.__anext__())] = iterator
        finally:
            for task in tasks:
                task.cancel()
            for iterator in iterators:
                await close_event_line(iterator)


async def close_event_line(iterator: AsyncIterator[object]) -> None:
    close = getattr(iterator, "aclose", None)
    if close is not None:
        await close()


__all__ = [
    "RuntimeEventLine",
    "RuntimeLine",
    "MergedRuntimeEventLine",
    "close_event_line",
]
