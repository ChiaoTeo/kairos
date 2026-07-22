from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from kairospy.identity import InstrumentId


class MarketEventType(StrEnum):
    TRADE = "trade"
    QUOTE = "quote"
    BAR = "bar"
    OPTION_SNAPSHOT = "option_snapshot"
    INSTRUMENT_DEFINITION = "instrument_definition"
    TRADING_STATUS = "trading_status"
    STATISTICS = "statistics"


@dataclass(frozen=True, slots=True)
class MarketEventEnvelope:
    instrument_id: InstrumentId
    event_time: datetime
    available_time: datetime
    ingested_at: datetime
    source: str
    source_namespace: str
    source_instrument_id: str
    record_type: MarketEventType
    source_order: int
    payload: Mapping[str, object]
    receive_time: datetime | None = None
    publisher_id: str | None = None
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("event_time", "available_time", "ingested_at"):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.receive_time is not None and self.receive_time.tzinfo is None:
            raise ValueError("receive_time must be timezone-aware")
        if self.available_time < self.event_time:
            raise ValueError("available_time cannot precede event_time")
        if self.source_order < 0:
            raise ValueError("source_order cannot be negative")
        if not self.source.strip() or not self.source_namespace.strip() or not self.source_instrument_id.strip():
            raise ValueError("source identity cannot be empty")

    @property
    def event_key(self) -> tuple[datetime, int, str, str]:
        return self.available_time, self.source_order, self.source_namespace, self.source_instrument_id
