from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from kairospy.contracts import (
    BarPayload, CanonicalEventEnvelope, MarketEventKind, OrderBookDeltaPayload,
    OrderBookLevelPayload, OrderBookSnapshotPayload, QuotePayload,
)
from kairospy.trading.identity import InstrumentId
from kairospy.trading.market_data import Bar


@dataclass(slots=True)
class CanonicalBarSeriesProjection:
    """Deterministic bar projection shared by replay and live-style event sources."""

    bars: list[Bar] = field(default_factory=list)
    _last_key: tuple | None = None

    def apply(self, event: CanonicalEventEnvelope) -> Bar | None:
        if event.kind is not MarketEventKind.BAR:
            return None
        if not isinstance(event.payload, BarPayload):
            raise TypeError("canonical bar event requires BarPayload")
        if self._last_key is not None and event.event_key < self._last_key:
            raise ValueError("canonical bar events must be ordered")
        payload = event.payload
        bar = Bar(
            event.instrument_id, payload.period_start, payload.period_end,
            payload.open, payload.high, payload.low, payload.close, payload.volume,
        )
        if self.bars and bar.start == self.bars[-1].start and bar.instrument_id == self.bars[-1].instrument_id:
            if bar != self.bars[-1]:
                raise ValueError("conflicting canonical bar for the same instrument and period")
            return None
        self.bars.append(bar)
        self._last_key = event.event_key
        return bar


