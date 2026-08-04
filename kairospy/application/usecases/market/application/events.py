"""Application boundary for processing canonical market events."""

from __future__ import annotations

from typing import Protocol

from kairospy.application.usecases.market.application.ingestion import MarketIngestionApplicationService
from kairospy.application.usecases.market.application.projections import MarketProjectionApplicationService
from kairospy.domain.market import MarketEvent


class MarketEventSink(Protocol):
    """Persistence port for canonical market events."""

    def append(self, event: MarketEvent) -> None:
        ...


class MarketEventApplicationService:
    """Processes one event for market state and downstream persistence.

    Integration and replay may use different transports, but once an event
    reaches this boundary it follows the same projection path. Runtime and
    connection lifecycle remain owned by System.
    """

    def __init__(
        self,
        *,
        ingestion: MarketIngestionApplicationService,
        projection: MarketProjectionApplicationService,
        sink: MarketEventSink | None = None,
    ) -> None:
        self.ingestion = ingestion
        self.projection = projection
        self.sink = sink

    def handle(self, message: object) -> MarketEvent | None:
        event = self.ingestion.event_from_message(message)
        if event is None:
            return None
        self.projection.apply(event)
        if self.sink is not None:
            self.sink.append(event)
        return event


__all__ = ["MarketEventApplicationService", "MarketEventSink"]
