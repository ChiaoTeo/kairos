from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kairospy.domain.market import (
    Bar,
    MarketEvent,
    MarketEventValue,
    MarketSubject,
    OptionGreeks,
    OrderBookDelta,
    OrderBookSnapshot,
    Quote,
    RateObservation,
    TradePrint,
)

from .sources import market_event_from_row, parse_event_time


class MarketMessage(Protocol):
    domain: object
    kind: object
    time: datetime
    sequence: int
    payload: object


class MarketIngestionService:
    def event_from_message(self, message: MarketMessage) -> MarketEvent | None:
        if str(message.domain) not in {"market", "data"}:
            return None
        payload = message.payload
        if isinstance(payload, MarketEvent):
            return payload
        if isinstance(payload, (Quote, OrderBookSnapshot, OrderBookDelta, Bar, TradePrint, RateObservation, OptionGreeks)):
            return self.event_from_value(
                payload,
                available_at=message.time,
                sequence=message.sequence,
            )
        if isinstance(payload, dict):
            return self.event_from_row(payload, sequence=message.sequence, stream=str(payload.get("source") or message.kind))
        return None

    def event_from_row(self, row: dict[str, object], *, sequence: int, stream: str) -> MarketEvent | None:
        return market_event_from_row(row, sequence=sequence, stream=stream)

    def event_from_value(
        self,
        value: MarketEventValue,
        *,
        available_at: datetime | None = None,
        source: str | None = None,
        sequence: int = 0,
    ) -> MarketEvent:
        subject = getattr(value, "subject", None)
        if not isinstance(subject, MarketSubject):
            subject = MarketSubject("instrument", getattr(value, "instrument_id", getattr(value, "rate_id", "unknown")))
        observed_at = getattr(value, "time", available_at)
        if observed_at is None:
            raise ValueError("market event requires an observed time")
        return MarketEvent(
            subject,
            observed_at,
            value,
            available_at=available_at,
            source=source if source is not None else str(getattr(value, "source", "") or ""),
            sequence=sequence,
        )

    def event_time(self, value: object) -> datetime:
        return parse_event_time(value)


__all__ = ["MarketMessage", "MarketIngestionService"]
