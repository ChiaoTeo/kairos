from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from kairospy.core.reference import InstrumentId, MarketId

from .selectors import MarketSelectable


BookSide = Literal["bid", "ask"]


@dataclass(frozen=True, slots=True)
class PriceLevel:
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        if self.price < 0:
            raise ValueError("price level price cannot be negative")
        if self.size < 0:
            raise ValueError("price level size cannot be negative")


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot(MarketSelectable):
    instrument_id: InstrumentId | str
    time: datetime
    market_id: MarketId | str | None = None
    market_key: str | None = None
    bids: tuple[PriceLevel, ...] = ()
    asks: tuple[PriceLevel, ...] = ()
    nonce: object | None = None
    source: str = ""
    basis: str = "orderbook"
    derivation: str = "direct"

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        object.__setattr__(self, "market_id", None if self.market_id is None else _id(self.market_id, MarketId, "market_id"))
        if self.market_key is not None and not self.market_key.strip():
            raise ValueError("order book market_key cannot be blank")
        if self.time.tzinfo is None:
            raise ValueError("order book time must be timezone-aware")
        object.__setattr__(self, "bids", _sort_levels(self.bids, side="bid"))
        object.__setattr__(self, "asks", _sort_levels(self.asks, side="ask"))

    @property
    def bid1(self) -> PriceLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def ask1(self) -> PriceLevel | None:
        return self.asks[0] if self.asks else None

    @property
    def best_bid(self) -> PriceLevel | None:
        return self.bid1

    @property
    def best_ask(self) -> PriceLevel | None:
        return self.ask1


@dataclass(frozen=True, slots=True)
class OrderBookChange:
    side: BookSide
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        if self.side not in {"bid", "ask"}:
            raise ValueError("order book change side must be bid or ask")
        if self.price < 0:
            raise ValueError("order book change price cannot be negative")
        if self.size < 0:
            raise ValueError("order book change size cannot be negative")


@dataclass(frozen=True, slots=True)
class OrderBookDelta:
    instrument_id: InstrumentId | str
    time: datetime
    changes: tuple[OrderBookChange, ...]
    market_id: MarketId | str | None = None
    market_key: str | None = None
    nonce: object | None = None
    source: str = ""
    sequence: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _id(self.instrument_id, InstrumentId, "instrument_id"))
        object.__setattr__(self, "market_id", None if self.market_id is None else _id(self.market_id, MarketId, "market_id"))
        if self.time.tzinfo is None:
            raise ValueError("order book delta time must be timezone-aware")
        if self.sequence is not None and self.sequence < 1:
            raise ValueError("order book delta sequence must be positive")
        object.__setattr__(self, "changes", tuple(self.changes))


def apply_orderbook_update(snapshot: OrderBookSnapshot, delta: OrderBookDelta) -> OrderBookSnapshot:
    if snapshot.instrument_id != delta.instrument_id:
        raise ValueError("order book delta instrument_id does not match snapshot")
    if snapshot.market_id is not None and delta.market_id is not None and snapshot.market_id != delta.market_id:
        raise ValueError("order book delta market_id does not match snapshot")
    if _is_stale(snapshot.nonce, delta.nonce):
        raise ValueError("order book delta nonce is stale")
    bids = _apply_changes(snapshot.bids, (change for change in delta.changes if change.side == "bid"), side="bid")
    asks = _apply_changes(snapshot.asks, (change for change in delta.changes if change.side == "ask"), side="ask")
    return OrderBookSnapshot(
        instrument_id=snapshot.instrument_id,
        market_id=delta.market_id or snapshot.market_id,
        market_key=delta.market_key or snapshot.market_key,
        time=delta.time,
        bids=bids,
        asks=asks,
        nonce=delta.nonce if delta.nonce is not None else snapshot.nonce,
        source=delta.source or snapshot.source,
        basis=snapshot.basis,
        derivation=snapshot.derivation,
    )


def _apply_changes(
    levels: tuple[PriceLevel, ...],
    changes: object,
    *,
    side: BookSide,
) -> tuple[PriceLevel, ...]:
    by_price = {level.price: level.size for level in levels}
    for change in changes:
        if not isinstance(change, OrderBookChange):
            continue
        if change.size == 0:
            by_price.pop(change.price, None)
        else:
            by_price[change.price] = change.size
    return _sort_levels((PriceLevel(price, size) for price, size in by_price.items()), side=side)


def _sort_levels(levels: object, *, side: BookSide) -> tuple[PriceLevel, ...]:
    values = tuple(levels)
    return tuple(sorted(values, key=lambda level: level.price, reverse=side == "bid"))


def _is_stale(current: object | None, incoming: object | None) -> bool:
    if current is None or incoming is None:
        return False
    try:
        return int(incoming) < int(current)
    except Exception:
        return False


__all__ = [
    "BookSide",
    "OrderBookChange",
    "OrderBookDelta",
    "OrderBookSnapshot",
    "PriceLevel",
    "apply_orderbook_update",
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
