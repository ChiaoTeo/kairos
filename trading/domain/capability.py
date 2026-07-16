from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .product import ProductType


class MarketDataKind(StrEnum):
    QUOTE = "quote"
    TRADE = "trade"
    BAR = "bar"
    ORDER_BOOK = "order_book"
    INDEX_PRICE = "index_price"
    MARK_PRICE = "mark_price"
    FUNDING_RATE = "funding_rate"
    OPEN_INTEREST = "open_interest"
    GREEKS = "greeks"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class MarginMode(StrEnum):
    NONE = "none"
    SECURITIES = "securities"
    CROSS = "cross"
    ISOLATED = "isolated"


class PositionMode(StrEnum):
    ONE_WAY = "one_way"
    HEDGE = "hedge"


@dataclass(frozen=True, slots=True)
class ReferenceCapabilities:
    product_types: frozenset[ProductType]

    def require_product(self, product_type: ProductType) -> None:
        if product_type not in self.product_types:
            raise ValueError(f"adapter does not support product: {product_type}")


@dataclass(frozen=True, slots=True)
class MarketDataCapabilities:
    market_data: frozenset[MarketDataKind]
    product_types: frozenset[ProductType]
    supports_native_greeks: bool = False

    def require_market_data(self, kind: MarketDataKind) -> None:
        if kind not in self.market_data:
            raise ValueError(f"adapter does not support market data: {kind}")

    def require_product(self, product_type: ProductType) -> None:
        if product_type not in self.product_types:
            raise ValueError(f"adapter does not support product: {product_type}")


@dataclass(frozen=True, slots=True)
class ExecutionCapabilities:
    order_types: frozenset[OrderType]
    product_types: frozenset[ProductType]
    supports_combo_orders: bool = False
    supports_reduce_only: bool = False
    supports_post_only: bool = False
    margin_modes: frozenset[MarginMode] = frozenset({MarginMode.NONE})
    position_modes: frozenset[PositionMode] = frozenset({PositionMode.ONE_WAY})

    def require_order_type(self, order_type: OrderType) -> None:
        if order_type not in self.order_types:
            raise ValueError(f"adapter does not support order type: {order_type}")

    def require_product(self, product_type: ProductType) -> None:
        if product_type not in self.product_types:
            raise ValueError(f"adapter does not support product: {product_type}")
