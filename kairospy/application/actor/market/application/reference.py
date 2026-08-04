"""Reference refresh owned by the market Actor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Mapping

from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.support.messaging import Message, MessageBus


class ReferenceActor(BusinessActor):
    def __init__(self, reference: object, bus: MessageBus, *, poll_interval_seconds: float = 300.0) -> None:
        super().__init__("reference")
        if poll_interval_seconds <= 0:
            raise ValueError("reference poll interval must be positive")
        self.reference = reference
        self.bus = bus
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._refresh_task: asyncio.Task[None] | None = None
        self._sequence = 0

    async def start(self) -> None:
        await super().start()
        if bool(getattr(self.reference, "has_source", lambda: False)()):
            self._stop_event.clear()
            self._refresh_task = asyncio.create_task(self._refresh_loop(), name="actor:market.reference.refresh")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
            self._refresh_task = None
        await super().stop()

    async def process(self, message: Message) -> None:
        if message.topic == "reference.refresh.requested":
            await self._refresh_once()

    async def _refresh_loop(self) -> None:
        await self._refresh_once()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
            if not self._stop_event.is_set():
                await self._refresh_once()

    async def _refresh_once(self) -> None:
        if not bool(getattr(self.reference, "has_source", lambda: False)()):
            return
        result = self.reference.bootstrap(as_of=datetime.now(timezone.utc))
        changed = bool(getattr(result, "events", ())) or getattr(result, "previous_markets", ()) != getattr(result, "current_markets", ())
        if result is None or not changed:
            return
        self._sequence += 1
        await self.bus.publish(Message(topic="reference.catalog.changed", payload={"events": result.events, "previous_markets": result.previous_markets, "current_markets": result.current_markets}, published_at=getattr(result, "as_of", datetime.now(timezone.utc)), producer="market.actor", producer_sequence=self._sequence))


def reference_poll_interval(config: Mapping[str, object] | None) -> float:
    section = None if config is None else config.get("reference")
    if not isinstance(section, Mapping):
        return 300.0
    try:
        interval = float(section.get("refresh_interval_seconds", 300.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("reference.refresh_interval_seconds must be numeric") from exc
    if interval <= 0:
        raise ValueError("reference.refresh_interval_seconds must be positive")
    return interval


__all__ = ["ReferenceActor", "reference_poll_interval"]
