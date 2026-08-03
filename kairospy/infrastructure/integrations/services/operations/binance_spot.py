from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from kairospy.infrastructure.integrations.services.clients.binance_spot import BinanceSpotRestClient


@dataclass(slots=True)
class BinanceSpotAccountOperations:
    """Account snapshot and account-query operations over a private client."""

    client: BinanceSpotRestClient

    def account_snapshot(self) -> object:
        return self.client.get("/api/v3/account", signed=True)

    def open_orders(self, *, symbol: str | None = None) -> object:
        return self.client.get("/api/v3/openOrders", params={"symbol": symbol}, signed=True)

    def closed_orders(self, *, symbol: str, limit: int | None = None, start_time: int | None = None, end_time: int | None = None) -> object:
        if not symbol.strip():
            raise ValueError("Binance all-orders query requires a symbol")
        return self.client.get(
            "/api/v3/allOrders",
            params={"symbol": symbol, "limit": limit, "startTime": start_time, "endTime": end_time},
            signed=True,
        )

    def my_trades(self, *, symbol: str, limit: int | None = None) -> object:
        if not symbol.strip():
            raise ValueError("Binance trade query requires a symbol")
        return self.client.get("/api/v3/myTrades", params={"symbol": symbol, "limit": limit}, signed=True)

    def create_listen_key(self) -> str:
        payload = self.client.post("/api/v3/userDataStream", signed=False)
        if not isinstance(payload, Mapping) or not isinstance(payload.get("listenKey"), str):
            raise ValueError("Binance user data stream response did not contain listenKey")
        return payload["listenKey"]

    def keepalive_listen_key(self, listen_key: str) -> None:
        if not listen_key.strip():
            raise ValueError("Binance listen key is required")
        self.client.post("/api/v3/userDataStream", params={"listenKey": listen_key}, signed=False)


@dataclass(slots=True)
class BinanceSpotMarketOperations:
    """Public Spot REST operations; payload conversion stays in the translator."""

    client: BinanceSpotRestClient

    def exchange_info(self) -> object:
        return self.client.get("/api/v3/exchangeInfo")

    def klines(self, *, symbol: str, interval: str = "1m", limit: int = 1000, start_time: int | None = None, end_time: int | None = None) -> object:
        if not symbol.strip():
            raise ValueError("Binance kline symbol is required")
        if not interval.strip():
            raise ValueError("Binance kline interval is required")
        if not 1 <= limit <= 1000:
            raise ValueError("Binance kline limit must be between 1 and 1000")
        return self.client.get(
            "/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit, "startTime": start_time, "endTime": end_time},
        )


@dataclass(slots=True)
class BinanceSpotOrderOperations:
    """Order submission, cancellation and order-query operations."""

    client: BinanceSpotRestClient

    def submit(self, params: Mapping[str, Any]) -> object:
        return self.client.post("/api/v3/order", params=params, signed=True)

    def cancel(self, params: Mapping[str, Any]) -> object:
        return self.client.delete("/api/v3/order", params=params, signed=True)

    def query(self, params: Mapping[str, Any]) -> object:
        return self.client.get("/api/v3/order", params=params, signed=True)


__all__ = ["BinanceSpotAccountOperations", "BinanceSpotMarketOperations", "BinanceSpotOrderOperations"]
