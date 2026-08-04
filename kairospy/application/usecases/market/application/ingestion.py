"""Market ingestion capability exposed to system application code."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from pathlib import Path

from kairospy.application.usecases.market.application.resolver import ResolvedMarketData
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.usecases.market.protocol import MarketDataWriter
from kairospy.application.usecases.market.services.ingestion import MarketIngestionService as _MarketIngestionService


class MarketIngestionApplicationService:
    """Normalizes and persists market data through the market application."""

    def __init__(self, writer: MarketDataWriter | None = None) -> None:
        self._writer = writer
        self._events = _MarketIngestionService()

    def event_from_message(self, message: object) -> object | None:
        return self._events.event_from_message(message)  # type: ignore[arg-type]

    def event_from_row(self, row: dict[str, object], *, sequence: int, stream: str) -> object | None:
        return self._events.event_from_row(row, sequence=sequence, stream=stream)

    def download(
        self,
        spec: MarketDataSpec,
        client: object,
        *,
        mode: str = "append",
        options: Mapping[str, object] | None = None,
    ) -> Path:
        if self._writer is None:
            raise RuntimeError("market ingestion application requires a data application service")
        return self._writer.download(spec, client, mode=mode, options=options)

    def persist_historical(
        self,
        spec: MarketDataSpec,
        observations: Iterable[object],
        *,
        mode: str = "append",
    ) -> Path:
        if self._writer is None:
            raise RuntimeError("market ingestion application requires a data application service")
        return self._writer.persist_historical(spec, observations, mode=mode)

    async def persist(
        self,
        spec: MarketDataSpec,
        events: AsyncIterable[Mapping[str, object]],
        *,
        limit: int | None = None,
    ) -> int:
        if self._writer is None:
            raise RuntimeError("market ingestion application requires a data application service")
        return await self._writer.persist(spec, events, limit=limit)

    def ensure(
        self,
        spec: MarketDataSpec,
        client: object | None = None,
        *,
        mode: str = "append",
        options: Mapping[str, object] | None = None,
    ) -> ResolvedMarketData:
        if self._writer is None:
            raise RuntimeError("market ingestion application requires a data application service")
        return self._writer.ensure(spec, client, mode=mode, options=options)


__all__ = ["MarketIngestionApplicationService"]
