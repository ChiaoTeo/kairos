from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from kairospy.market.canonical import (
    CanonicalEventEnvelope, MarketEventKind, OrderBookDeltaPayload,
    canonical_from_trading_market_data,
)
from kairospy.identity import InstrumentId
from kairospy.market.types import OrderBookLevel, OrderBookSnapshot
from kairospy.market.capture import CanonicalCaptureWriter
from kairospy.market.projections import CanonicalOrderBookProjection
from kairospy.market.stream import BoundedEventChannel, EventSource

from .rest_transport import BinanceTransport


class OrderBookSnapshotProvider(Protocol):
    async def fetch(self) -> OrderBookSnapshot: ...


class BinanceOrderBookSyncFault(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BinanceOrderBookSnapshotProvider:
    """Fetch a public Binance depth snapshot without account credentials."""

    def __init__(
        self,
        transport: BinanceTransport,
        symbol: str,
        instrument_id: InstrumentId,
        *,
        futures: bool = False,
        inverse: bool = False,
        limit: int = 1000,
    ) -> None:
        if not symbol.strip():
            raise ValueError("Binance order-book symbol cannot be empty")
        if inverse and not futures:
            raise ValueError("inverse Binance order book requires futures=True")
        if limit not in {5, 10, 20, 50, 100, 500, 1000, 5000}:
            raise ValueError("unsupported Binance depth snapshot limit")
        self.transport = transport
        self.symbol = symbol.upper()
        self.instrument_id = instrument_id
        self.futures = futures
        self.inverse = inverse
        self.limit = limit

    async def fetch(self) -> OrderBookSnapshot:
        path = "/dapi/v1/depth" if self.inverse else "/fapi/v1/depth" if self.futures else "/api/v3/depth"
        row = await asyncio.to_thread(
            self.transport.request, "GET", path, {"symbol": self.symbol, "limit": self.limit}, None,
        )
        try:
            sequence = int(row["lastUpdateId"])
            bids = tuple(OrderBookLevel(Decimal(price), Decimal(quantity)) for price, quantity in row["bids"])
            asks = tuple(OrderBookLevel(Decimal(price), Decimal(quantity)) for price, quantity in row["asks"])
        except (KeyError, TypeError, ValueError) as error:
            raise BinanceOrderBookSyncFault("invalid_snapshot", "Binance depth snapshot is malformed") from error
        return OrderBookSnapshot(
            self.instrument_id, bids, asks, sequence, datetime.now(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class BinanceOrderBookSyncMetrics:
    snapshots: int
    deltas_received: int
    deltas_published: int
    stale_deltas: int
    gaps: int
    resyncs: int


class BinanceOrderBookSyncService:
    """Publish only snapshot-aligned Binance depth events to strategy consumers."""

    def __init__(
        self,
        source: EventSource[CanonicalEventEnvelope],
        snapshot_provider: OrderBookSnapshotProvider,
        output: BoundedEventChannel[CanonicalEventEnvelope],
        *,
        source_instance: str,
        stream_id: str,
        canonical_capture: CanonicalCaptureWriter | None = None,
        maximum_resync_attempts: int = 3,
    ) -> None:
        if not source_instance.strip() or not stream_id.strip():
            raise ValueError("Binance order-book sync identity cannot be empty")
        if maximum_resync_attempts < 1:
            raise ValueError("maximum_resync_attempts must be positive")
        self.source = source
        self.snapshot_provider = snapshot_provider
        self.output = output
        self.source_instance = source_instance
        self.stream_id = stream_id
        self.canonical_capture = canonical_capture
        self.maximum_resync_attempts = maximum_resync_attempts
        self.projection = CanonicalOrderBookProjection()
        self._snapshots = self._deltas_received = self._deltas_published = 0
        self._stale_deltas = self._gaps = self._resyncs = 0
        self._last_published_available_time: datetime | None = None

    async def run(self) -> None:
        try:
            await self._publish_snapshot(resync=False)
            async for event in self.source.events():
                if event.kind is not MarketEventKind.ORDER_BOOK_DELTA:
                    continue
                if not isinstance(event.payload, OrderBookDeltaPayload):
                    raise BinanceOrderBookSyncFault("invalid_delta", "depth event requires OrderBookDeltaPayload")
                self._deltas_received += 1
                await self._process_delta(event)
        finally:
            await self.output.close()
            if self.canonical_capture is not None:
                self.canonical_capture.finalize()

    async def _process_delta(self, event: CanonicalEventEnvelope) -> None:
        if "transport_reconnected" in event.flags:
            await self._publish_snapshot(resync=True)
        for attempt in range(self.maximum_resync_attempts + 1):
            current = self.projection.get(event.instrument_id)
            expected = (current.sequence or 0) + 1
            payload = event.payload
            assert isinstance(payload, OrderBookDeltaPayload)
            if payload.last_sequence < expected:
                self._stale_deltas += 1
                return
            if payload.first_sequence <= expected <= payload.last_sequence:
                aligned = self._align_for_consumption(event)
                state = self.projection.apply(aligned)
                if state is None:
                    self._stale_deltas += 1
                    return
                if not state.valid:
                    self._gaps += 1
                    if attempt >= self.maximum_resync_attempts:
                        break
                    await self._publish_snapshot(resync=True)
                    continue
                await self._publish(aligned)
                self._deltas_published += 1
                return
            self._gaps += 1
            self.projection.apply(event)
            if attempt >= self.maximum_resync_attempts:
                break
            await self._publish_snapshot(resync=True)
        raise BinanceOrderBookSyncFault(
            "resync_exhausted",
            f"unable to bridge Binance depth delta after {self.maximum_resync_attempts} snapshots",
        )

    async def _publish_snapshot(self, *, resync: bool) -> None:
        snapshot = await self.snapshot_provider.fetch()
        now = datetime.now(timezone.utc)
        event = canonical_from_trading_market_data(
            snapshot, source="binance", source_instance=self.source_instance,
            stream_id=self.stream_id, receive_time=now, published_time=now,
            source_sequence=snapshot.sequence,
        )[0]
        event = replace(event, capture_offset=f"rest-snapshot:{self._snapshots + 1}:{snapshot.sequence}")
        state = self.projection.apply(event)
        if state is None or not state.valid:
            raise BinanceOrderBookSyncFault("invalid_snapshot", "Binance depth snapshot produced invalid book")
        self._snapshots += 1
        if resync:
            self._resyncs += 1
        await self._publish(event)

    async def _publish(self, event: CanonicalEventEnvelope) -> None:
        if self.canonical_capture is not None:
            self.canonical_capture.append(event)
        await self.output.publish(event)
        self._last_published_available_time = event.available_time

    def _align_for_consumption(self, event: CanonicalEventEnvelope) -> CanonicalEventEnvelope:
        """Preserve event_time while making strategy availability reflect snapshot alignment."""
        now = datetime.now(timezone.utc)
        available = max(
            event.available_time,
            self._last_published_available_time or event.available_time,
        )
        return replace(
            event, available_time=available, published_time=max(event.published_time, now),
            flags=event.flags + (("snapshot_aligned",) if "snapshot_aligned" not in event.flags else ()),
        )

    @property
    def metrics(self) -> BinanceOrderBookSyncMetrics:
        return BinanceOrderBookSyncMetrics(
            self._snapshots, self._deltas_received, self._deltas_published,
            self._stale_deltas, self._gaps, self._resyncs,
        )
