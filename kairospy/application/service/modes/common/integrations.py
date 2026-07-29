from __future__ import annotations

from typing import TypeVar

from kairospy.infrastructure.integrations import (
    BinanceBroker,
    BinanceMarketDataConnector,
    CcxtDriver,
    HyperliquidMarketDataConnector,
    OkxBroker,
    OkxMarketDataConnector,
)
from kairospy.infrastructure.integrations.protocols import BrokerClient, LiveMarketDataFeed

ConfigErrorT = TypeVar("ConfigErrorT", bound=Exception)


def default_market_feed(venue: str, *, mode_label: str, error_type: type[ConfigErrorT]) -> LiveMarketDataFeed:
    normalized = venue.strip().lower()
    if normalized == "binance":
        return BinanceMarketDataConnector(CcxtDriver())
    if normalized == "hyperliquid":
        return HyperliquidMarketDataConnector(CcxtDriver())
    if normalized in {"okx", "okex"}:
        return OkxMarketDataConnector()
    raise error_type(f"unsupported {mode_label} market data venue: {venue}")


def default_broker(venue: str, credential: str | None, *, mode_label: str, error_type: type[ConfigErrorT]) -> BrokerClient:
    normalized = venue.strip().lower()
    if normalized == "binance":
        return BinanceBroker(CcxtDriver())
    if normalized in {"okx", "okex"}:
        return OkxBroker.from_credential(credential)
    raise error_type(f"unsupported {mode_label} broker venue: {venue}")


__all__ = ["default_broker", "default_market_feed"]
