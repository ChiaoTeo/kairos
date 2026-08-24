from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.application.reference import InstrumentRef
from kairospy.primitives.reference import InstrumentId, MarketId


class AggressorSide(StrEnum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class ObservationScopeKind(StrEnum):
    MARKET = "market"
    CONSOLIDATED = "consolidated"


@dataclass(frozen=True, slots=True)
class ObservationScope:
    kind: ObservationScopeKind
    market_id: MarketId | None = None
    instrument_id: InstrumentId | None = None
    network_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ObservationScopeKind.MARKET:
            if self.market_id is None or self.instrument_id is not None:
                raise ValueError("market scope requires only market_id")
        elif self.instrument_id is None or self.market_id is not None:
            raise ValueError("consolidated scope requires only instrument_id")
        if self.network_id is not None and not self.network_id.strip():
            raise ValueError("scope network_id must be non-empty when present")

    @classmethod
    def market(cls, market_id: MarketId | str) -> ObservationScope:
        return cls(ObservationScopeKind.MARKET, market_id=MarketId(str(market_id)))

    @classmethod
    def consolidated(
        cls, instrument_id: InstrumentId | str, network_id: str | None = None
    ) -> ObservationScope:
        return cls(
            ObservationScopeKind.CONSOLIDATED,
            instrument_id=InstrumentId(str(instrument_id)),
            network_id=network_id,
        )

    def key(self) -> str:
        if self.market_id is not None:
            return str(self.market_id)
        return f"consolidated:{self.instrument_id}:{self.network_id or '*'}"


@dataclass(frozen=True, slots=True)
class Bar:
    scope: ObservationScope
    instrument: InstrumentRef
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    provider: str | None = None

    @property
    def market_id(self) -> MarketId | None:
        return self.scope.market_id

    def __post_init__(self) -> None:
        if not self.timeframe.strip():
            raise ValueError("bar timeframe is required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("bar occurred_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Quote:
    scope: ObservationScope
    instrument: InstrumentRef
    bid_price: Decimal | None
    bid_quantity: Decimal | None
    ask_price: Decimal | None
    ask_quantity: Decimal | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    provider: str | None = None
    bid_venue_code: str | None = None
    ask_venue_code: str | None = None
    tape: int | None = None

    @property
    def market_id(self) -> MarketId | None:
        return self.scope.market_id


@dataclass(frozen=True, slots=True)
class Trade:
    scope: ObservationScope
    instrument: InstrumentRef
    price: Decimal
    quantity: Decimal
    aggressor_side: AggressorSide | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    provider: str | None = None
    venue_code: str | None = None
    tape: int | None = None
    trf_id: int | None = None
    participant_timestamp_unix_nanos: int | None = None
    trf_timestamp_unix_nanos: int | None = None

    @property
    def market_id(self) -> MarketId | None:
        return self.scope.market_id


@dataclass(frozen=True, slots=True)
class OptionGreeks:
    scope: ObservationScope
    instrument: InstrumentRef
    expiry_unix_nanos: int
    strike: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    vega: Decimal | None
    theta: Decimal | None
    implied_volatility: Decimal | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    provider: str | None = None
    derivation: str | None = None

    @property
    def market_id(self) -> MarketId | None:
        return self.scope.market_id


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """One synchronous Market read model decoded from Market-owned storage."""

    view_key: str
    snapshot_id: str
    owner_actor_id: str
    generation: int
    quotes: tuple[Quote, ...] = ()
    trades: tuple[Trade, ...] = ()
    bars: tuple[Bar, ...] = ()
    greeks: tuple[OptionGreeks, ...] = ()

    def __post_init__(self) -> None:
        if not all(
            (
                self.view_key.strip(),
                self.snapshot_id.strip(),
                self.owner_actor_id.strip(),
            )
        ):
            raise ValueError("Market snapshot identity fields are required")
        if self.generation < 0:
            raise ValueError("Market snapshot generation cannot be negative")

    def latest_bar(self, market_id: MarketId, timeframe: str) -> Bar | None:
        return next(
            (
                value
                for value in self.bars
                if value.market_id == market_id and value.timeframe == timeframe
            ),
            None,
        )

    def latest_quote(self, market_id: MarketId) -> Quote | None:
        return next(
            (value for value in self.quotes if value.market_id == market_id), None
        )

    def latest_trade(self, market_id: MarketId) -> Trade | None:
        return next(
            (value for value in self.trades if value.market_id == market_id), None
        )

    def latest_greeks(self, market_id: MarketId) -> OptionGreeks | None:
        return next(
            (value for value in self.greeks if value.market_id == market_id), None
        )
