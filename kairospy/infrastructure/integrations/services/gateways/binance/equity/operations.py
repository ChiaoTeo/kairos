from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from .client import BinanceEquityRestClient


@dataclass(slots=True)
class BinanceEquityMarketOperations:
    client: BinanceEquityRestClient

    def exchange_info(self, *, symbol: str | None = None) -> object:
        return self.client.get("/sapi/v1/equity/market/exchangeInfo", params={"symbol": symbol})

    def latest_quote(self, *, symbol: str) -> object:
        normalized = symbol.strip()
        if not normalized:
            raise ValueError("Binance Stocks Trading quote symbol is required")
        return self.client.get("/sapi/v1/equity/market/quote", params={"symbol": normalized.upper()})


@dataclass(slots=True)
class BinanceEquityOrderOperations:
    """Signed Stocks Trading order operations."""

    client: BinanceEquityRestClient

    def submit(self, params: Mapping[str, Any]) -> object:
        return self.client.post("/sapi/v1/equity/order/place", params=params, signed=True)

    def cancel(self, params: Mapping[str, Any]) -> object:
        return self.client.post("/sapi/v1/equity/order/cancel", params=params, signed=True)

    def open_orders(self) -> object:
        return self.client.get("/sapi/v1/equity/order/open-orders", signed=True)

    def history(self, params: Mapping[str, Any]) -> object:
        return self.client.get("/sapi/v1/equity/order/history", params=params, signed=True)

    def detail(self, params: Mapping[str, Any]) -> object:
        return self.client.get("/sapi/v1/equity/order/detail", params=params, signed=True)


__all__ = ["BinanceEquityMarketOperations", "BinanceEquityOrderOperations"]
