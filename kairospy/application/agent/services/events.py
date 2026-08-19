from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Lock

from kairospy.domain_types import EventMetadata

from ..models import (
    AgentDecisionNotice,
    AgentEvent,
    AgentEventStatus,
    DecisionReceipt,
    IntentCandidate,
)


class AgentEventStream:
    """Bounded thread-to-async bridge for best-effort Strategy notifications."""

    def __init__(self, *, enabled: bool, capacity: int = 256) -> None:
        if capacity <= 0:
            raise ValueError("Agent event capacity must be positive")
        self._enabled = enabled
        self._queue: Queue[AgentEvent] = Queue(maxsize=capacity)
        self._lock = Lock()
        self._sequence = 0
        self._closed = False
        self._published: set[str] = set()
        self._published_order: deque[str] = deque()

    def publish(
        self,
        candidate: IntentCandidate,
        receipt: DecisionReceipt,
        reason_codes: tuple[str, ...],
    ) -> None:
        if not self._enabled:
            return
        try:
            status = AgentEventStatus(receipt.status.value)
        except ValueError:
            return
        with self._lock:
            if self._closed or candidate.decision_id in self._published:
                return
            self._remember(candidate.decision_id)
            self._sequence += 1
            sequence = self._sequence
        now = (
            candidate.submitted_at
            if candidate.runtime == "fixture"
            else datetime.now(timezone.utc)
        )
        event = AgentEvent(
            AgentDecisionNotice(
                decision_id=candidate.decision_id,
                capability=candidate.snapshot.capability,
                status=status,
                reason_codes=reason_codes,
            ),
            EventMetadata(
                stream_id=f"agent.decisions:{candidate.instance_id}",
                sequence=sequence,
                producer="strategy.agent",
                occurred_at=now,
                occurred_at_unix_nanos=int(now.timestamp() * 1_000_000_000),
                causation_id=candidate.request_id,
            ),
        )
        try:
            self._queue.put_nowait(event)
        except Full:
            # This surface is explicitly best-effort. Preserve recent terminal
            # notices without ever blocking the Decision worker.
            try:
                self._queue.get_nowait()
            except Empty:
                pass
            self._queue.put_nowait(event)

    async def events(self):
        while True:
            try:
                event = self._queue.get_nowait()
            except Empty:
                with self._lock:
                    if self._closed:
                        return
                await asyncio.sleep(0.01)
                continue
            yield event

    def drain(self) -> tuple[AgentEvent, ...]:
        events: list[AgentEvent] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except Empty:
                return tuple(events)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def _remember(self, decision_id: str) -> None:
        self._published.add(decision_id)
        self._published_order.append(decision_id)
        if len(self._published_order) > 4096:
            expired = self._published_order.popleft()
            self._published.discard(expired)


__all__ = ["AgentEventStream"]
