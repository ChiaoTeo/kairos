from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from kairospy.identity import VenueId
from kairospy.market.subscriptions import MarketDataCapabilities, MarketDataKind
from kairospy.market.types import Quote
from kairospy.reference.contracts import InstrumentDefinition, ProductType

from .symbol_mapper import CcxtSymbolMapper


CCXT_MARKET_DATA_CAPABILITIES = MarketDataCapabilities(
    frozenset({MarketDataKind.QUOTE, MarketDataKind.TRADE, MarketDataKind.BAR, MarketDataKind.ORDER_BOOK}),
    product_types=frozenset({ProductType.CRYPTO_SPOT, ProductType.PERPETUAL, ProductType.FUTURE}),
)


class CcxtMarketDataClient:
    def __init__(
        self,
        exchange: Any,
        *,
        provider: str,
        symbol_mapper: CcxtSymbolMapper | None = None,
        capabilities: MarketDataCapabilities = CCXT_MARKET_DATA_CAPABILITIES,
    ) -> None:
        self.exchange = exchange
        self.venue_id = VenueId(provider)
        self.symbol_mapper = symbol_mapper or CcxtSymbolMapper({})
        self.capabilities = capabilities

    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Quote, ...]:
        result = []
        now = datetime.now(timezone.utc)
        for definition in instruments:
            self.capabilities.require_product(definition.instrument_type)
            symbol = self.symbol_mapper.symbol_for(definition.instrument_id)
            row = self.exchange.fetch_ticker(symbol)
            result.append(Quote(
                definition.instrument_id,
                _decimal(row.get("bid")),
                _decimal(row.get("ask")),
                _decimal(row.get("bidVolume") or row.get("bidQty")),
                _decimal(row.get("askVolume") or row.get("askQty")),
                _timestamp(row.get("timestamp"), now),
            ))
        return tuple(result)


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value not in (None, "") else None


def _timestamp(value: Any, fallback: datetime) -> datetime:
    if value in (None, ""):
        return fallback
    return datetime.fromtimestamp(float(Decimal(str(value)) / Decimal("1000")), timezone.utc)
