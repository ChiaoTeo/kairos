from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator

from kairospy.identity import InstrumentId, VenueId
from kairospy.market.types import OrderBookLevel, OrderBookSnapshot

from .symbol_mapper import CcxtSymbolMapper


DEFAULT_CCXT_PRO_ORDER_BOOK_SYMBOLS = {
    "binance": "BTC/USDT",
    "okex": "BTC/USDT",
    "okx": "BTC/USDT",
    "hyperliquid": "BTC/USDC:USDC",
}


@dataclass(frozen=True, slots=True)
class CcxtOrderBookSubscription:
    instrument_id: InstrumentId
    symbol: str
    depth: int | None = None


class CcxtOrderBookEventSource:
    """Async EventSource backed by ccxt.pro watch_order_book."""

    def __init__(
        self,
        exchange: Any,
        *,
        provider: str,
        subscriptions: tuple[CcxtOrderBookSubscription, ...],
        close_on_exit: bool = True,
        new_updates: bool | None = None,
    ) -> None:
        if not subscriptions:
            raise ValueError("CCXT order-book stream requires at least one subscription")
        self.exchange = exchange
        self.venue_id = VenueId(provider)
        self.subscriptions = subscriptions
        self.close_on_exit = close_on_exit
        if new_updates is not None:
            setattr(self.exchange, "newUpdates", new_updates)

    async def events(self) -> AsyncIterator[OrderBookSnapshot]:
        try:
            while True:
                for subscription in self.subscriptions:
                    row = await self.exchange.watch_order_book(subscription.symbol, subscription.depth)
                    yield parse_ccxt_order_book(row, subscription.instrument_id)
        finally:
            if self.close_on_exit:
                close = getattr(self.exchange, "close", None)
                if close is not None:
                    with suppress(Exception):
                        await close()

    @classmethod
    def for_instruments(
        cls,
        exchange: Any,
        *,
        provider: str,
        instrument_ids: tuple[InstrumentId, ...],
        symbol_mapper: CcxtSymbolMapper,
        depth: int | None = None,
        close_on_exit: bool = True,
        new_updates: bool | None = None,
    ) -> "CcxtOrderBookEventSource":
        return cls(
            exchange,
            provider=provider,
            subscriptions=tuple(
                CcxtOrderBookSubscription(instrument_id, symbol_mapper.symbol_for(instrument_id), depth)
                for instrument_id in instrument_ids
            ),
            close_on_exit=close_on_exit,
            new_updates=new_updates,
        )


async def watch_order_book_forever(
    exchange: Any,
    *,
    symbol: str,
    instrument_id: InstrumentId,
    depth: int | None = None,
) -> AsyncIterator[OrderBookSnapshot]:
    """Small helper mirroring the ccxt.pro watch loop while returning canonical snapshots."""
    while True:
        yield parse_ccxt_order_book(await exchange.watch_order_book(symbol, depth), instrument_id)


def parse_ccxt_order_book(row: dict[str, Any], instrument_id: InstrumentId) -> OrderBookSnapshot:
    bids = tuple(_level(item) for item in row.get("bids", ()))
    asks = tuple(_level(item) for item in row.get("asks", ()))
    return OrderBookSnapshot(
        instrument_id,
        bids,
        asks,
        _sequence(row),
        _timestamp(row.get("timestamp"), datetime.now(timezone.utc)),
    )


def _level(value: Any) -> OrderBookLevel:
    price, quantity = value[0], value[1]
    return OrderBookLevel(Decimal(str(price)), Decimal(str(quantity)))


def _sequence(row: dict[str, Any]) -> int:
    for key in ("nonce", "sequence", "lastUpdateId"):
        value = row.get(key)
        if value not in (None, ""):
            return int(value)
    return 0


def _timestamp(value: Any, fallback: datetime) -> datetime:
    if value in (None, ""):
        return fallback
    return datetime.fromtimestamp(float(Decimal(str(value)) / Decimal("1000")), timezone.utc)


__all__ = [
    "CcxtOrderBookEventSource",
    "CcxtOrderBookSubscription",
    "DEFAULT_CCXT_PRO_ORDER_BOOK_SYMBOLS",
    "parse_ccxt_order_book",
    "watch_order_book_forever",
]
