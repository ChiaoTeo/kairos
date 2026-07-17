from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass
from enum import StrEnum
from typing import AsyncIterator, Generic, Iterable, Protocol, TypeVar


T = TypeVar("T")


class EventSource(Protocol[T]):
    def events(self) -> AsyncIterator[T]: ...


class OverflowPolicy(StrEnum):
    BLOCK_PRODUCER = "block_producer"
    DROP_OLDEST_WITH_GAP = "drop_oldest_with_gap"
    CONFLATE_LATEST = "conflate_latest"
    FAIL_STREAM = "fail_stream"


class StreamClosed(RuntimeError):
    pass


class StreamOverflow(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ConsumerGap:
    dropped: int
    producer_sequence: int
    consumer_sequence: int


@dataclass(frozen=True, slots=True)
class ChannelMetrics:
    capacity: int
    depth: int
    published: int
    consumed: int
    dropped: int
    conflated: int
    closed: bool
    peak_depth: int


class IterableEventSource(Generic[T]):
    """Deterministic asynchronous adapter over an existing finite iterable."""

    def __init__(self, values: Iterable[T]) -> None:
        self.values = values

    async def events(self) -> AsyncIterator[T]:
        for value in self.values:
            yield value
            await asyncio.sleep(0)


class BoundedEventChannel(Generic[T]):
    """Single-consumer bounded channel with explicit overflow evidence."""

    def __init__(self, capacity: int, *, overflow: OverflowPolicy = OverflowPolicy.BLOCK_PRODUCER) -> None:
        if capacity < 1:
            raise ValueError("channel capacity must be positive")
        if overflow is OverflowPolicy.CONFLATE_LATEST:
            raise ValueError("use ConflatedLatestChannel for key-based conflation")
        self.capacity = capacity
        self.overflow = overflow
        self._values: deque[T] = deque()
        self._condition = asyncio.Condition()
        self._published = 0
        self._consumed = 0
        self._dropped = 0
        self._closed = False
        self._pending_gap = 0
        self._peak_depth = 0

    async def publish(self, value: T) -> None:
        async with self._condition:
            if self._closed:
                raise StreamClosed("cannot publish to a closed channel")
            if self.overflow is OverflowPolicy.BLOCK_PRODUCER:
                await self._condition.wait_for(lambda: len(self._values) < self.capacity or self._closed)
                if self._closed:
                    raise StreamClosed("cannot publish to a closed channel")
            elif len(self._values) >= self.capacity:
                if self.overflow is OverflowPolicy.FAIL_STREAM:
                    raise StreamOverflow("bounded channel is full")
                self._values.popleft()
                self._dropped += 1
                self._pending_gap += 1
            self._values.append(value)
            self._peak_depth = max(self._peak_depth, len(self._values))
            self._published += 1
            self._condition.notify_all()

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    async def events(self) -> AsyncIterator[T | ConsumerGap]:
        while True:
            async with self._condition:
                await self._condition.wait_for(lambda: bool(self._values) or self._closed)
                if not self._values and self._closed:
                    return
                value = self._values.popleft()
                pending_gap = self._pending_gap
                self._pending_gap = 0
                self._condition.notify_all()
            if pending_gap:
                yield ConsumerGap(pending_gap, self._published, self._consumed)
            self._consumed += 1
            yield value

    @property
    def metrics(self) -> ChannelMetrics:
        return ChannelMetrics(
            self.capacity, len(self._values), self._published, self._consumed,
            self._dropped, 0, self._closed,
            self._peak_depth,
        )


K = TypeVar("K")


class ConflatedLatestChannel(Generic[K, T]):
    """Bounded latest-value channel; replacement is observable through metrics."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("channel capacity must be positive")
        self.capacity = capacity
        self._values: OrderedDict[K, T] = OrderedDict()
        self._condition = asyncio.Condition()
        self._published = 0
        self._consumed = 0
        self._dropped = 0
        self._conflated = 0
        self._closed = False
        self._peak_depth = 0

    async def publish(self, key: K, value: T) -> None:
        async with self._condition:
            if self._closed:
                raise StreamClosed("cannot publish to a closed channel")
            if key in self._values:
                self._values.pop(key)
                self._conflated += 1
            elif len(self._values) >= self.capacity:
                self._values.popitem(last=False)
                self._dropped += 1
            self._values[key] = value
            self._peak_depth = max(self._peak_depth, len(self._values))
            self._published += 1
            self._condition.notify()

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    async def events(self) -> AsyncIterator[T]:
        while True:
            async with self._condition:
                await self._condition.wait_for(lambda: bool(self._values) or self._closed)
                if not self._values and self._closed:
                    return
                _, value = self._values.popitem(last=False)
            self._consumed += 1
            yield value

    @property
    def metrics(self) -> ChannelMetrics:
        return ChannelMetrics(
            self.capacity, len(self._values), self._published, self._consumed,
            self._dropped, self._conflated, self._closed,
            self._peak_depth,
        )
