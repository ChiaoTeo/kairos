"""Market-owned classified event contract."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from kairospy.infrastructure.protocol.eventing import EventMetadataRead

if TYPE_CHECKING:
    from kairospy._native_market_contract import (
        MarketBarCurrent,
        MarketEvent,
        MarketFundingRateCurrent,
        MarketGreeksCurrent,
        MarketIndexPriceCurrent,
        MarketMarkPriceCurrent,
        MarketOpenInterestCurrent,
        MarketObservationScope,
        MarketOrderBookCurrent,
        MarketOrderBookDeltaEventPayload,
        MarketOrderBookResyncEventPayload,
        MarketQuoteCurrent,
        MarketRateCurrent,
        MarketTicker24hCurrent,
        MarketTradeEventPayload,
    )


class _MarketEventBase(Protocol):
    metadata: EventMetadataRead


class MarketQuoteUpdatedEvent(_MarketEventBase, Protocol):
    kind: Literal["quote_updated"]
    data: MarketQuoteCurrent


class MarketTradeOccurredEvent(_MarketEventBase, Protocol):
    kind: Literal["trade_occurred"]
    data: MarketTradeEventPayload


class MarketBarCompletedEvent(_MarketEventBase, Protocol):
    kind: Literal["bar_completed"]
    data: MarketBarCurrent


class MarketGreeksUpdatedEvent(_MarketEventBase, Protocol):
    kind: Literal["greeks_updated"]
    data: MarketGreeksCurrent


class MarketRateUpdatedEvent(_MarketEventBase, Protocol):
    kind: Literal["rate_updated"]
    data: MarketRateCurrent


class MarketTicker24hUpdatedEvent(_MarketEventBase, Protocol):
    kind: Literal["ticker_24h_updated"]
    data: MarketTicker24hCurrent


class MarketMarkPriceUpdatedEvent(_MarketEventBase, Protocol):
    kind: Literal["mark_price_updated"]
    data: MarketMarkPriceCurrent


class MarketFundingRateUpdatedEvent(_MarketEventBase, Protocol):
    kind: Literal["funding_rate_updated"]
    data: MarketFundingRateCurrent


class MarketOpenInterestUpdatedEvent(_MarketEventBase, Protocol):
    kind: Literal["open_interest_updated"]
    data: MarketOpenInterestCurrent


class MarketIndexPriceUpdatedEvent(_MarketEventBase, Protocol):
    kind: Literal["index_price_updated"]
    data: MarketIndexPriceCurrent


class MarketOrderBookSnapshotReceivedEvent(_MarketEventBase, Protocol):
    kind: Literal["order_book_snapshot_received"]
    data: MarketOrderBookCurrent


class MarketOrderBookDeltaReceivedEvent(_MarketEventBase, Protocol):
    kind: Literal["order_book_delta_received"]
    data: MarketOrderBookDeltaEventPayload


class MarketOrderBookResyncRequiredEvent(_MarketEventBase, Protocol):
    kind: Literal["order_book_resync_required"]
    data: MarketOrderBookResyncEventPayload


MarketEventVariant: TypeAlias = (
    MarketQuoteUpdatedEvent
    | MarketTradeOccurredEvent
    | MarketBarCompletedEvent
    | MarketGreeksUpdatedEvent
    | MarketRateUpdatedEvent
    | MarketTicker24hUpdatedEvent
    | MarketMarkPriceUpdatedEvent
    | MarketFundingRateUpdatedEvent
    | MarketOpenInterestUpdatedEvent
    | MarketIndexPriceUpdatedEvent
    | MarketOrderBookSnapshotReceivedEvent
    | MarketOrderBookDeltaReceivedEvent
    | MarketOrderBookResyncRequiredEvent
)


def decode_event(frame: bytes) -> MarketEventVariant:
    return cast(
        MarketEventVariant,
        import_module("kairospy._native_market_contract").decode_event(frame),
    )


def decode_events(frames: Sequence[bytes]) -> list[MarketEventVariant]:
    return cast(
        list[MarketEventVariant],
        import_module("kairospy._native_market_contract").decode_events(frames),
    )


if not TYPE_CHECKING:
    _module = import_module("kairospy._native_market_contract")
    MarketBarCurrent = _module.MarketBarCurrent
    MarketEvent = _module.MarketEvent
    MarketFundingRateCurrent = _module.MarketFundingRateCurrent
    MarketGreeksCurrent = _module.MarketGreeksCurrent
    MarketIndexPriceCurrent = _module.MarketIndexPriceCurrent
    MarketMarkPriceCurrent = _module.MarketMarkPriceCurrent
    MarketObservationScope = _module.MarketObservationScope
    MarketOpenInterestCurrent = _module.MarketOpenInterestCurrent
    MarketOrderBookCurrent = _module.MarketOrderBookCurrent
    MarketOrderBookDeltaEventPayload = _module.MarketOrderBookDeltaEventPayload
    MarketOrderBookResyncEventPayload = _module.MarketOrderBookResyncEventPayload
    MarketQuoteCurrent = _module.MarketQuoteCurrent
    MarketRateCurrent = _module.MarketRateCurrent
    MarketTicker24hCurrent = _module.MarketTicker24hCurrent
    MarketTradeEventPayload = _module.MarketTradeEventPayload


__all__ = [
    "MarketBarCompletedEvent",
    "MarketBarCurrent",
    "MarketEvent",
    "MarketEventVariant",
    "MarketFundingRateUpdatedEvent",
    "MarketFundingRateCurrent",
    "MarketGreeksUpdatedEvent",
    "MarketGreeksCurrent",
    "MarketIndexPriceUpdatedEvent",
    "MarketIndexPriceCurrent",
    "MarketMarkPriceUpdatedEvent",
    "MarketMarkPriceCurrent",
    "MarketObservationScope",
    "MarketOpenInterestUpdatedEvent",
    "MarketOpenInterestCurrent",
    "MarketOrderBookDeltaReceivedEvent",
    "MarketOrderBookDeltaEventPayload",
    "MarketOrderBookResyncRequiredEvent",
    "MarketOrderBookResyncEventPayload",
    "MarketOrderBookSnapshotReceivedEvent",
    "MarketOrderBookCurrent",
    "MarketQuoteUpdatedEvent",
    "MarketQuoteCurrent",
    "MarketRateUpdatedEvent",
    "MarketRateCurrent",
    "MarketTicker24hUpdatedEvent",
    "MarketTicker24hCurrent",
    "MarketTradeOccurredEvent",
    "MarketTradeEventPayload",
    "decode_event",
    "decode_events",
]