@dataclass(frozen=True, slots=True)
class QuoteState:
    instrument_id: InstrumentId
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    event_time: datetime
    available_time: datetime
    source_sequence: int | None
    version: int

    @property
    def midpoint(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / Decimal("2")

    def stale_at(self, now: datetime, maximum_age_seconds: int) -> bool:
        if now.tzinfo is None or maximum_age_seconds <= 0:
            raise ValueError("quote staleness requires aware time and positive maximum age")
        return (now - self.available_time).total_seconds() > maximum_age_seconds


class CanonicalQuoteProjection:
    """Read-only latest quote view suitable for both live and replay strategies."""

    def __init__(self) -> None:
        self._quotes: dict[InstrumentId, QuoteState] = {}
        self._version = 0

    def apply(self, event: CanonicalEventEnvelope) -> QuoteState | None:
        if event.kind is not MarketEventKind.QUOTE:
            return None
        if not isinstance(event.payload, QuotePayload):
            raise TypeError("canonical quote event requires QuotePayload")
        previous = self._quotes.get(event.instrument_id)
        if previous is not None:
            if event.available_time < previous.available_time:
                raise ValueError("quote projection rejects time regression")
            if (event.source_sequence is not None and previous.source_sequence is not None
                    and event.source_sequence < previous.source_sequence):
                raise ValueError("quote projection rejects source sequence regression")
        self._version += 1
        state = QuoteState(
            event.instrument_id, event.payload.bid, event.payload.ask,
            event.payload.bid_size, event.payload.ask_size, event.event_time,
            event.available_time, event.source_sequence, self._version,
        )
        self._quotes[event.instrument_id] = state
        return state

    def get(self, instrument_id: InstrumentId) -> QuoteState:
        try:
            return self._quotes[instrument_id]
        except KeyError as error:
            raise LookupError(f"quote state not available: {instrument_id}") from error

    @property
    def version(self) -> int:
        return self._version


@dataclass(frozen=True, slots=True)
class OrderBookGap:
    instrument_id: InstrumentId
    expected_sequence: int
    first_received_sequence: int
    last_received_sequence: int
    event_time: datetime
    message_id: str


@dataclass(frozen=True, slots=True)
class OrderBookState:
    instrument_id: InstrumentId
    bids: tuple[OrderBookLevelPayload, ...]
    asks: tuple[OrderBookLevelPayload, ...]
    sequence: int | None
    event_time: datetime
    available_time: datetime
    valid: bool
    invalid_reason: str | None
    version: int

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None


class CanonicalOrderBookProjection:
    """Deterministic snapshot/delta book that fails closed after any sequence gap."""

    def __init__(self) -> None:
        self._books: dict[InstrumentId, OrderBookState] = {}
        self._gaps: list[OrderBookGap] = []
        self._version = 0

    def apply(self, event: CanonicalEventEnvelope) -> OrderBookState | None:
        if event.kind is MarketEventKind.ORDER_BOOK_SNAPSHOT:
            if not isinstance(event.payload, OrderBookSnapshotPayload):
                raise TypeError("canonical order-book snapshot requires OrderBookSnapshotPayload")
            state = self._from_snapshot(event)
            self._books[event.instrument_id] = state
            return state
        if event.kind is not MarketEventKind.ORDER_BOOK_DELTA:
            return None
        if not isinstance(event.payload, OrderBookDeltaPayload):
            raise TypeError("canonical order-book delta requires OrderBookDeltaPayload")
        previous = self._books.get(event.instrument_id)
        if previous is None:
            return self._invalidate(event, None, "snapshot_required")
        if not previous.valid:
            return previous
        expected = (previous.sequence or 0) + 1
        payload = event.payload
        if payload.last_sequence < expected:
            return None
        if not payload.first_sequence <= expected <= payload.last_sequence:
            self._gaps.append(OrderBookGap(
                event.instrument_id, expected, payload.first_sequence, payload.last_sequence,
                event.event_time, str(event.message_id),
            ))
            return self._invalidate(
                event, previous,
                f"sequence_gap:expected={expected},received={payload.first_sequence}-{payload.last_sequence}",
            )
        bids = {level.price: level.quantity for level in previous.bids}
        asks = {level.price: level.quantity for level in previous.asks}
        self._apply_levels(bids, payload.bids)
        self._apply_levels(asks, payload.asks)
        ordered_bids = tuple(OrderBookLevelPayload(price, quantity) for price, quantity in sorted(bids.items(), reverse=True))
        ordered_asks = tuple(OrderBookLevelPayload(price, quantity) for price, quantity in sorted(asks.items()))
        if ordered_bids and ordered_asks and ordered_bids[0].price >= ordered_asks[0].price:
            return self._invalidate(event, previous, "crossed_book")
        self._version += 1
        state = OrderBookState(
            event.instrument_id, ordered_bids, ordered_asks, payload.last_sequence,
            event.event_time, event.available_time, True, None, self._version,
        )
        self._books[event.instrument_id] = state
        return state

    def get(self, instrument_id: InstrumentId) -> OrderBookState:
        try:
            return self._books[instrument_id]
        except KeyError as error:
            raise LookupError(f"order-book state not available: {instrument_id}") from error

    @property
    def gaps(self) -> tuple[OrderBookGap, ...]:
        return tuple(self._gaps)

    @property
    def version(self) -> int:
        return self._version

    def _from_snapshot(self, event: CanonicalEventEnvelope) -> OrderBookState:
        payload = event.payload
        assert isinstance(payload, OrderBookSnapshotPayload)
        bids = tuple(sorted(payload.bids, key=lambda item: item.price, reverse=True))
        asks = tuple(sorted(payload.asks, key=lambda item: item.price))
        valid = not (bids and asks and bids[0].price >= asks[0].price)
        self._version += 1
        return OrderBookState(
            event.instrument_id, bids if valid else (), asks if valid else (), payload.sequence,
            event.event_time, event.available_time, valid,
            None if valid else "crossed_snapshot", self._version,
        )

    def _invalidate(
        self,
        event: CanonicalEventEnvelope,
        previous: OrderBookState | None,
        reason: str,
    ) -> OrderBookState:
        self._version += 1
        state = OrderBookState(
            event.instrument_id, (), (), previous.sequence if previous else None,
            event.event_time, event.available_time, False, reason, self._version,
        )
        self._books[event.instrument_id] = state
        return state

    @staticmethod
    def _apply_levels(book: dict[Decimal, Decimal], updates: tuple[OrderBookLevelPayload, ...]) -> None:
        for level in updates:
            if level.price <= 0 or level.quantity < 0:
                raise ValueError("order-book delta has invalid price or quantity")
            if level.quantity == 0:
                book.pop(level.price, None)
            else:
                book[level.price] = level.quantity
