from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from typing import TypeAlias

from kairospy.infrastructure.persistence.market_data.records import OrderBookRecord, QuoteRecord, TradeRecord
from kairospy.core.order import VenueOrderResponse


IntegrationParams: TypeAlias = Mapping[str, object]
RawPayload: TypeAlias = Mapping[str, object]
RawPayloadRows: TypeAlias = Iterable[RawPayload]
RawPayloadStream: TypeAlias = AsyncIterator[RawPayload]
QuoteRecordStream: TypeAlias = AsyncIterator[QuoteRecord]
OrderBookRecordStream: TypeAlias = AsyncIterator[OrderBookRecord]
TradeRecordStream: TypeAlias = AsyncIterator[TradeRecord]
RawOrderResponse: TypeAlias = VenueOrderResponse
OrderSubmissionResponse: TypeAlias = VenueOrderResponse


__all__ = [
    "IntegrationParams",
    "OrderBookRecordStream",
    "OrderSubmissionResponse",
    "QuoteRecordStream",
    "RawOrderResponse",
    "RawPayload",
    "RawPayloadRows",
    "RawPayloadStream",
    "TradeRecordStream",
]
