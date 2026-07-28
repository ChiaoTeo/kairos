from __future__ import annotations

from typing import AsyncIterator, Iterable, Protocol

from kairospy.application.runtime.model import RuntimeDataEnvelope


class EventSource(Protocol):
    def events(self) -> Iterable[RuntimeDataEnvelope]:
        ...


class AsyncEventSource(Protocol):
    def events(self) -> AsyncIterator[RuntimeDataEnvelope]:
        ...


async def close_async_iterator(iterator: object) -> None:
    close = getattr(iterator, "aclose", None)
    if not callable(close):
        close = getattr(iterator, "close", None)
    if not callable(close):
        return
    result = close()
    if hasattr(result, "__await__"):
        await result


__all__ = ["AsyncEventSource", "EventSource", "close_async_iterator"]
