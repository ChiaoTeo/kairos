from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import AsyncIterator, Mapping

from ..domain.lifecycle import StrategyLifecycle
from ..domain.messages import (
    CommandHandle,
    ContextRequest,
    EventEnvelope,
    LifecycleRecord,
    SnapshotEnvelope,
    StrategySignal,
)


class InMemoryContextBus:
    """Deterministic composition fake for Context Bus tests."""

    def __init__(self) -> None:
        self.requests: list[ContextRequest] = []
        self.signals: list[StrategySignal] = []
        self._handles: dict[str, CommandHandle] = {}

    def submit(self, request: ContextRequest) -> CommandHandle:
        self.requests.append(request)
        status = "pending" if request.operation == "market.subscribe" else "accepted"
        handle = CommandHandle(request.request_id, status)
        self._handles[request.request_id] = handle
        return handle

    def publish_signal(self, signal: StrategySignal) -> CommandHandle:
        self.signals.append(signal)
        request_id = f"{signal.strategy_id}:signal:{len(self.signals)}"
        handle = CommandHandle(request_id, "accepted")
        self._handles[request_id] = handle
        return handle

    def status(self, request_id: str) -> CommandHandle:
        return self._handles.get(request_id, CommandHandle(request_id, "missing", error="request not found"))

    def resolve(self, request_id: str, *, status: str = "ready", result: Mapping[str, object] | None = None, error: str | None = None) -> None:
        if request_id not in self._handles:
            raise KeyError(request_id)
        self._handles[request_id] = CommandHandle(request_id, status, result or {}, error)


class InMemorySnapshotReader:
    def __init__(self, snapshots: Mapping[str, SnapshotEnvelope] = ()) -> None:
        self.snapshots = dict(snapshots)

    def read(self, view_key: str) -> SnapshotEnvelope:
        return self.snapshots[view_key]


class InMemoryEventStream:
    def __init__(self, stream_id: str, *, first_sequence: int = 1) -> None:
        self.stream_id = stream_id
        self.first_sequence = first_sequence
        self._events: deque[EventEnvelope] = deque()
        self._waiters: list[asyncio.Future[None]] = []

    def can_join(self, event_sequence: int) -> bool:
        return event_sequence >= self.first_sequence - 1

    def append(self, event: EventEnvelope) -> None:
        if event.stream_id != self.stream_id:
            raise ValueError("event belongs to a different stream")
        self._events.append(event)
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(None)
        self._waiters.clear()

    async def events(self, after_sequence: int = 0) -> AsyncIterator[EventEnvelope]:
        next_sequence = after_sequence + 1
        while True:
            while self._events and self._events[0].sequence < next_sequence:
                self._events.popleft()
            if self._events and self._events[0].sequence == next_sequence:
                event = self._events.popleft()
                next_sequence += 1
                yield event
                continue
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            await waiter


class InMemoryLifecycleJournal:
    def __init__(self) -> None:
        self.records: list[LifecycleRecord] = []

    def append(self, record: LifecycleRecord) -> None:
        self.records.append(record)

