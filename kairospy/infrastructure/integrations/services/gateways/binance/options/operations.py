from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .client import BinanceOptionsRestClient


@dataclass(slots=True)
class BinanceOptionsMarketOperations:
    client: BinanceOptionsRestClient

    def exchange_info(self) -> object:
        return self.client.get("/eapi/v1/exchangeInfo")

    def ticker(self, *, symbol: str | None = None) -> object:
        return self.client.get("/eapi/v1/ticker", params={"symbol": symbol})


@dataclass(slots=True)
class BinanceOptionsAccountOperations:
    client: BinanceOptionsRestClient

    def account_snapshot(self) -> object:
        return self.client.get("/eapi/v1/account", signed=True)

    def open_orders(self, *, symbol: str | None = None) -> object:
        return self.client.get("/eapi/v1/openOrders", params={"underlying": symbol}, signed=True)


@dataclass(slots=True)
class BinanceOptionsOrderOperations:
    client: BinanceOptionsRestClient

    def submit(self, params: Mapping[str, Any]) -> object:
        return self.client.post("/eapi/v1/order", params=params, signed=True)

    def cancel(self, params: Mapping[str, Any]) -> object:
        return self.client.delete("/eapi/v1/order", params=params, signed=True)


__all__ = ["BinanceOptionsAccountOperations", "BinanceOptionsMarketOperations", "BinanceOptionsOrderOperations"]
