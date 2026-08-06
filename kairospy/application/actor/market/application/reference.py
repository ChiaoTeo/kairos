"""Reference catalog refresh Actor.

The catalog is shared business state for market consumers, but its refresh
lifecycle is independent from any one market feed or provider.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Mapping

from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.support.messaging import Message, MessageBus
from kairospy.application.usecases.reference.application.component import ReferenceApplication


_LOGGER = logging.getLogger("kairospy.actor.reference")


class ReferenceActor(BusinessActor):
    def __init__(self, reference: ReferenceApplication, bus: MessageBus, *, poll_interval_seconds: float = 300.0) -> None:
        super().__init__("reference", bus=bus)
        if poll_interval_seconds <= 0:
            raise ValueError("reference poll interval must be positive")
        self.reference = reference
        self.bus = bus
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._refresh_task: asyncio.Task[None] | None = None
        self._sequence = 0

    async def start(self) -> None:
        _LOGGER.info("actor=reference state=starting source=%s", bool(getattr(self.reference, "has_source", lambda: False)()))
        await super().start()
        if bool(getattr(self.reference, "has_source", lambda: False)()):
            self._stop_event.clear()
            self._refresh_task = asyncio.create_task(self._refresh_loop(), name="actor:reference.refresh")
            _LOGGER.info("actor=reference refresh_loop=started interval_seconds=%s", self.poll_interval_seconds)

    async def stop(self) -> None:
        _LOGGER.info("actor=reference state=stopping")
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
        has_catalog = getattr(self.reference, "has_catalog", None)
        if callable(has_catalog) and has_catalog():
            _LOGGER.info("actor=reference refresh=skipped reason=cached_catalog")
        else:
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
        try:
            result = await asyncio.to_thread(self.reference.bootstrap, as_of=datetime.now(timezone.utc))
        except asyncio.CancelledError:
            _LOGGER.info("actor=reference refresh=cancelled")
            raise
        except Exception as error:
            _LOGGER.exception(
                "actor=reference refresh=failed error_type=%s reason=%s",
                type(error).__name__,
                error,
            )
            return
        changed = bool(getattr(result, "events", ())) or getattr(result, "previous_markets", ()) != getattr(result, "current_markets", ())
        if result is None or not changed:
            _LOGGER.info("actor=reference refresh=unchanged")
            return
        self._sequence += 1
        await self.bus.publish(Message(topic="reference.catalog.changed", payload={"events": result.events, "previous_markets": result.previous_markets, "current_markets": result.current_markets}, published_at=getattr(result, "as_of", datetime.now(timezone.utc)), producer="reference.actor", producer_sequence=self._sequence))
        _LOGGER.info("actor=reference refresh=changed events=%d", len(result.events))


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
