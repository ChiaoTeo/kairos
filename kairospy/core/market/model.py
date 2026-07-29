from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal, Mapping

from kairospy.core.reference import InstrumentId, MarketId

from .orderbook import OrderBookSnapshot, PriceLevel
from .selectors import MarketSelectable


MarketSubjectType = Literal["instrument", "market", "rate", "curve", "index"]
MarketObservationKind = Literal[
    "quote",
    "orderbook",
    "trade",
    "bar",
    "funding_rate",
    "interest_rate",
    "curve_point",
    "index_value",
]


@dataclass(frozen=True, slots=True)
class MarketSubject:
    subject_type: MarketSubjectType | str
    subject_id: InstrumentId | MarketId | str

    def __post_init__(self) -> None:
        subject_type = str(self.subject_type).strip()
        if not subject_type or not str(self.subject_id).strip():
            raise ValueError("market subject identity fields are required")
        object.__setattr__(self, "subject_type", subject_type)
        if subject_type == "instrument":
            object.__setattr__(self, "subject_id", _id(self.subject_id, InstrumentId, "subject_id"))
        elif subject_type == "market":
            object.__setattr__(self, "subject_id", _id(self.subject_id, MarketId, "subject_id"))


@dataclass(frozen=True, slots=True)
class MarketObservation:
    subject: MarketSubject
    kind: MarketObservationKind | str
    observed_at: datetime
    payload: Mapping[str, object]
    available_at: datetime | None = None
    source: str = ""
    sequence: int | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("market observation kind is required")
        if self.observed_at.tzinfo is None:
            raise ValueError("market observation observed_at must be timezone-aware")
        if self.available_at is not None and self.available_at.tzinfo is None:
            raise ValueError("market observation available_at must be timezone-aware")
        if self.sequence is not None and self.sequence < 1:
            raise ValueError("market observation sequence must be positive")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class Quote(MarketSelectable):
    instrument_id: InstrumentId | str
    time: datetime
    market_id: MarketId | str | None = None
    market_key: str | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    source: str = ""
    basis: str = "ticker"
    derivation: str = "direct"

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        object.__setattr__(self, "market_id", None if self.market_id is None else _id(self.market_id, MarketId, "market_id"))
        if self.market_key is not None and not self.market_key.strip():
            raise ValueError("quote market_key cannot be blank")
        if self.time.tzinfo is None:
            raise ValueError("quote time must be timezone-aware")
        if self.bid is not None and self.bid < 0:
            raise ValueError("quote bid cannot be negative")
        if self.ask is not None and self.ask < 0:
            raise ValueError("quote ask cannot be negative")
        if self.bid_size is not None and self.bid_size < 0:
            raise ValueError("quote bid_size cannot be negative")
        if self.ask_size is not None and self.ask_size < 0:
            raise ValueError("quote ask_size cannot be negative")
        if not self.basis.strip():
            raise ValueError("quote basis is required")
        if not self.derivation.strip():
            raise ValueError("quote derivation is required")

    @property
    def midpoint(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / Decimal("2")


@dataclass(frozen=True, slots=True)
class Bar(MarketSelectable):
    instrument_id: InstrumentId | str
    time: datetime
    timeframe: str
    market_id: MarketId | str | None = None
    market_key: str | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    source: str = ""
    basis: str = "bar"
    derivation: str = "direct"

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        object.__setattr__(self, "market_id", None if self.market_id is None else _id(self.market_id, MarketId, "market_id"))
        if not self.timeframe.strip():
            raise ValueError("bar timeframe is required")
        if self.time.tzinfo is None:
            raise ValueError("bar time must be timezone-aware")
        if not self.basis.strip():
            raise ValueError("bar basis is required")
        if not self.derivation.strip():
            raise ValueError("bar derivation is required")


@dataclass(frozen=True, slots=True)
class TradePrint(MarketSelectable):
    instrument_id: InstrumentId | str
    time: datetime
    market_id: MarketId | str | None = None
    market_key: str | None = None
    trade_id: str | None = None
    side: str | None = None
    price: Decimal | None = None
    size: Decimal | None = None
    cost: Decimal | None = None
    source: str = ""
    basis: str = "trade"
    derivation: str = "direct"

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        object.__setattr__(self, "market_id", None if self.market_id is None else _id(self.market_id, MarketId, "market_id"))
        if self.time.tzinfo is None:
            raise ValueError("trade time must be timezone-aware")
        if not self.basis.strip():
            raise ValueError("trade basis is required")
        if not self.derivation.strip():
            raise ValueError("trade derivation is required")


@dataclass(frozen=True, slots=True)
class RateObservation(MarketSelectable):
    rate_id: str
    time: datetime
    rate: Decimal
    source: str = ""
    tenor: str | None = None
    basis: str = ""
    market_id: MarketId | str | None = None
    derivation: str = "direct"

    def __post_init__(self) -> None:
        if not self.rate_id.strip():
            raise ValueError("rate_id is required")
        object.__setattr__(self, "market_id", None if self.market_id is None else _id(self.market_id, MarketId, "market_id"))
        if self.time.tzinfo is None:
            raise ValueError("rate observation time must be timezone-aware")
        if not self.derivation.strip():
            raise ValueError("rate observation derivation is required")

    @property
    def subject(self) -> MarketSubject:
        if self.market_id:
            return MarketSubject("market", self.market_id)
        return MarketSubject("rate", self.rate_id)

    def to_observation(self, *, kind: str = "interest_rate", sequence: int | None = None) -> MarketObservation:
        payload: dict[str, Any] = {
            "rate_id": self.rate_id,
            "rate": self.rate,
            "tenor": self.tenor,
            "basis": self.basis,
            "market_id": None if self.market_id is None else str(self.market_id),
        }
        return MarketObservation(
            self.subject,
            kind,
            self.time,
            payload,
            available_at=self.time,
            source=self.source,
            sequence=sequence,
        )


__all__ = [
    "Bar",
    "MarketObservation",
    "MarketObservationKind",
    "MarketSubject",
    "MarketSubjectType",
    "OrderBookSnapshot",
    "PriceLevel",
    "Quote",
    "RateObservation",
    "TradePrint",
]


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} cannot be empty")
    return text


def _id(value, id_type, label: str):
    if isinstance(value, id_type):
        return value
    return id_type(_required_text(value, label))
