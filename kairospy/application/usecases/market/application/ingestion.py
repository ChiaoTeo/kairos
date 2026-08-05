"""Market ingestion capability exposed to system application code."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from pathlib import Path

from kairospy.application.usecases.market.application.resolver import ResolvedMarketData
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.protocol import MarketDataWriter
from kairospy.application.usecases.market.services.ingestion import MarketIngestionService as _MarketIngestionService
from kairospy.application.support.messaging import Message
from kairospy.application.usecases.market.application.requests import MarketDataRow
from kairospy.application.usecases.market.application.requests import MarketOptions
from kairospy.application.usecases.market.protocol import MarketHistoricalClient
from kairospy.domain.market import Bar, MarketEvent, RateObservation


class MarketIngestionApplicationService:
    """Normalizes and persists market data through the market application."""

    def __init__(self, writer: MarketDataWriter | None = None) -> None:
        self._writer = writer
        self._events = _MarketIngestionService()

    def event_from_message(self, message: Message) -> MarketEvent | None:
        return self._events.event_from_message(message)

    def event_from_row(self, row: MarketDataRow, *, sequence: int, stream: str) -> MarketEvent | None:
        return self._events.event_from_row(row, sequence=sequence, stream=stream)

    def download(
        self,
        spec: MarketDataSpec,
        client: MarketHistoricalClient,
        *,
        mode: str = "append",
        options: MarketOptions | None = None,
    ) -> Path:
        if self._writer is None:
            raise RuntimeError("market ingestion application requires a data application service")
        return self._writer.download(spec, client, mode=mode, options=options)

    def persist_historical(
        self,
        spec: MarketDataSpec,
        observations: Iterable[Bar | RateObservation],
        *,
        mode: str = "append",
    ) -> Path:
        if self._writer is None:
            raise RuntimeError("market ingestion application requires a data application service")
        return self._writer.persist_historical(spec, observations, mode=mode)

    async def persist(
        self,
        spec: MarketDataSpec,
        events: AsyncIterable[MarketEvent],
        *,
        limit: int | None = None,
    ) -> int:
        if self._writer is None:
            raise RuntimeError("market ingestion application requires a data application service")
        return await self._writer.persist(spec, events, limit=limit)

    def ensure(
        self,
        spec: MarketDataSpec,
        client: MarketHistoricalClient | None = None,
        *,
        mode: str = "append",
        options: MarketOptions | None = None,
    ) -> ResolvedMarketData:
        if self._writer is None:
            raise RuntimeError("market ingestion application requires a data application service")
        return self._writer.ensure(spec, client, mode=mode, options=options)


__all__ = ["MarketIngestionApplicationService"]
