"""Binance REST market data snapshot client."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.trading.capability import MarketDataCapabilities, MarketDataKind
from kairospy.trading.identity import VenueId
from kairospy.trading.market_data import Quote
from kairospy.trading.product import ProductType
from kairospy.reference import InstrumentDefinition

from .rest_transport import BinanceTransport, RateLimiter


BINANCE_SPOT_MARKET_DATA_CAPABILITIES = MarketDataCapabilities(
    frozenset({MarketDataKind.QUOTE, MarketDataKind.TRADE, MarketDataKind.BAR, MarketDataKind.ORDER_BOOK}),
    product_types=frozenset({ProductType.CRYPTO_SPOT}),
)
BINANCE_FUTURES_MARKET_DATA_CAPABILITIES = MarketDataCapabilities(
    frozenset({
        MarketDataKind.QUOTE,
        MarketDataKind.TRADE,
        MarketDataKind.BAR,
        MarketDataKind.ORDER_BOOK,
        MarketDataKind.INDEX_PRICE,
        MarketDataKind.MARK_PRICE,
        MarketDataKind.FUNDING_RATE,
        MarketDataKind.OPEN_INTEREST,
    }),
    product_types=frozenset({ProductType.PERPETUAL, ProductType.FUTURE}),
)
BINANCE_OPTIONS_MARKET_DATA_CAPABILITIES = MarketDataCapabilities(
    frozenset({
        MarketDataKind.QUOTE,
        MarketDataKind.TRADE,
        MarketDataKind.ORDER_BOOK,
        MarketDataKind.GREEKS,
        MarketDataKind.INDEX_PRICE,
    }),
    product_types=frozenset({ProductType.CRYPTO_OPTION}),
    supports_native_greeks=True,
)

_BINANCE_MARKET_DATA_CAPABILITIES = {
    ProductType.CRYPTO_SPOT: BINANCE_SPOT_MARKET_DATA_CAPABILITIES,
    ProductType.PERPETUAL: BINANCE_FUTURES_MARKET_DATA_CAPABILITIES,
    ProductType.FUTURE: BINANCE_FUTURES_MARKET_DATA_CAPABILITIES,
    ProductType.CRYPTO_OPTION: BINANCE_OPTIONS_MARKET_DATA_CAPABILITIES,
}


class BinanceMarketDataClient:
    venue_id = VenueId("binance")

    def __init__(
        self,
        transport: BinanceTransport,
        product_type: ProductType = ProductType.CRYPTO_SPOT,
        path: str | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        try:
            self.capabilities = _BINANCE_MARKET_DATA_CAPABILITIES[product_type]
        except KeyError as error:
            raise ValueError(f"Binance market data does not support {product_type}") from error
        default_path = {
            ProductType.CRYPTO_SPOT: "/api/v3/ticker/bookTicker",
            ProductType.PERPETUAL: "/fapi/v1/ticker/bookTicker",
            ProductType.FUTURE: "/fapi/v1/ticker/bookTicker",
            ProductType.CRYPTO_OPTION: "/eapi/v1/ticker",
        }[product_type]
        self.transport, self.product_type, self.path = transport, product_type, path or default_path
        self.limiter = limiter or RateLimiter(1200, 60)

    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Quote, ...]:
        self.limiter.acquire()
        data = self.transport.request("GET", self.path)
        rows = data if isinstance(data, list) else [data]
        by_symbol = {item["symbol"]: item for item in rows}
        now = datetime.now(timezone.utc)
        result = []
        for definition in instruments:
            self.capabilities.require_product(definition.instrument_type)
            row = by_symbol[definition.display_name]
            result.append(Quote(
                definition.instrument_id,
                _decimal(row.get("bidPrice")),
                _decimal(row.get("askPrice")),
                _decimal(row.get("bidQty")),
                _decimal(row.get("askQty")),
                now,
            ))
        return tuple(result)


def _decimal(value):
    return Decimal(str(value)) if value not in (None, "") else None
