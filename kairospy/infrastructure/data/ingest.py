from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterable, Iterable, Mapping

from .store import DataStore


@dataclass(frozen=True, slots=True)
class DataSink:
    store: DataStore
    dataset: str

    def write(self, events: Iterable[Mapping[str, object]]) -> None:
        self.store.write(self.dataset, events)

    async def consume(self, events: AsyncIterable[Mapping[str, object]], *, limit: int | None = None) -> int:
        count = 0
        async for event in events:
            self.store.write(self.dataset, (event,))
            count += 1
            if limit is not None and count >= limit:
                break
        return count


__all__ = ["DataSink"]
